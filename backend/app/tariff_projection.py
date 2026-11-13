"""Yearly SA projection: next year's seasons derived from this year's, explained per season.

Each season of the base year keeps its dates in the target year and its rate moves by
three explicit adjustments: costs (inflation), own demand (historical occupancy) and
market position (hand-captured competitor prices). Nothing here writes to Avantio; a
projection only reaches Tarifas when an administrator applies it.
"""

from __future__ import annotations

import csv
import io
import statistics
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    TariffMarketObservation,
    TariffProjection,
    TariffSeason,
)
from app.tariff_lab import (
    BOOKABLE_STATUSES,
    TariffLabError,
    _current_properties,
    _effective_reservations,
    _latest_valid_batch,
    _property_mappings,
)

ALGORITHM_VERSION = "proyeccion-1.0"
HISTORY_YEARS = 4
LONG_STAY_NIGHTS = 28
SPLIT_MIN_DAYS = 60
SPLIT_OCCUPANCY_SPREAD = 0.20
EASTER_SEASON_MAX_DAYS = 21

DEFAULT_PARAMETERS: dict[str, float | int | str] = {
    "cost_percent": 3.5,
    "demand_high_occupancy": 80,
    "demand_high_percent": 2.0,
    "demand_low_occupancy": 60,
    "demand_drop_points": 5,
    "demand_low_percent": -2.0,
    "min_units": 3,
    "market_min_refs": 2,
    "market_threshold_percent": 10,
    "market_share": 0.3333,
    "market_max_percent": 5.0,
    "market_above_percent": -2.0,
    "market_inflation_percent": 3.7,
    "min_change_percent": 0.0,
    "max_change_percent": 10.0,
    "rounding": "euro",
}
PARAMETER_LIMITS: dict[str, tuple[float, float]] = {
    "cost_percent": (-10, 25),
    "demand_high_occupancy": (0, 100),
    "demand_high_percent": (-10, 10),
    "demand_low_occupancy": (0, 100),
    "demand_drop_points": (0, 100),
    "demand_low_percent": (-10, 10),
    "min_units": (1, 100),
    "market_min_refs": (1, 50),
    "market_threshold_percent": (0, 100),
    "market_share": (0, 1),
    "market_max_percent": (0, 25),
    "market_above_percent": (-25, 0),
    "market_inflation_percent": (-10, 25),
    "min_change_percent": (-25, 25),
    "max_change_percent": (-25, 50),
}
ROUNDING_MODES = ("euro", "nine")


# ---------------------------------------------------------------- calendar helpers


def easter_sunday(year: int) -> date:
    """Gregorian Easter Sunday (anonymous algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


def holy_week(year: int) -> tuple[date, date]:
    """Friday before Palm Sunday through Easter Monday, both inclusive."""
    easter = easter_sunday(year)
    return easter - timedelta(days=9), easter + timedelta(days=1)


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def shift_date(value: date, year: int, *, is_end: bool = False) -> date:
    """Same day and month in another year; the end of February follows the leap year."""
    if value.month == 2 and value.day >= 28 and is_end:
        return date(year, 2, 29 if _is_leap(year) else 28)
    if value.month == 2 and value.day == 29:
        return date(year, 2, 29) if _is_leap(year) else date(year, 3, 1)
    return value.replace(year=year)


def _days(start: date, end: date) -> int:
    return (end - start).days + 1


def _date_range(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


# ---------------------------------------------------------------- validation


def normalize_parameters(raw: dict | None) -> dict:
    params = dict(DEFAULT_PARAMETERS)
    for key, value in (raw or {}).items():
        if value is None:
            continue
        if key == "rounding":
            if value not in ROUNDING_MODES:
                raise TariffLabError("Redondeo no válido: usa «euro» o «nine»")
            params[key] = value
            continue
        if key not in PARAMETER_LIMITS:
            raise TariffLabError(f"Parámetro desconocido: {key}")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise TariffLabError(f"El parámetro {key} debe ser un número") from exc
        low, high = PARAMETER_LIMITS[key]
        if not low <= number <= high:
            raise TariffLabError(f"El parámetro {key} debe estar entre {low:g} y {high:g}")
        params[key] = int(number) if key in {"min_units", "market_min_refs"} else number
    if params["min_change_percent"] > params["max_change_percent"]:
        raise TariffLabError("La subida mínima no puede superar a la máxima")
    return params


def _sa_groups(db: Session) -> set[str]:
    return set(
        db.scalars(select(TariffSeason.tariff_name).where(TariffSeason.rate_plan == "SA")).all()
    )


def base_seasons(db: Session, group: str, year: int) -> list[dict]:
    """SA seasons of a group overlapping a year, clipped to that year."""
    start, end = date(year, 1, 1), date(year, 12, 31)
    rows = db.scalars(
        select(TariffSeason)
        .where(
            TariffSeason.tariff_name == group,
            TariffSeason.rate_plan == "SA",
            TariffSeason.start_date <= end,
            TariffSeason.end_date >= start,
        )
        .order_by(TariffSeason.start_date)
    ).all()
    return [
        {
            "start": max(row.start_date, start),
            "end": min(row.end_date, end),
            "rate": Decimal(row.nightly_rate),
        }
        for row in rows
    ]


# ---------------------------------------------------------------- history


def _round_rate(value: Decimal, mode: str) -> Decimal:
    rounded = value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if mode == "nine":
        tens = (rounded // 10) * 10
        candidates = [tens - 1, tens + 9]
        rounded = min(candidates, key=lambda item: (abs(item - value), -item))
    return Decimal(max(rounded, Decimal("1"))).quantize(Decimal("0.01"))


class _History:
    """Sold nights and gross nightly revenue per group and day."""

    def __init__(self, db: Session, groups: list[str], first_year: int, last_day: date):
        mappings = _property_mappings(db)
        active = {item.property_id for item in _current_properties(db)}
        self.units: dict[str, int] = defaultdict(int)
        property_group: dict[str, str] = {}
        for property_id, mapping in mappings.items():
            if property_id in active and mapping.tariff_name in groups:
                self.units[mapping.tariff_name] += 1
                property_group[property_id] = mapping.tariff_name
        self.sold: dict[tuple[str, date], int] = defaultdict(int)
        self.revenue: dict[tuple[str, date], Decimal] = defaultdict(Decimal)
        self.priced: dict[tuple[str, date], int] = defaultdict(int)
        self.reservations = [
            row for row in _effective_reservations(db) if row.property_id in property_group
        ]
        self.property_group = property_group
        first_day = date(first_year, 1, 1)
        for row in self.reservations:
            if row.status not in BOOKABLE_STATUSES or row.nights <= 0:
                continue
            group = property_group[row.property_id]
            nightly = Decimal(row.total) / row.nights
            for day in _date_range(
                max(row.start_date, first_day),
                min(row.end_date - timedelta(days=1), last_day),
            ):
                self.sold[(group, day)] += 1
                if row.nights < LONG_STAY_NIGHTS:
                    self.revenue[(group, day)] += nightly
                    self.priced[(group, day)] += 1

    def window(self, group: str, start: date, end: date) -> dict:
        sold = sum(self.sold[(group, day)] for day in _date_range(start, end))
        priced = sum(self.priced[(group, day)] for day in _date_range(start, end))
        revenue = sum((self.revenue[(group, day)] for day in _date_range(start, end)), Decimal())
        units = self.units.get(group, 0)
        capacity = units * _days(start, end)
        return {
            "occupancy": round(sold / capacity, 4) if capacity else None,
            "adr": (revenue / priced).quantize(Decimal("0.01")) if priced else None,
        }

    def pickup(self, group: str, year: int, as_of: date) -> dict:
        """Nights of a year booked by as_of, against the previous year at the same point."""

        def booked(stay_year: int, cutoff: date) -> int:
            total = 0
            for row in self.reservations:
                if (
                    self.property_group[row.property_id] == group
                    and row.status in BOOKABLE_STATUSES
                    and row.created_on
                    and row.created_on <= cutoff
                    and row.start_date.year == stay_year
                ):
                    total += row.nights
            return total

        current = booked(year, as_of)
        previous = booked(year - 1, shift_date(as_of, as_of.year - 1))
        return {
            "year": year,
            "nights": current,
            "previous_nights": previous,
            "change": round(current / previous - 1, 4) if previous else None,
        }


# ---------------------------------------------------------------- market


def _market(
    observations: list[TariffMarketObservation],
    season_start: date,
    season_end: date,
    base_year: int,
    inflation: float,
) -> dict | None:
    prices = []
    competitors = set()
    for item in observations:
        try:
            start = shift_date(season_start, item.stay_date.year)
            end = shift_date(season_end, item.stay_date.year, is_end=True)
        except ValueError:
            continue
        if not start <= item.stay_date <= end:
            continue
        years = base_year - item.stay_date.year
        factor = Decimal(str((1 + inflation / 100) ** years)) if years > 0 else Decimal(1)
        prices.append(Decimal(item.nightly_rate) * factor)
        competitors.add(item.competitor)
    if not prices:
        return None
    return {
        "median": Decimal(statistics.median(prices)).quantize(Decimal("0.01")),
        "refs": len(competitors),
        "observations": len(prices),
    }


# ---------------------------------------------------------------- projection


def _reference_years(start: date, end: date, base_year: int, as_of: date) -> list[int]:
    """Most recent years, up to the base year, in which the whole season has passed."""
    years = []
    for year in range(base_year, base_year - HISTORY_YEARS - 1, -1):
        if shift_date(end, year, is_end=True) < as_of:
            years.append(year)
    return years


def _month_spread(history: _History, group: str, start: date, end: date) -> dict | None:
    if _days(start, end) < SPLIT_MIN_DAYS:
        return None
    months: dict[str, tuple[date, date]] = {}
    for day in _date_range(start, end):
        key = f"{day.year}-{day.month:02d}"
        first, _ = months.get(key, (day, day))
        months[key] = (first, day)
    values = {
        key: history.window(group, first, last)["occupancy"]
        for key, (first, last) in months.items()
    }
    known = {key: value for key, value in values.items() if value is not None}
    if len(known) < 2 or max(known.values()) - min(known.values()) < SPLIT_OCCUPANCY_SPREAD:
        return None
    return known


def calculate_projection(
    db: Session,
    *,
    base_year: int,
    target_year: int,
    groups: list[str],
    parameters: dict,
    as_of: date | None = None,
) -> dict:
    params = normalize_parameters(parameters)
    reservation_batch = _latest_valid_batch(db, "reservations")
    if as_of is None:
        as_of = (
            reservation_batch.created_at.date() if reservation_batch else datetime.now(UTC).date()
        )
    history = _History(db, groups, base_year - HISTORY_YEARS, as_of - timedelta(days=1))
    observations_by_group: dict[str, list[TariffMarketObservation]] = defaultdict(list)
    for item in db.scalars(
        select(TariffMarketObservation).where(TariffMarketObservation.tariff_name.in_(groups))
    ):
        observations_by_group[item.tariff_name].append(item)

    cost = params["cost_percent"] / 100
    seasons: list[dict] = []
    group_summary: dict[str, dict] = {}
    warnings: list[str] = []
    for group in groups:
        base = base_seasons(db, group, base_year)
        if not base:
            warnings.append(f"{group}: no hay temporadas SA en {base_year}")
            continue
        covered = sum(_days(item["start"], item["end"]) for item in base)
        if covered < _days(date(base_year, 1, 1), date(base_year, 12, 31)):
            warnings.append(f"{group}: las temporadas SA de {base_year} no cubren todo el año")
        units = history.units.get(group, 0)
        reliable_units = units >= params["min_units"]
        if not units:
            warnings.append(
                f"{group}: no tiene alojamientos asignados; se omite el ajuste de demanda"
            )
        elif not reliable_units:
            warnings.append(
                f"{group}: solo {units} alojamientos; la ocupación no es representativa y "
                "se omite el ajuste de demanda"
            )
        for item in base:
            flags: list[str] = []
            years = _reference_years(item["start"], item["end"], base_year, as_of)
            ref_year = years[0] if years else None
            prev_year = years[1] if len(years) > 1 else None
            ref = (
                history.window(
                    group,
                    shift_date(item["start"], ref_year),
                    shift_date(item["end"], ref_year, is_end=True),
                )
                if ref_year
                else {"occupancy": None, "adr": None}
            )
            prev = (
                history.window(
                    group,
                    shift_date(item["start"], prev_year),
                    shift_date(item["end"], prev_year, is_end=True),
                )
                if prev_year
                else {"occupancy": None, "adr": None}
            )
            demand = 0.0
            occupancy = ref["occupancy"]
            if units and reliable_units and occupancy is not None:
                if occupancy * 100 >= params["demand_high_occupancy"]:
                    demand = params["demand_high_percent"] / 100
                elif (
                    occupancy * 100 < params["demand_low_occupancy"]
                    and prev["occupancy"] is not None
                    and (prev["occupancy"] - occupancy) * 100 >= params["demand_drop_points"]
                ):
                    demand = params["demand_low_percent"] / 100
            elif units and reliable_units:
                flags.append("sin_historico")

            market = _market(
                observations_by_group[group],
                item["start"],
                item["end"],
                base_year,
                params["market_inflation_percent"],
            )
            market_adj = 0.0
            gap = None
            if market:
                gap = float(market["median"] / item["rate"] - 1)
                if market["refs"] < params["market_min_refs"]:
                    flags.append("mercado_pocas_referencias")
                elif gap * 100 >= params["market_threshold_percent"]:
                    market_adj = min(
                        gap * params["market_share"], params["market_max_percent"] / 100
                    )
                elif gap * 100 <= -params["market_threshold_percent"]:
                    market_adj = params["market_above_percent"] / 100

            raw_total = cost + demand + market_adj
            total = max(
                params["min_change_percent"] / 100,
                min(params["max_change_percent"] / 100, raw_total),
            )
            if total != raw_total:
                flags.append("limitado")
            rate = _round_rate(item["rate"] * Decimal(str(1 + total)), str(params["rounding"]))
            spread = (
                _month_spread(
                    history,
                    group,
                    shift_date(item["start"], ref_year),
                    shift_date(item["end"], ref_year, is_end=True),
                )
                if ref_year and reliable_units
                else None
            )
            if spread:
                flags.append("separar_temporada")
            seasons.append({
                "key": f"{group}|{shift_date(item['start'], target_year).isoformat()}",
                "tariff_name": group,
                "base_start": item["start"].isoformat(),
                "base_end": item["end"].isoformat(),
                "start": shift_date(item["start"], target_year).isoformat(),
                "end": shift_date(item["end"], target_year, is_end=True).isoformat(),
                "base_rate": f"{item['rate']:.2f}",
                "occupancy": occupancy,
                "occupancy_year": ref_year,
                "previous_occupancy": prev["occupancy"],
                "previous_year": prev_year,
                "adr": f"{ref['adr']:.2f}" if ref["adr"] is not None else None,
                "market_median": f"{market['median']:.2f}" if market else None,
                "market_refs": market["refs"] if market else 0,
                "market_gap": round(gap, 4) if gap is not None else None,
                "cost_adjustment": round(cost, 4),
                "demand_adjustment": round(demand, 4),
                "market_adjustment": round(market_adj, 4),
                "total_change": round(total, 4),
                "rate": f"{rate:.2f}",
                "monthly_occupancy": spread,
                "flags": flags,
            })
        group_summary[group] = {
            "units": units,
            "pickup": history.pickup(group, base_year, as_of) if units else None,
        }

    calendar = _easter_calendar(seasons, base_year, target_year)
    for group in group_summary:
        group_summary[group].update(_averages(seasons, group))
    return {
        "as_of": as_of.isoformat(),
        "reservation_batch_id": reservation_batch.id if reservation_batch else None,
        "parameters": params,
        "groups": group_summary,
        "seasons": seasons,
        "easter": calendar,
        "warnings": warnings,
    }


def _averages(seasons: list[dict], group: str) -> dict:
    rows = [item for item in seasons if item["tariff_name"] == group]
    weight = sum(
        _days(date.fromisoformat(item["start"]), date.fromisoformat(item["end"])) for item in rows
    )
    if not weight:
        return {"average_base_rate": None, "average_rate": None}

    def average(field: str) -> str:
        total = sum(
            Decimal(item[field])
            * _days(date.fromisoformat(item["start"]), date.fromisoformat(item["end"]))
            for item in rows
        )
        return f"{total / weight:.2f}"

    return {"average_base_rate": average("base_rate"), "average_rate": average("rate")}


def _easter_calendar(seasons: list[dict], base_year: int, target_year: int) -> dict:
    """When Easter changes month, move the Holy Week price to the new dates."""
    base_easter = easter_sunday(base_year)
    target_start, target_end = holy_week(target_year)
    result = {
        "base_easter": base_easter.isoformat(),
        "target_easter": easter_sunday(target_year).isoformat(),
        "window_start": target_start.isoformat(),
        "window_end": target_end.isoformat(),
        "groups": {},
    }
    by_group: dict[str, list[dict]] = defaultdict(list)
    for item in seasons:
        by_group[item["tariff_name"]].append(item)
    for group, rows in by_group.items():
        index = next(
            (
                position
                for position, item in enumerate(rows)
                if date.fromisoformat(item["base_start"])
                <= base_easter
                <= date.fromisoformat(item["base_end"])
                and _days(
                    date.fromisoformat(item["base_start"]), date.fromisoformat(item["base_end"])
                )
                <= EASTER_SEASON_MAX_DAYS
            ),
            None,
        )
        if index is None:
            continue
        easter_row = rows[index]
        if (
            date.fromisoformat(easter_row["start"]) <= target_start
            and target_end <= date.fromisoformat(easter_row["end"])
        ):
            continue
        neighbour = rows[index - 1] if index > 0 else (rows[index + 1] if len(rows) > 1 else None)
        if neighbour is None:
            continue
        adjusted: list[dict] = []
        for position, item in enumerate(rows):
            rate = neighbour["rate"] if position == index else item["rate"]
            label = (
                f"Sin Semana Santa: toma la tarifa de {neighbour['start']} – {neighbour['end']}"
                if position == index
                else None
            )
            adjusted.extend(
                _carve(item, target_start, target_end, rate, label, easter_row["rate"])
            )
        result["groups"][group] = {
            "easter_season": {"start": easter_row["start"], "end": easter_row["end"]},
            "seasons": adjusted,
        }
    return result


def _carve(
    item: dict, window_start: date, window_end: date, rate: str, label: str | None, easter_rate: str
) -> list[dict]:
    start, end = date.fromisoformat(item["start"]), date.fromisoformat(item["end"])
    group = item["tariff_name"]

    def piece(first: date, last: date, value: str, note: str | None) -> dict:
        return {
            "key": f"{group}|{first.isoformat()}",
            "tariff_name": group,
            "start": first.isoformat(),
            "end": last.isoformat(),
            "rate": value,
            "note": note,
        }

    if end < window_start or start > window_end:
        return [piece(start, end, rate, label)]
    pieces = []
    if start < window_start:
        pieces.append(piece(start, window_start - timedelta(days=1), rate, label))
    pieces.append(
        piece(max(start, window_start), min(end, window_end), easter_rate, "Semana Santa")
    )
    if end > window_end:
        pieces.append(piece(window_end + timedelta(days=1), end, rate, label))
    return pieces


# ---------------------------------------------------------------- persistence


def validate_request(
    db: Session, *, name: str, base_year: int, target_year: int, groups: list[str]
) -> None:
    if not name.strip():
        raise TariffLabError("La proyección necesita un nombre")
    if target_year <= base_year:
        raise TariffLabError("El año de destino debe ser posterior al año base")
    if target_year - base_year > 3:
        raise TariffLabError("Proyecta como máximo tres años por delante del año base")
    if not groups or len(set(groups)) != len(groups):
        raise TariffLabError("Selecciona uno o más grupos SA sin duplicados")
    unknown = sorted(set(groups) - _sa_groups(db))
    if unknown:
        raise TariffLabError(f"Grupos SA desconocidos: {', '.join(unknown)}")


def create_projection(db: Session, data: dict, actor_user_id: int) -> TariffProjection:
    groups = [str(value).strip() for value in data["tariff_names"]]
    validate_request(
        db,
        name=data["name"],
        base_year=data["base_year"],
        target_year=data["target_year"],
        groups=groups,
    )
    result = calculate_projection(
        db,
        base_year=data["base_year"],
        target_year=data["target_year"],
        groups=groups,
        parameters=data.get("parameters") or {},
    )
    if not result["seasons"]:
        raise TariffLabError(f"No hay temporadas SA de {data['base_year']} para proyectar")
    projection = TariffProjection(
        name=data["name"].strip(),
        base_year=data["base_year"],
        target_year=data["target_year"],
        tariff_names=groups,
        parameters=result["parameters"],
        result=result,
        overrides={},
        move_easter=bool(result["easter"]["groups"]),
        status="draft",
        algorithm_version=ALGORITHM_VERSION,
        created_by_id=actor_user_id,
    )
    db.add(projection)
    db.flush()
    return projection


def final_seasons(projection: TariffProjection) -> list[dict]:
    """Seasons that would be written to Tarifas, with manual changes applied."""
    result = projection.result or {}
    easter_groups = (result.get("easter") or {}).get("groups", {})
    rows: list[dict] = []
    for group in projection.tariff_names:
        if projection.move_easter and group in easter_groups:
            source = easter_groups[group]["seasons"]
        else:
            source = [item for item in result.get("seasons", []) if item["tariff_name"] == group]
        for item in source:
            override = (projection.overrides or {}).get(item["key"])
            rows.append({
                "key": item["key"],
                "tariff_name": group,
                "start": item["start"],
                "end": item["end"],
                "proposed_rate": item["rate"],
                "rate": override["rate"] if override else item["rate"],
                "override_reason": override.get("reason") if override else None,
                "note": item.get("note"),
            })
    return rows


def update_projection(projection: TariffProjection, data: dict) -> TariffProjection:
    if projection.status != "draft":
        raise TariffLabError("La proyección ya se aplicó a Tarifas y no se puede modificar")
    if data.get("move_easter") is not None:
        projection.move_easter = bool(data["move_easter"])
    if data.get("notes") is not None:
        projection.notes = data["notes"].strip() or None
    if data.get("overrides") is not None:
        keys = {item["key"] for item in projection.result.get("seasons", [])}
        for group in (projection.result.get("easter") or {}).get("groups", {}).values():
            keys |= {item["key"] for item in group["seasons"]}
        overrides = {}
        for key, value in data["overrides"].items():
            if key not in keys:
                raise TariffLabError("Hay un cambio manual para una temporada que no existe")
            if value is None:
                continue
            try:
                rate = Decimal(str(value.get("rate")))
            except InvalidOperation as exc:
                raise TariffLabError("El precio manual debe ser un número") from exc
            if not rate.is_finite() or rate <= 0 or rate > Decimal("10000"):
                raise TariffLabError("El precio manual debe estar entre 1 y 10.000 €")
            overrides[key] = {
                "rate": f"{rate.quantize(Decimal('0.01')):.2f}",
                "reason": (str(value.get("reason") or "").strip()[:300]) or None,
            }
        projection.overrides = overrides
    projection.updated_at = datetime.now(UTC)
    return projection


def apply_projection(db: Session, projection: TariffProjection, actor_user_id: int) -> int:
    if projection.status != "draft":
        raise TariffLabError("La proyección ya se aplicó a Tarifas")
    rows = final_seasons(projection)
    start = date(projection.target_year, 1, 1)
    end = date(projection.target_year, 12, 31)
    existing = sorted(
        set(
            db.scalars(
                select(TariffSeason.tariff_name).where(
                    TariffSeason.tariff_name.in_(projection.tariff_names),
                    TariffSeason.rate_plan == "SA",
                    TariffSeason.start_date <= end,
                    TariffSeason.end_date >= start,
                )
            ).all()
        )
    )
    if existing:
        raise TariffLabError(
            f"Ya hay tarifas SA de {projection.target_year} para: {', '.join(existing)}. "
            "No se sobrescriben."
        )
    for item in rows:
        db.add(TariffSeason(
            tariff_name=item["tariff_name"],
            rate_plan="SA",
            start_date=date.fromisoformat(item["start"]),
            end_date=date.fromisoformat(item["end"]),
            nightly_rate=Decimal(item["rate"]),
        ))
    projection.status = "applied"
    projection.applied_by_id = actor_user_id
    projection.applied_at = datetime.now(UTC)
    projection.updated_at = projection.applied_at
    return len(rows)


def projection_view(projection: TariffProjection, include_result: bool = True) -> dict:
    view = {
        "id": projection.id,
        "name": projection.name,
        "base_year": projection.base_year,
        "target_year": projection.target_year,
        "tariff_names": projection.tariff_names,
        "status": projection.status,
        "algorithm_version": projection.algorithm_version,
        "move_easter": projection.move_easter,
        "notes": projection.notes,
        "created_at": projection.created_at.isoformat() if projection.created_at else None,
        "applied_at": projection.applied_at.isoformat() if projection.applied_at else None,
        "override_count": len(projection.overrides or {}),
    }
    if include_result:
        view["result"] = projection.result
        view["overrides"] = projection.overrides or {}
        view["final_seasons"] = final_seasons(projection)
    return view


def list_projections(db: Session) -> list[dict]:
    rows = db.scalars(select(TariffProjection).order_by(TariffProjection.id.desc()).limit(100))
    return [projection_view(row, include_result=False) for row in rows]


def delete_projection(db: Session, projection: TariffProjection) -> None:
    if projection.status != "draft":
        raise TariffLabError("Una proyección aplicada se conserva como registro")
    db.delete(projection)


def projection_csv(projection: TariffProjection) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["tariff_name", "rate_plan", "start_date", "end_date", "nightly_rate"])
    for item in final_seasons(projection):
        writer.writerow([item["tariff_name"], "SA", item["start"], item["end"], item["rate"]])
    return output.getvalue().encode("utf-8")


# ---------------------------------------------------------------- market observations


def observation_view(row: TariffMarketObservation) -> dict:
    return {
        "id": row.id,
        "tariff_name": row.tariff_name,
        "competitor": row.competitor,
        "unit_label": row.unit_label,
        "stay_date": row.stay_date.isoformat(),
        "nights": row.nights,
        "guests": row.guests,
        "nightly_rate": f"{row.nightly_rate:.2f}",
        "source": row.source,
        "observed_on": row.observed_on.isoformat(),
        "notes": row.notes,
    }


def list_observations(db: Session) -> list[dict]:
    rows = db.scalars(
        select(TariffMarketObservation).order_by(
            TariffMarketObservation.stay_date.desc(),
            TariffMarketObservation.tariff_name,
            TariffMarketObservation.competitor,
        )
    )
    return [observation_view(row) for row in rows]


def add_observations(db: Session, data: dict, actor_user_id: int) -> list[TariffMarketObservation]:
    groups = [str(value).strip() for value in data["tariff_names"]]
    if not groups or len(set(groups)) != len(groups):
        raise TariffLabError("Selecciona uno o más grupos SA sin duplicados")
    unknown = sorted(set(groups) - _sa_groups(db))
    if unknown:
        raise TariffLabError(f"Grupos SA desconocidos: {', '.join(unknown)}")
    competitor = data["competitor"].strip()
    if not competitor:
        raise TariffLabError("Indica el alojamiento de la competencia")
    if data["observed_on"] > datetime.now(UTC).date():
        raise TariffLabError("La fecha de consulta no puede ser futura")
    rows = [
        TariffMarketObservation(
            tariff_name=group,
            competitor=competitor[:180],
            unit_label=(data.get("unit_label") or "").strip()[:120] or None,
            stay_date=data["stay_date"],
            nights=data["nights"],
            guests=data["guests"],
            nightly_rate=Decimal(str(data["nightly_rate"])).quantize(Decimal("0.01")),
            source=(data.get("source") or "manual").strip()[:255] or "manual",
            observed_on=data["observed_on"],
            notes=(data.get("notes") or "").strip() or None,
            created_by_id=actor_user_id,
        )
        for group in groups
    ]
    db.add_all(rows)
    db.flush()
    return rows

