"""Auditable tariff studies built from historical Avantio imports and a market provider."""

from __future__ import annotations

import asyncio
import csv
import io
import statistics
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Protocol
from urllib.parse import urlsplit

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    AvailabilitySlot,
    ImportBatch,
    PropertySnapshot,
    ReservationSnapshot,
    TariffGroupAssignment,
    TariffHoliday,
    TariffSeason,
    TariffStudy,
)

ALGORITHM_VERSION = "1.0"
PRICE_CHANGE_LIMIT = Decimal("0.20")
MIN_COMPETITORS = 3
HISTORY_YEARS = 3
BOOKABLE_STATUSES = frozenset({"confirmed", "paid"})
NON_SELLABLE_STATUSES = frozenset({"owner", "unavailable"})


class TariffLabError(ValueError):
    """An input or source condition prevents a tariff study from running."""


class MarketProviderUnavailable(TariffLabError):
    """No approved rate provider has been registered for this deployment."""


@dataclass(frozen=True)
class MarketSearchRequest:
    tariff_name: str
    municipality: str
    capacity: int
    start_date: date
    end_date: date
    guests: int
    nights: int
    currency: str = "EUR"


@dataclass(frozen=True)
class MarketObservation:
    stay_date: date
    nightly_rate: Decimal
    currency: str
    source: str
    comparable_name: str
    source_url: str | None
    captured_at: datetime
    guests: int
    nights: int
    includes_mandatory_fees: bool
    cancellation_policy: str | None = None


class MarketDataProvider(Protocol):
    """Adapter contract for a vendor API or an otherwise authorized data source."""

    @property
    def name(self) -> str: ...

    async def search(self, request: MarketSearchRequest) -> list[MarketObservation]: ...


_market_provider: MarketDataProvider | None = None


def register_market_provider(provider: MarketDataProvider | None) -> None:
    """Register a deployment-specific adapter; credentials stay outside the database."""
    global _market_provider
    _market_provider = provider


def market_provider() -> MarketDataProvider | None:
    return _market_provider


def _fold(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", (value or "").casefold())
    return "".join(
        character for character in normalized if not unicodedata.combining(character)
    ).strip()


def _latest_valid_batch(db: Session, dataset: str) -> ImportBatch | None:
    return db.scalar(
        select(ImportBatch)
        .where(ImportBatch.dataset == dataset, ImportBatch.status == "valid")
        .order_by(desc(ImportBatch.id))
    )


def _current_properties(db: Session, batch_id: int | None = None) -> list[PropertySnapshot]:
    batch = (
        db.get(ImportBatch, batch_id)
        if batch_id is not None
        else _latest_valid_batch(db, "properties")
    )
    if not batch:
        return []
    return list(
        db.scalars(
            select(PropertySnapshot)
            .where(PropertySnapshot.batch_id == batch.id, PropertySnapshot.active.is_(True))
            .order_by(PropertySnapshot.name)
        )
    )


def _effective_reservations(
    db: Session, through_batch_id: int | None = None
) -> list[ReservationSnapshot]:
    if through_batch_id is not None and through_batch_id <= 0:
        return []
    batch_query = select(ImportBatch).where(
        ImportBatch.dataset == "reservations", ImportBatch.status == "valid"
    )
    if through_batch_id is not None:
        batch_query = batch_query.where(ImportBatch.id <= through_batch_id)
    latest = db.scalar(batch_query.order_by(desc(ImportBatch.id)))
    if not latest:
        return []
    effective: dict[str, ReservationSnapshot] = {}
    rows = db.scalars(
        select(ReservationSnapshot)
        .join(ImportBatch, ReservationSnapshot.batch_id == ImportBatch.id)
        .where(
            ImportBatch.dataset == "reservations",
            ImportBatch.status == "valid",
            ImportBatch.id <= latest.id,
        )
        .order_by(ImportBatch.id, ReservationSnapshot.id)
    )
    for row in rows:
        effective[row.locator] = row
    return list(effective.values())


def _property_mappings(db: Session) -> dict[str, TariffGroupAssignment]:
    return {row.property_id: row for row in db.scalars(select(TariffGroupAssignment)).all()}


def tariff_readiness(db: Session) -> dict:
    """Summarize source coverage without making any external requests."""
    groups = sorted(
        set(
            db.scalars(select(TariffSeason.tariff_name).where(TariffSeason.rate_plan == "SA")).all()
        )
    )
    properties = _current_properties(db)
    assignments = _property_mappings(db)
    unmapped = [
        {"id": item.property_id, "name": item.name, "city": item.city}
        for item in properties
        if item.property_id not in assignments
        or assignments[item.property_id].tariff_name not in groups
    ]
    mapped_groups = {
        assignments[item.property_id].tariff_name
        for item in properties
        if item.property_id in assignments
    }
    unmapped_groups = sorted(set(groups) - mapped_groups)
    reservations = _effective_reservations(db)
    reservation_dates = [row.start_date for row in reservations] + [
        row.end_date - timedelta(days=1) for row in reservations
    ]
    slots = db.scalars(
        select(AvailabilitySlot)
        .join(ImportBatch, AvailabilitySlot.batch_id == ImportBatch.id)
        .where(ImportBatch.dataset == "availability", ImportBatch.status == "valid")
    ).all()
    slot_dates = [row.start_date for row in slots] + [
        row.end_date - timedelta(days=1) for row in slots
    ]
    holidays = db.scalars(select(TariffHoliday)).all()
    provider = market_provider()
    checks = [
        {"key": "properties", "ready": bool(properties), "count": len(properties)},
        {
            "key": "group_mapping",
            "ready": bool(properties) and not unmapped and not unmapped_groups,
            "unmapped": unmapped,
            "unmapped_groups": unmapped_groups,
        },
        {"key": "reservations", "ready": bool(reservations), "count": len(reservations)},
        {"key": "availability_history", "ready": bool(slots), "count": len(slots)},
        {
            "key": "market_provider",
            "ready": provider is not None,
            "name": provider.name if provider else None,
        },
        {"key": "holiday_calendar", "ready": bool(holidays), "count": len(holidays)},
    ]
    return {
        "can_generate": all(
            item["ready"]
            for item in checks
            if item["key"]
            in {
                "properties",
                "group_mapping",
                "reservations",
                "market_provider",
            }
        ),
        "groups": groups,
        "active_properties": len(properties),
        "unmapped_properties": unmapped,
        "unmapped_groups": unmapped_groups,
        "reservation_coverage": _coverage(reservation_dates),
        "availability_coverage": _coverage(slot_dates),
        "checks": checks,
        "algorithm_version": ALGORITHM_VERSION,
        "price_change_limit_percent": int(PRICE_CHANGE_LIMIT * 100),
    }


def _coverage(values: list[date]) -> dict | None:
    if not values:
        return None
    return {"from": min(values).isoformat(), "to": max(values).isoformat()}


def list_property_mappings(db: Session) -> list[dict]:
    assignments = _property_mappings(db)
    return [
        {
            "property_id": item.property_id,
            "name": item.name,
            "city": item.city,
            "capacity": item.capacity,
            "tariff_name": assignments[item.property_id].tariff_name
            if item.property_id in assignments
            else "",
            "municipality": assignments[item.property_id].municipality
            if item.property_id in assignments
            else item.city or "",
            "province": assignments[item.property_id].province
            if item.property_id in assignments
            else "",
            "autonomous_community": assignments[item.property_id].autonomous_community
            if item.property_id in assignments
            else "",
        }
        for item in _current_properties(db)
    ]


def save_property_mappings(db: Session, mappings: list[dict], actor_user_id: int) -> list[dict]:
    properties = {item.property_id: item for item in _current_properties(db)}
    groups = set(
        db.scalars(select(TariffSeason.tariff_name).where(TariffSeason.rate_plan == "SA")).all()
    )
    if not properties:
        raise TariffLabError("Importa primero el catálogo de alojamientos")
    seen: set[str] = set()
    for item in mappings:
        property_id = str(item.get("property_id", "")).strip()
        tariff_name = str(item.get("tariff_name", "")).strip()
        municipality = str(item.get("municipality", "")).strip()
        if property_id in seen:
            raise TariffLabError("Hay alojamientos repetidos en la asignación")
        if property_id not in properties:
            raise TariffLabError(f"El alojamiento {property_id} no está en el catálogo activo")
        if tariff_name not in groups:
            raise TariffLabError(f"El grupo SA {tariff_name} no existe")
        if not municipality:
            raise TariffLabError(f"Falta el municipio del alojamiento {property_id}")
        seen.add(property_id)
        assignment = db.get(TariffGroupAssignment, property_id)
        if assignment is None:
            assignment = TariffGroupAssignment(
                property_id=property_id, tariff_name=tariff_name, municipality=municipality
            )
            db.add(assignment)
        assignment.tariff_name = tariff_name
        assignment.municipality = municipality[:150]
        assignment.province = str(item.get("province") or "").strip()[:150] or None
        assignment.autonomous_community = (
            str(item.get("autonomous_community") or "").strip()[:150] or None
        )
        assignment.updated_by_id = actor_user_id
    for property_id in properties.keys() - seen:
        assignment = db.get(TariffGroupAssignment, property_id)
        if assignment is not None:
            db.delete(assignment)
    db.commit()
    return list_property_mappings(db)


def holiday_view(db: Session) -> list[dict]:
    rows = db.scalars(
        select(TariffHoliday).order_by(TariffHoliday.holiday_date, TariffHoliday.name)
    ).all()
    return [
        {
            "id": row.id,
            "date": row.holiday_date.isoformat(),
            "name": row.name,
            "scope": row.scope,
            "region": row.region,
            "municipality": row.municipality,
            "source": row.source,
        }
        for row in rows
    ]


def add_holiday(db: Session, data: dict, actor_user_id: int) -> dict:
    scope = str(data.get("scope", ""))
    name = str(data.get("name", "")).strip()
    region = str(data.get("region") or "").strip() or None
    municipality = str(data.get("municipality") or "").strip() or None
    if scope not in {"national", "regional", "municipal"}:
        raise TariffLabError("El ámbito debe ser nacional, regional o municipal")
    if not name or len(name) > 180:
        raise TariffLabError("Indica un nombre de festivo de hasta 180 caracteres")
    if scope == "regional" and not region:
        raise TariffLabError("Indica la comunidad autónoma del festivo")
    if scope == "municipal" and not municipality:
        raise TariffLabError("Indica el municipio del festivo")
    holiday_date = data.get("holiday_date")
    if isinstance(holiday_date, str):
        holiday_date = date.fromisoformat(holiday_date)
    duplicate = db.scalar(
        select(TariffHoliday.id).where(
            TariffHoliday.holiday_date == holiday_date,
            TariffHoliday.name == name,
            TariffHoliday.scope == scope,
            TariffHoliday.region.is_(None) if region is None else TariffHoliday.region == region,
            TariffHoliday.municipality.is_(None)
            if municipality is None
            else TariffHoliday.municipality == municipality,
        )
    )
    if duplicate:
        raise TariffLabError("Ese festivo ya está registrado")
    row = TariffHoliday(
        holiday_date=holiday_date,
        name=name,
        scope=scope,
        region=region,
        municipality=municipality,
        source=str(data.get("source") or "manual")[:255],
        created_by_id=actor_user_id,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise TariffLabError("Ese festivo ya está registrado") from None
    db.refresh(row)
    return {
        "id": row.id,
        "date": row.holiday_date.isoformat(),
        "name": row.name,
        "scope": row.scope,
        "region": row.region,
        "municipality": row.municipality,
        "source": row.source,
    }


def remove_holiday(db: Session, holiday_id: int) -> bool:
    row = db.get(TariffHoliday, holiday_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def validate_study_request(
    *,
    name: str,
    start: date,
    end: date,
    guests: int,
    nights: int,
    groups: list[str],
    today: date | None = None,
) -> None:
    today = today or date.today()
    if not name.strip() or len(name.strip()) > 120:
        raise TariffLabError("Indica un nombre de estudio de hasta 120 caracteres")
    if start < today:
        raise TariffLabError("La fecha de inicio debe ser hoy o posterior")
    if end < start or (end - start).days > 400:
        raise TariffLabError("El periodo debe ser inclusivo y no superar 401 días")
    if not 1 <= guests <= 20:
        raise TariffLabError("El número de huéspedes debe estar entre 1 y 20")
    if not 1 <= nights <= 90:
        raise TariffLabError("La duración de estancia debe estar entre 1 y 90 noches")
    if not groups or len(set(groups)) != len(groups):
        raise TariffLabError("Selecciona uno o más grupos SA sin duplicados")


def create_study(db: Session, data: dict, actor_user_id: int) -> TariffStudy:
    provider = market_provider()
    if provider is None:
        raise MarketProviderUnavailable(
            "Falta configurar un proveedor/API autorizado de tarifas de mercado"
        )
    start = data["period_start"]
    end = data["period_end"]
    groups = [str(value).strip() for value in data["tariff_names"]]
    validate_study_request(
        name=data["name"],
        start=start,
        end=end,
        guests=data["guests"],
        nights=data["nights"],
        groups=groups,
    )
    known_groups = set(
        db.scalars(select(TariffSeason.tariff_name).where(TariffSeason.rate_plan == "SA")).all()
    )
    unknown = sorted(set(groups) - known_groups)
    if unknown:
        raise TariffLabError("Grupos SA no reconocidos: " + ", ".join(unknown))
    unmapped = tariff_readiness(db)["unmapped_properties"]
    if unmapped:
        raise TariffLabError("Completa primero la asignación de alojamientos a grupos SA")
    assignments = _property_mappings(db)
    missing_groups = [
        group
        for group in groups
        if not any(item.tariff_name == group for item in assignments.values())
    ]
    if missing_groups:
        raise TariffLabError("Asigna al menos un alojamiento a: " + ", ".join(missing_groups))
    property_batch = _latest_valid_batch(db, "properties")
    reservation_batch = _latest_valid_batch(db, "reservations")
    availability_batch = _latest_valid_batch(db, "availability")
    snapshot_assignments = [
        {
            "property_id": item.property_id,
            "tariff_name": item.tariff_name,
            "municipality": item.municipality,
            "province": item.province,
            "autonomous_community": item.autonomous_community,
        }
        for item in assignments.values()
        if item.tariff_name in groups
    ]
    snapshot_rates = [
        {
            "tariff_name": item.tariff_name,
            "rate_plan": item.rate_plan,
            "start_date": item.start_date.isoformat(),
            "end_date": item.end_date.isoformat(),
            "nightly_rate": str(item.nightly_rate),
        }
        for item in db.scalars(
            select(TariffSeason).where(
                TariffSeason.rate_plan == "SA", TariffSeason.tariff_name.in_(groups)
            )
        )
    ]
    snapshot_holidays = [
        {
            "holiday_date": item.holiday_date.isoformat(),
            "name": item.name,
            "scope": item.scope,
            "region": item.region,
            "municipality": item.municipality,
            "source": item.source,
        }
        for item in db.scalars(
            select(TariffHoliday).where(
                TariffHoliday.holiday_date >= date(start.year - HISTORY_YEARS, 1, 1),
                TariffHoliday.holiday_date <= end,
            )
        )
    ]
    study = TariffStudy(
        name=data["name"].strip(),
        period_start=start,
        period_end=end,
        guests=data["guests"],
        nights=data["nights"],
        tariff_names=groups,
        parameters={
            "target_occupancy": data.get("target_occupancy"),
            "price_change_limit_percent": int(PRICE_CHANGE_LIMIT * 100),
            "input_snapshot": {
                "captured_at": datetime.now(UTC).isoformat(),
                "provider": provider.name,
                "source_batches": {
                    "properties": property_batch.id if property_batch else 0,
                    "reservations_through": reservation_batch.id if reservation_batch else 0,
                    "availability_through": availability_batch.id if availability_batch else 0,
                },
                "assignments": snapshot_assignments,
                "tariff_seasons": snapshot_rates,
                "holidays": snapshot_holidays,
            },
        },
        status="pending",
        algorithm_version=ALGORITHM_VERSION,
        created_by_id=actor_user_id,
    )
    db.add(study)
    db.flush()
    from app.ingest import enqueue

    enqueue(
        db,
        key=f"tariff-study:{study.id}",
        kind="tariff_study",
        payload={"study_id": study.id},
    )
    db.commit()
    db.refresh(study)
    return study


def study_view(study: TariffStudy, include_result: bool = True) -> dict:
    result = {
        "id": study.id,
        "name": study.name,
        "period_start": study.period_start.isoformat(),
        "period_end": study.period_end.isoformat(),
        "guests": study.guests,
        "nights": study.nights,
        "tariff_names": study.tariff_names or [],
        "parameters": {
            key: value for key, value in (study.parameters or {}).items() if key != "input_snapshot"
        },
        "status": study.status,
        "algorithm_version": study.algorithm_version,
        "error": study.error,
        "created_at": study.created_at.isoformat() if study.created_at else None,
        "started_at": study.started_at.isoformat() if study.started_at else None,
        "completed_at": study.completed_at.isoformat() if study.completed_at else None,
    }
    if include_result:
        result["result"] = study.result
    return result


def list_studies(db: Session) -> list[dict]:
    rows = db.scalars(select(TariffStudy).order_by(desc(TariffStudy.id)).limit(100)).all()
    return [study_view(row, include_result=False) for row in rows]


def _holiday_matches(
    day: date,
    assignments: list[TariffGroupAssignment],
    holidays: list[TariffHoliday],
):
    matches = []
    for row in holidays:
        if row.holiday_date != day:
            continue
        if row.scope == "national":
            matches.append(row)
        elif row.scope == "regional" and any(
            _fold(row.region) == _fold(item.autonomous_community) for item in assignments
        ):
            matches.append(row)
        elif row.scope == "municipal" and any(
            _fold(row.municipality) == _fold(item.municipality) for item in assignments
        ):
            matches.append(row)
    return matches


def _effective_availability(
    db: Session,
    mapped_property_ids: set[str],
    years: set[int],
    through_batch_id: int | None = None,
) -> dict[tuple[str, date], str]:
    if through_batch_id is not None and through_batch_id <= 0:
        return {}
    filters = [
        ImportBatch.dataset == "availability",
        ImportBatch.status == "valid",
        AvailabilitySlot.property_id.in_(mapped_property_ids or {""}),
    ]
    if through_batch_id is not None:
        filters.append(ImportBatch.id <= through_batch_id)
    rows = db.execute(
        select(AvailabilitySlot, ImportBatch.id)
        .join(ImportBatch, AvailabilitySlot.batch_id == ImportBatch.id)
        .where(*filters)
        .order_by(ImportBatch.id, AvailabilitySlot.id)
    ).all()
    state: dict[tuple[str, date], tuple[int, str]] = {}
    for slot, batch_id in rows:
        day = max(slot.start_date, date(min(years), 1, 1)) if years else slot.start_date
        last_day = min(slot.end_date, date(max(years) + 1, 1, 1)) if years else slot.end_date
        while day < last_day:
            key = (slot.property_id, day)
            state[key] = (batch_id, slot.status)
            day += timedelta(days=1)
    return {key: status for key, (_, status) in state.items()}


def _historical_day_metrics(
    db: Session,
    mappings: dict[str, TariffGroupAssignment],
    period_start: date,
    period_end: date | None = None,
    source_batches: dict | None = None,
) -> tuple[dict[str, dict[date, dict]], dict]:
    period_end = period_end or period_start
    years = {
        year
        for target_year in range(period_start.year, period_end.year + 1)
        for year in range(target_year - HISTORY_YEARS, target_year)
    }
    source_batches = source_batches or {}
    reservations = _effective_reservations(db, source_batches.get("reservations_through"))
    relevant_reservations = [
        row
        for row in reservations
        if row.property_id in mappings
        and mappings[row.property_id].tariff_name
        and row.start_date.year <= max(years) + 1
        and row.end_date.year >= min(years)
    ]
    availability = _effective_availability(
        db,
        set(mappings),
        years | {period_start.year - 1},
        source_batches.get("availability_through"),
    )
    by_group: dict[str, dict[date, dict]] = defaultdict(dict)
    booked_property_nights: set[tuple[str, date]] = set()
    for row in relevant_reservations:
        assignment = mappings[row.property_id]
        if row.status not in BOOKABLE_STATUSES:
            continue
        nightly_net = Decimal(row.net) / Decimal(max(row.nights, 1))
        day = row.start_date
        while day < row.end_date:
            if day.year in years:
                booked_property_nights.add((row.property_id, day))
                metric = by_group[assignment.tariff_name].setdefault(
                    day,
                    {
                        "sold": 0,
                        "revenue": Decimal("0"),
                        "lead_times": [],
                        "lengths": [],
                        "bookings": 0,
                    },
                )
                metric["sold"] += 1
                metric["revenue"] += nightly_net
                metric["bookings"] += 1
                if row.created_on:
                    metric["lead_times"].append(max(0, (row.start_date - row.created_on).days))
                metric["lengths"].append(row.nights)
            day += timedelta(days=1)

    non_sellable: set[tuple[str, date]] = set()
    for row in relevant_reservations:
        if row.status not in NON_SELLABLE_STATUSES:
            continue
        day = row.start_date
        while day < row.end_date:
            if day.year in years:
                non_sellable.add((row.property_id, day))
            day += timedelta(days=1)

    available_by_group_day: dict[tuple[str, date], int] = defaultdict(int)
    for (property_id, day), status in availability.items():
        if day.year not in years or (property_id, day) in non_sellable:
            continue
        assignment = mappings.get(property_id)
        if not assignment:
            continue
        if status == "available":
            available_by_group_day[(assignment.tariff_name, day)] += 1
        elif status == "occupied" and (property_id, day) in booked_property_nights:
            available_by_group_day[(assignment.tariff_name, day)] += 1

    property_ids_by_group: dict[str, set[str]] = defaultdict(set)
    for property_id, assignment in mappings.items():
        property_ids_by_group[assignment.tariff_name].add(property_id)

    # Keep all dates in the historical window so missing inventory is explicit.
    result: dict[str, dict[date, dict]] = defaultdict(dict)
    all_groups = {mapping.tariff_name for mapping in mappings.values()}
    first_day = date(min(years), 1, 1)
    last_day = date(max(years) + 1, 1, 1)
    days = {(group, day) for group in all_groups for day in _date_range(first_day, last_day)}
    for group, day in days:
        metric = by_group[group].get(
            day,
            {"sold": 0, "revenue": Decimal("0"), "lead_times": [], "lengths": [], "bookings": 0},
        )
        available = available_by_group_day.get((group, day), 0)
        non_sellable_properties = {
            property_id
            for property_id in property_ids_by_group[group]
            if (property_id, day) in non_sellable
        }
        expected_properties = len(property_ids_by_group[group]) - len(non_sellable_properties)
        observed_properties = sum(
            1
            for property_id in property_ids_by_group[group] - non_sellable_properties
            if (property_id, day) in availability
        )
        inventory_complete = expected_properties > 0 and observed_properties == expected_properties
        result[group][day] = {
            "available": available,
            "sold": metric["sold"],
            "revenue": metric["revenue"],
            "inventory_complete": inventory_complete,
            "observed_properties": observed_properties,
            "expected_properties": expected_properties,
            "occupancy": metric["sold"] / available if inventory_complete and available else None,
            "adr": metric["revenue"] / metric["sold"] if metric["sold"] else None,
            "revpar": metric["revenue"] / available if inventory_complete and available else None,
            "lead_times": metric["lead_times"],
            "lengths": metric["lengths"],
            "bookings": metric["bookings"],
        }
    coverage = {
        group: {
            "days_with_inventory": sum(
                1
                for metric in group_days.values()
                if metric["inventory_complete"] and metric["available"] > 0
            ),
            "days_expected": len(group_days),
            "sold_nights": sum(
                metric["sold"] for metric in group_days.values() if metric["inventory_complete"]
            ),
            "uncovered_sold_nights": sum(
                metric["sold"] for metric in group_days.values() if not metric["inventory_complete"]
            ),
            "sellable_nights": sum(
                metric["available"]
                for metric in group_days.values()
                if metric["inventory_complete"]
            ),
            "years": sorted(
                {
                    day.year
                    for day, metric in group_days.items()
                    if metric["inventory_complete"] and metric["available"] > 0
                }
            ),
        }
        for group in all_groups
        for group_days in [result.get(group, {})]
    }
    return result, coverage


def _date_range(start: date, end: date):
    day = start
    while day < end:
        yield day
        day += timedelta(days=1)


def _median(values: list[Decimal | float | int]) -> Decimal | None:
    if not values:
        return None
    return Decimal(str(statistics.median(values)))


def _historical_profile(
    metrics: dict[date, dict],
    day: date,
    holidays: list[TariffHoliday],
    assignments: list[TariffGroupAssignment],
) -> dict:
    target_holidays = _holiday_matches(day, assignments, holidays)
    holiday_names = {_fold(item.name) for item in target_holidays}
    matching_holiday_days = {
        row.holiday_date
        for row in holidays
        if row.holiday_date.year < day.year
        and _fold(row.name) in holiday_names
        and (
            row.scope == "national"
            or (
                row.scope == "regional"
                and any(
                    _fold(row.region) == _fold(item.autonomous_community) for item in assignments
                )
            )
            or (
                row.scope == "municipal"
                and any(_fold(row.municipality) == _fold(item.municipality) for item in assignments)
            )
        )
    }
    candidate_dates: set[date] = set()
    if matching_holiday_days:
        candidate_dates = {item for item in matching_holiday_days if item in metrics}
    if not candidate_dates:
        candidate_dates = {
            hist_day
            for hist_day, value in metrics.items()
            if hist_day.year < day.year
            and hist_day.month == day.month
            and hist_day.weekday() == day.weekday()
            and abs(hist_day.day - day.day) <= 7
            and (value["sold"] > 0 or value["available"] > 0)
        }
    candidates = [metrics[item] for item in candidate_dates]
    reliable_inventory = [
        item for item in candidates if item["inventory_complete"] and item["available"] > 0
    ]
    sold_rows = [item for item in candidates if item["sold"] > 0]
    sold = sum(item["sold"] for item in reliable_inventory)
    available = sum(item["available"] for item in reliable_inventory)
    revenue = sum((item["revenue"] for item in sold_rows), Decimal("0"))
    sold_nights = sum(item["sold"] for item in sold_rows)
    revpar_revenue = sum((item["revenue"] for item in reliable_inventory), Decimal("0"))
    lead = [value for item in sold_rows for value in item["lead_times"]]
    lengths = [value for item in sold_rows for value in item["lengths"]]
    return {
        "occupancy": sold / available if available else None,
        "adr": revenue / sold_nights if sold_nights else None,
        "revpar": revpar_revenue / available if available else None,
        "lead_time_days": float(_median(lead)) if lead else None,
        "length_of_stay": float(_median(lengths)) if lengths else None,
        "sample_days": len(reliable_inventory),
        "sample_years": sorted(
            {
                item_day.year
                for item_day in candidate_dates
                if item_day.year < day.year
                and metrics[item_day]["inventory_complete"]
                and metrics[item_day]["available"] > 0
            }
        ),
        "holiday_names": [row.name for row in target_holidays],
    }


def _base_rate(
    rates: list[TariffSeason],
    tariff_name: str,
    day: date,
) -> Decimal | None:
    group_rates = [row for row in rates if row.tariff_name == tariff_name and row.rate_plan == "SA"]
    direct = next((row for row in group_rates if row.start_date <= day <= row.end_date), None)
    if direct:
        return Decimal(direct.nightly_rate)
    years = sorted(
        {row.start_date.year for row in group_rates} | {row.end_date.year for row in group_rates}
    )
    for year in reversed(years):
        try:
            template_date = date(year, day.month, day.day)
        except ValueError:
            template_date = date(year, 2, 28)
        template = next(
            (row for row in group_rates if row.start_date <= template_date <= row.end_date), None
        )
        if template:
            return Decimal(template.nightly_rate)
    return None


def _merge_seasons(daily: list[dict]) -> list[dict]:
    output = []
    for group in sorted({row["tariff_name"] for row in daily}):
        rows = sorted(
            (row for row in daily if row["tariff_name"] == group), key=lambda row: row["date"]
        )
        current = None
        for row in rows:
            if row["recommended_rate"] is None:
                if current:
                    output.append(current)
                    current = None
                continue
            if (
                current
                and current["nightly_rate"] == row["recommended_rate"]
                and date.fromisoformat(current["end_date"]) + timedelta(days=1)
                == date.fromisoformat(row["date"])
            ):
                current["end_date"] = row["date"]
                current["day_count"] += 1
            else:
                if current:
                    output.append(current)
                current = {
                    "tariff_name": group,
                    "rate_plan": "SA",
                    "start_date": row["date"],
                    "end_date": row["date"],
                    "nightly_rate": row["recommended_rate"],
                    "day_count": 1,
                }
        if current:
            output.append(current)
    return output


async def calculate_study(db: Session, study: TariffStudy) -> dict:
    provider = market_provider()
    if provider is None:
        raise MarketProviderUnavailable(
            "No hay un proveedor/API autorizado instalado para consultar precios de mercado"
        )
    input_snapshot = (study.parameters or {}).get("input_snapshot") or {}
    source_batches = input_snapshot.get("source_batches") or {}
    if input_snapshot:
        mappings = {
            item["property_id"]: TariffGroupAssignment(**item)
            for item in input_snapshot.get("assignments", [])
        }
        property_rows = _current_properties(db, source_batches.get("properties"))
        rates = [
            TariffSeason(
                tariff_name=item["tariff_name"],
                rate_plan=item["rate_plan"],
                start_date=date.fromisoformat(item["start_date"]),
                end_date=date.fromisoformat(item["end_date"]),
                nightly_rate=Decimal(item["nightly_rate"]),
            )
            for item in input_snapshot.get("tariff_seasons", [])
        ]
        holidays = [
            TariffHoliday(
                holiday_date=date.fromisoformat(item["holiday_date"]),
                name=item["name"],
                scope=item["scope"],
                region=item.get("region"),
                municipality=item.get("municipality"),
                source=item.get("source", ""),
            )
            for item in input_snapshot.get("holidays", [])
        ]
    else:
        mappings = _property_mappings(db)
        property_rows = _current_properties(db)
        rates = db.scalars(select(TariffSeason).where(TariffSeason.rate_plan == "SA")).all()
        holidays = db.scalars(select(TariffHoliday)).all()
    properties = {item.property_id: item for item in property_rows}
    assignments = {
        property_id: assignment
        for property_id, assignment in mappings.items()
        if property_id in properties and assignment.tariff_name in study.tariff_names
    }
    if not assignments:
        raise TariffLabError("No hay alojamientos asignados a los grupos elegidos")
    groups_by_municipality: dict[tuple[str, str], list[str]] = defaultdict(list)
    for property_id, assignment in assignments.items():
        groups_by_municipality[(assignment.tariff_name, assignment.municipality)].append(
            property_id
        )

    start = study.period_start
    end = study.period_end
    observations: list[tuple[MarketSearchRequest, MarketObservation]] = []
    market_warnings: list[str] = []
    requests: list[MarketSearchRequest] = []
    for tariff_name, municipality in groups_by_municipality:
        group_property_capacities = [
            properties[property_id].capacity
            for property_id in groups_by_municipality[(tariff_name, municipality)]
            if property_id in properties
        ]
        request = MarketSearchRequest(
            tariff_name=tariff_name,
            municipality=municipality,
            capacity=round(statistics.median(group_property_capacities))
            if group_property_capacities
            else study.guests,
            start_date=start,
            end_date=end,
            guests=study.guests,
            nights=study.nights,
        )
        requests.append(request)
    market_results = await asyncio.gather(
        *(provider.search(request) for request in requests), return_exceptions=True
    )
    for request, result in zip(requests, market_results, strict=True):
        if isinstance(result, Exception):
            market_warnings.append(f"Consulta de mercado fallida: {type(result).__name__}")
        else:
            observations.extend((request, item) for item in result)

    historical, history_coverage = _historical_day_metrics(
        db,
        assignments,
        study.period_start,
        study.period_end,
        source_batches,
    )
    target_occupancy = study.parameters.get("target_occupancy")
    daily: list[dict] = []
    gaps: list[dict] = []
    day = start
    while day <= end:
        for tariff_name in study.tariff_names:
            group_assignments = [
                item for item in assignments.values() if item.tariff_name == tariff_name
            ]
            if not group_assignments:
                gaps.append(
                    {
                        "date": day.isoformat(),
                        "tariff_name": tariff_name,
                        "reason": "no_property_mapping",
                    }
                )
                continue
            # One tariff group is kept as one output. Its locations contribute
            # market observations to the same comparator median.
            profile = _historical_profile(
                historical.get(tariff_name, {}),
                day,
                holidays,
                group_assignments,
            )
            comparable = [
                item
                for request, item in observations
                if request.tariff_name == tariff_name
                and item.stay_date == day
                and item.nights == study.nights
                and item.guests == study.guests
                and item.currency.upper() == "EUR"
                and item.nightly_rate > 0
                and item.includes_mandatory_fees
            ]
            market_median = _median([item.nightly_rate for item in comparable])
            current_rate = _base_rate(rates, tariff_name, day)
            history_adr = profile["adr"]
            occupancy = profile["occupancy"]
            group_coverage = history_coverage.get(tariff_name, {})
            group_days = historical.get(tariff_name, {})
            historic_occupancies = [
                value["occupancy"]
                for historical_day, value in group_days.items()
                if historical_day.year < day.year and value["occupancy"] is not None
            ]
            target = (
                float(target_occupancy)
                if target_occupancy is not None
                else (statistics.median(historic_occupancies) if historic_occupancies else None)
            )
            reason = None
            recommended = None
            demand_adjustment = None
            if current_rate is None:
                reason = "no_current_sa_reference"
            elif history_adr is None:
                reason = "no_historical_adr"
            elif market_median is None or len(comparable) < MIN_COMPETITORS:
                reason = "insufficient_comparable_market_rates"
            else:
                anchor = _median([current_rate, history_adr, market_median])
                if occupancy is not None and target is not None:
                    demand_adjustment = max(-0.10, min(0.10, 0.5 * (occupancy - target)))
                candidate = anchor * Decimal(str(1 + (demand_adjustment or 0)))
                floor = current_rate * (Decimal("1") - PRICE_CHANGE_LIMIT)
                ceiling = current_rate * (Decimal("1") + PRICE_CHANGE_LIMIT)
                rounded_floor = floor.quantize(Decimal("1"), rounding=ROUND_CEILING)
                rounded_ceiling = ceiling.quantize(Decimal("1"), rounding=ROUND_FLOOR)
                rounded_candidate = candidate.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                recommended = max(rounded_floor, min(rounded_ceiling, rounded_candidate))
            if reason:
                gaps.append({"date": day.isoformat(), "tariff_name": tariff_name, "reason": reason})
            elif occupancy is None:
                gaps.append(
                    {
                        "date": day.isoformat(),
                        "tariff_name": tariff_name,
                        "reason": "occupancy_not_reliable",
                    }
                )
            market_observations = [
                {
                    "stay_date": item.stay_date.isoformat(),
                    "nightly_rate": f"{item.nightly_rate:.2f}",
                    "currency": item.currency.upper(),
                    "source": item.source,
                    "comparable_name": item.comparable_name,
                    "source_url": item.source_url,
                    "captured_at": item.captured_at.isoformat(),
                    "guests": item.guests,
                    "nights": item.nights,
                    "includes_mandatory_fees": item.includes_mandatory_fees,
                    "cancellation_policy": item.cancellation_policy,
                }
                for item in comparable
            ]
            daily.append(
                {
                    "date": day.isoformat(),
                    "tariff_name": tariff_name,
                    "current_rate": f"{current_rate:.2f}" if current_rate is not None else None,
                    "historical_adr": f"{history_adr:.2f}" if history_adr is not None else None,
                    "historical_occupancy": occupancy,
                    "occupancy_reliable": occupancy is not None,
                    "historical_revpar": f"{profile['revpar']:.2f}"
                    if profile["revpar"] is not None
                    else None,
                    "lead_time_days": profile["lead_time_days"],
                    "length_of_stay": profile["length_of_stay"],
                    "history_sample_days": profile["sample_days"],
                    "history_years": profile["sample_years"],
                    "target_occupancy": target,
                    "market_median": f"{market_median:.2f}" if market_median is not None else None,
                    "market_sample_count": len(comparable),
                    "market_observations": market_observations,
                    "holiday_names": profile["holiday_names"],
                    "demand_adjustment": demand_adjustment,
                    "recommended_rate": f"{recommended:.2f}" if recommended is not None else None,
                    "confidence": "high"
                    if occupancy is not None
                    and profile["sample_days"] >= 3
                    and len(comparable) >= 5
                    else "medium"
                    if occupancy is not None
                    and profile["sample_days"] >= 1
                    and len(comparable) >= MIN_COMPETITORS
                    else "low",
                    "reason": reason,
                    "sellable_inventory_nights": group_coverage.get("sellable_nights", 0),
                }
            )
        day += timedelta(days=1)

    seasons = _merge_seasons(daily)
    missing_count = sum(1 for row in daily if row["recommended_rate"] is None)
    market_sources = sorted({item.source for _, item in observations if item.source})
    if any(row["historical_occupancy"] is None for row in daily):
        market_warnings.append(
            "El inventario histórico tiene cobertura incompleta; se omiten ocupación, RevPAR y "
            "ajuste de demanda donde faltan noches disponibles."
        )
    comparable_sources = sorted(
        {
            (item.comparable_name, item.source_url if _is_https_url(item.source_url) else None)
            for _, item in observations
            if item.comparable_name
        }
    )
    stored_observations = []
    for request, item in observations:
        eligible = (
            start <= item.stay_date <= end
            and item.nights == study.nights
            and item.guests == study.guests
            and item.currency.upper() == "EUR"
            and item.nightly_rate > 0
            and item.includes_mandatory_fees
        )
        stored_observations.append(
            {
                "tariff_name": request.tariff_name,
                "municipality": request.municipality,
                "stay_date": item.stay_date.isoformat(),
                "nightly_rate": f"{item.nightly_rate:.2f}",
                "currency": item.currency.upper(),
                "source": item.source,
                "comparable_name": item.comparable_name,
                "source_url": item.source_url,
                "captured_at": item.captured_at.isoformat(),
                "guests": item.guests,
                "nights": item.nights,
                "includes_mandatory_fees": item.includes_mandatory_fees,
                "cancellation_policy": item.cancellation_policy,
                "eligible_for_comparison": eligible,
            }
        )
    return {
        "algorithm_version": ALGORITHM_VERSION,
        "provider": provider.name,
        "provider_at_creation": input_snapshot.get("provider"),
        "market_sources": market_sources,
        "comparables": [{"name": name, "url": url} for name, url in comparable_sources],
        "market_observations": stored_observations,
        "captured_at": datetime.now(UTC).isoformat(),
        "method": {
            "baseline": "Mediana entre la tarifa SA vigente, el ADR neto histórico y la mediana "
            "de comparables.",
            "demand_adjustment": "El 50% de la diferencia de ocupación histórica frente al "
            "objetivo, limitado a ±10%; se omite si no hay inventario completo.",
            "price_guardrail": "La tarifa propuesta se limita a ±20% de la tarifa SA de "
            "referencia.",
            "rounding": (
                "Redondeo al euro más cercano; las fechas contiguas con el mismo importe se "
                "agrupan en temporadas inclusivas."
            ),
            "market_sample_minimum": MIN_COMPETITORS,
            "history_years_requested": HISTORY_YEARS,
        },
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "guests": study.guests,
        "nights": study.nights,
        "history_coverage": history_coverage,
        "source_batches": source_batches,
        "daily": daily,
        "seasons": seasons,
        "gaps": gaps,
        "warnings": market_warnings,
        "status": "partial" if missing_count or market_warnings else "completed",
        "generated_days": len(daily) - missing_count,
        "missing_days": missing_count,
    }


async def run_study(db: Session, study_id: int) -> None:
    study = db.get(TariffStudy, study_id)
    if study is None:
        raise TariffLabError("Estudio de tarifas no encontrado")
    study.status = "running"
    study.started_at = datetime.now(UTC)
    study.error = None
    db.commit()
    result = await calculate_study(db, study)
    study = db.get(TariffStudy, study_id)
    study.result = result
    study.status = result["status"]
    study.completed_at = datetime.now(UTC)
    db.commit()


def tariffs_csv(study: TariffStudy) -> bytes:
    result = study.result or {}
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["tariff_name", "rate_plan", "start_date", "end_date", "nightly_rate"])
    for season in result.get("seasons", []):
        writer.writerow(
            [
                season["tariff_name"],
                season["rate_plan"],
                season["start_date"],
                season["end_date"],
                season["nightly_rate"],
            ]
        )
    return output.getvalue().encode("utf-8-sig")


def study_report_pdf(study: TariffStudy) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    result = study.result or {}
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=13 * mm,
        bottomMargin=13 * mm,
    )
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="AvantiaTitle",
            parent=styles["Title"],
            alignment=TA_LEFT,
            textColor=colors.HexColor("#0A192F"),
            fontSize=20,
            leading=25,
        )
    )
    styles.add(
        ParagraphStyle(
            name="AvantiaBody",
            parent=styles["BodyText"],
            fontSize=8.5,
            leading=12,
            textColor=colors.HexColor("#172A45"),
        )
    )
    story = [
        Paragraph("Laboratorio de tarifas · informe del estudio", styles["AvantiaTitle"]),
        Spacer(1, 4 * mm),
        Paragraph(
            f"<b>{_html(study.name)}</b> · {study.period_start:%d/%m/%Y}–"
            f"{study.period_end:%d/%m/%Y} · {study.guests} huéspedes · "
            f"estancias de {study.nights} noches · Estado: {_html(study.status)}",
            styles["AvantiaBody"],
        ),
        Paragraph(
            f"Versión del cálculo: {_html(study.algorithm_version)} · "
            f"Fuente de mercado: {_html(result.get('provider', 'sin proveedor'))} · "
            f"capturado: {_html(result.get('captured_at', '—'))}",
            styles["AvantiaBody"],
        ),
        Paragraph(
            "Lotes de entrada: "
            + _html(
                ", ".join(
                    f"{name}={batch_id}"
                    for name, batch_id in (result.get("source_batches") or {}).items()
                )
                or "sin histórico"
            ),
            styles["AvantiaBody"],
        ),
        Paragraph(
            "Canales: " + _html(", ".join(result.get("market_sources", [])) or "sin observaciones"),
            styles["AvantiaBody"],
        ),
        Spacer(1, 4 * mm),
        Paragraph("Cómo se calculó", styles["Heading2"]),
        Paragraph(
            _html(result.get("method", {}).get("baseline", "Sin cálculo disponible")),
            styles["AvantiaBody"],
        ),
        Paragraph(
            _html(result.get("method", {}).get("demand_adjustment", "")), styles["AvantiaBody"]
        ),
        Paragraph(
            _html(result.get("method", {}).get("price_guardrail", "")), styles["AvantiaBody"]
        ),
        Paragraph(
            f"Se generaron {result.get('generated_days', 0)} días y faltan datos para "
            f"{result.get('missing_days', 0)} días.",
            styles["AvantiaBody"],
        ),
        Spacer(1, 4 * mm),
        Paragraph("Cobertura histórica", styles["Heading2"]),
    ]
    history_data = [
        [
            "Grupo SA",
            "Años fiables",
            "Días completos / esperados",
            "Noches vendidas fiables",
            "Noches vendibles fiables",
            "Ventas sin inventario",
        ]
    ]
    for group, coverage in (result.get("history_coverage") or {}).items():
        history_data.append(
            [
                group,
                ", ".join(str(year) for year in coverage.get("years", [])) or "—",
                f"{coverage.get('days_with_inventory', 0)} / {coverage.get('days_expected', 0)}",
                str(coverage.get("sold_nights", 0)),
                str(coverage.get("sellable_nights", 0)),
                str(coverage.get("uncovered_sold_nights", 0)),
            ]
        )
    if len(history_data) == 1:
        history_data.append(["Sin datos históricos", "—", "—", "—", "—", "—"])
    history_table = Table(
        history_data,
        repeatRows=1,
        colWidths=[47 * mm, 29 * mm, 45 * mm, 39 * mm, 39 * mm, 34 * mm],
    )
    history_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#172A45")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#8892B0")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#E6F1FF")]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.extend(
        [
            history_table,
            Spacer(1, 4 * mm),
            Paragraph("Temporadas propuestas", styles["Heading2"]),
        ]
    )
    data = [["Grupo SA", "Plan", "Desde", "Hasta", "€/noche", "Días"]]
    for season in result.get("seasons", []):
        data.append(
            [
                season["tariff_name"],
                season["rate_plan"],
                season["start_date"],
                season["end_date"],
                season["nightly_rate"],
                str(season["day_count"]),
            ]
        )
    if len(data) == 1:
        data.append(["Sin temporadas completas", "—", "—", "—", "—", "—"])
    table = Table(
        data, repeatRows=1, colWidths=[75 * mm, 17 * mm, 26 * mm, 26 * mm, 25 * mm, 16 * mm]
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0A192F")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#8892B0")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#E6F1FF")]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend(
        [table, Spacer(1, 4 * mm), Paragraph("Detalle diario y explicación", styles["Heading2"])]
    )
    daily_data = [
        [
            "Fecha",
            "Grupo",
            "SA actual",
            "ADR histórico",
            "Ocupación",
            "Antelación",
            "Estancia",
            "RevPAR",
            "Mercado",
            "Muestra",
            "Festivo",
            "Propuesta",
            "Confianza / motivo",
        ]
    ]
    for row in result.get("daily", []):
        occupancy = (
            "—" if row["historical_occupancy"] is None else f"{row['historical_occupancy']:.0%}"
        )
        holidays = ", ".join(row["holiday_names"]) or "—"
        reason = row["reason"] or row["confidence"]
        daily_data.append(
            [
                row["date"],
                row["tariff_name"],
                row["current_rate"] or "—",
                row["historical_adr"] or "—",
                occupancy,
                f"{row['lead_time_days']:.0f}" if row["lead_time_days"] is not None else "—",
                f"{row['length_of_stay']:.0f}" if row["length_of_stay"] is not None else "—",
                row["historical_revpar"] or "—",
                row["market_median"] or "—",
                str(row["market_sample_count"]),
                Paragraph(_html(holidays), styles["AvantiaBody"]),
                row["recommended_rate"] or "—",
                reason,
            ]
        )
    if len(daily_data) == 1:
        daily_data.append(["Sin datos diarios"] + ["—"] * 12)
    detail_table = Table(
        daily_data,
        repeatRows=1,
        colWidths=[
            20 * mm,
            48 * mm,
            18 * mm,
            20 * mm,
            18 * mm,
            18 * mm,
            15 * mm,
            15 * mm,
            18 * mm,
            14 * mm,
            22 * mm,
            20 * mm,
            24 * mm,
        ],
    )
    detail_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#172A45")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 6.5),
                ("GRID", (0, 0), (-1, -1), 0.2, colors.HexColor("#8892B0")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#E6F1FF")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(detail_table)
    if result.get("warnings") or result.get("gaps"):
        story.extend([Spacer(1, 4 * mm), Paragraph("Cobertura y avisos", styles["Heading2"])])
        for warning in result.get("warnings", []):
            story.append(Paragraph("• " + _html(warning), styles["AvantiaBody"]))
        for gap in result.get("gaps", [])[:100]:
            story.append(
                Paragraph(
                    f"{gap['date']} · {_html(gap['tariff_name'])} · {_html(gap['reason'])}",
                    styles["AvantiaBody"],
                )
            )
    comparables = result.get("comparables", [])
    if comparables:
        story.extend(
            [
                Spacer(1, 4 * mm),
                Paragraph("Alojamientos comparables consultados", styles["Heading2"]),
            ]
        )
        for comparable in comparables[:100]:
            name = _html(comparable.get("name", "Alojamiento"))
            url = comparable.get("url")
            if _is_https_url(url):
                safe_url = _html(url)
                story.append(
                    Paragraph(
                        f'<link href="{safe_url}" color="#0A192F">{name}</link>',
                        styles["AvantiaBody"],
                    )
                )
            else:
                story.append(Paragraph(name, styles["AvantiaBody"]))
    document.build(story)
    return buffer.getvalue()


def _html(value: object) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _is_https_url(value: str | None) -> bool:
    if not value:
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme == "https" and bool(parsed.hostname)
    except ValueError:
        return False
