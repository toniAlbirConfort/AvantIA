"""Guests to win back: stayed with us in earlier years and have no booking this year.

Reservation snapshots carry only the guest name (the occupant, or the customer when
the occupant is blank), not the Avantio customer id. Guests are therefore grouped by
their accent and case insensitive name, and each group is linked to the customer
directory by exact name. The link quality travels with every row so nobody contacts
the wrong person: ``exact`` (one customer), ``ambiguous`` (several) or ``none``.
"""

from __future__ import annotations

import csv
import io
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import SOLD_STATUSES, channel_of
from app.customers import fold
from app.models import Customer, PropertySnapshot
from app.planning import consolidated_reservations, latest_source_batch

# Any of these in the target year means the guest has already booked again.
ACTIVE_STATUSES = SOLD_STATUSES | {"pre_reservation"}
PLACEHOLDER_NAMES = {"huesped sin nombre", "no disponible", "uso de propietario"}
CHANNELS = ("Directo", "Booking", "Expedia / Hotelbeds", "Airbnb", "Agencias y otros")
CONTACT_FILTERS = ("any", "email", "phone", "opt_in")
MAX_BASE_YEARS = 12


def _customer_index(db: Session) -> dict[str, list[dict]]:
    rows = db.execute(
        select(
            Customer.avantio_id, Customer.display_name, Customer.first_name,
            Customer.last_name, Customer.email, Customer.phone, Customer.phone_alt1,
            Customer.phone_alt2, Customer.language, Customer.country, Customer.email_opt_in,
        )
    )
    index: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        customer = {
            "avantio_id": row.avantio_id,
            "email": row.email,
            "phone": row.phone or row.phone_alt1 or row.phone_alt2,
            "language": row.language,
            "country": row.country,
            "email_opt_in": row.email_opt_in,
        }
        short = " ".join(part for part in (row.first_name, row.last_name) if part)
        for key in {fold(row.display_name), fold(short)} - {""}:
            index[key].append(customer)
    return index


def _link(index: dict[str, list[dict]], key: str) -> dict:
    found = {item["avantio_id"]: item for item in index.get(key, [])}
    if len(found) == 1:
        return {"match": "exact", "customer": next(iter(found.values())), "candidates": 1}
    return {"match": "ambiguous" if found else "none", "customer": None, "candidates": len(found)}


def _mode(values: list[str]) -> str | None:
    return Counter(values).most_common(1)[0][0] if values else None


def month_span(month_from: int | None, month_to: int | None) -> set[int] | None:
    """Arrival months to keep; a range like December-February wraps over the new year."""
    if month_from is None and month_to is None:
        return None
    if month_from is None or month_to is None:
        raise ValueError("Indica el mes inicial y el final")
    if not (1 <= month_from <= 12 and 1 <= month_to <= 12):
        raise ValueError("Los meses van del 1 al 12")
    if month_from <= month_to:
        return set(range(month_from, month_to + 1))
    return set(range(month_from, 13)) | set(range(1, month_to + 1))


def retention_view(
    db: Session,
    *,
    target_year: int | None = None,
    base_years: list[int] | None = None,
    min_stays: int = 1,
    channel: str | None = None,
    contact: str = "any",
    q: str = "",
    month_from: int | None = None,
    month_to: int | None = None,
    offset: int = 0,
    limit: int = 50,
) -> dict:
    months = month_span(month_from, month_to)
    target = target_year or date.today().year
    years = sorted(set(base_years or range(target - 3, target)))
    if not years or any(year >= target for year in years):
        raise ValueError("Los años de referencia deben ser anteriores al año objetivo")
    if len(years) > MAX_BASE_YEARS:
        raise ValueError(f"Elige como máximo {MAX_BASE_YEARS} años de referencia")
    if channel and channel not in CHANNELS:
        raise ValueError("Canal no reconocido")
    if contact not in CONTACT_FILTERS:
        raise ValueError("Filtro de contacto no reconocido")

    empty = {
        "items": [], "total": 0, "offset": offset, "limit": limit, "has_snapshot": False,
        "target_year": target, "base_years": years, "channels": list(CHANNELS),
        "summary": {"guests": 0, "linked": 0, "with_email": 0, "opt_in": 0},
        "years_available": [],
        "months": None if months is None else [month_from, month_to],
    }
    batch = latest_source_batch(db, "reservations")
    if batch is None:
        return empty

    stays: dict[str, list] = defaultdict(list)
    returning: set[str] = set()
    # With a month range, only a booking for those months in the target year means the
    # guest is back; bookings for other months are listed so the offer can mention them.
    other_bookings: dict[str, list] = defaultdict(list)
    # Active bookings (pre-reservations included) per arrival year, so the view can
    # show which years have data, future ones too.
    per_year: Counter[int] = Counter()

    def in_months(day: date) -> bool:
        return months is None or day.month in months

    for row in consolidated_reservations(db, batch).values():
        key = fold(row.display_name)
        if not key or key in PLACEHOLDER_NAMES:
            continue
        active = row.status in ACTIVE_STATUSES
        if active and in_months(row.start_date):
            per_year[row.start_date.year] += 1
        if active and row.start_date.year == target:
            if in_months(row.start_date):
                returning.add(key)
            else:
                other_bookings[key].append(row)
        elif active and months is None and row.created_on and row.created_on.year == target:
            returning.add(key)
        elif (
            row.status in SOLD_STATUSES and row.start_date.year in years
            and in_months(row.start_date)
        ):
            stays[key].append(row)

    property_batch = latest_source_batch(db, "properties")
    property_names = {} if property_batch is None else dict(
        db.execute(
            select(PropertySnapshot.property_id, PropertySnapshot.name)
            .where(PropertySnapshot.batch_id == property_batch.id)
        ).all()
    )
    index = _customer_index(db)
    needle = fold(q)
    items = []
    for key, rows in stays.items():
        if key in returning or len(rows) < min_stays:
            continue
        channels = [channel_of(row.portal) for row in rows]
        main_channel = _mode(channels)
        if channel and main_channel != channel:
            continue
        if needle and needle not in key:
            continue
        link = _link(index, key)
        customer = link["customer"] or {}
        if contact == "email" and not customer.get("email"):
            continue
        if contact == "phone" and not customer.get("phone"):
            continue
        if contact == "opt_in" and not (customer.get("email") and customer.get("email_opt_in")):
            continue
        latest = max(rows, key=lambda row: row.start_date)
        main_property = _mode([row.property_id for row in rows])
        items.append({
            "key": key,
            "display_name": latest.display_name,
            "years": sorted({row.start_date.year for row in rows}),
            "stays": len(rows),
            "nights": sum(row.nights for row in rows),
            "total": sum((row.total for row in rows), Decimal(0)),
            "last_stay_start": latest.start_date,
            "last_stay_end": latest.end_date,
            "last_locator": latest.locator,
            "channel": main_channel,
            "property_id": main_property,
            "property_name": property_names.get(main_property, main_property),
            "match": link["match"],
            "candidates": link["candidates"],
            "target_bookings": [
                {
                    "locator": row.locator, "start_date": row.start_date,
                    "end_date": row.end_date, "status": row.status,
                }
                for row in sorted(other_bookings.get(key, []), key=lambda row: row.start_date)
            ],
            # Every counted stay, newest first, so the row can unfold its history.
            "history": [
                {
                    "locator": row.locator,
                    "start_date": row.start_date,
                    "end_date": row.end_date,
                    "nights": row.nights,
                    "guests": row.adults + row.children + row.infants,
                    "property_id": row.property_id,
                    "property_name": property_names.get(row.property_id, row.property_id),
                    "status": row.status,
                    "channel": channel_of(row.portal),
                    "portal": row.portal,
                    "created_on": row.created_on,
                    "total": row.total,
                }
                for row in sorted(rows, key=lambda row: (row.start_date, row.locator), reverse=True)
            ],
            **{
                field: customer.get(field)
                for field in ("avantio_id", "email", "phone", "language", "country", "email_opt_in")
            },
        })

    items.sort(key=lambda item: (
        -item["stays"], -item["total"], -item["last_stay_start"].toordinal(), item["key"],
    ))
    return {
        **empty,
        "items": items[offset : offset + limit],
        "total": len(items),
        "has_snapshot": True,
        "summary": {
            "guests": len(items),
            "linked": sum(1 for item in items if item["match"] == "exact"),
            "with_email": sum(1 for item in items if item["email"]),
            "opt_in": sum(1 for item in items if item["email"] and item["email_opt_in"]),
        },
        "years_available": [
            {"year": year, "reservations": count} for year, count in sorted(per_year.items())
        ],
    }


MATCH_LABELS = {"exact": "Ficha única", "ambiguous": "Varias fichas", "none": "Sin ficha"}
CSV_COLUMNS = (
    ("Huésped", "display_name"), ("Años", "years"), ("Estancias", "stays"),
    ("Noches", "nights"), ("Importe total", "total"), ("Última entrada", "last_stay_start"),
    ("Último localizador", "last_locator"), ("Canal habitual", "channel"),
    ("Alojamiento habitual", "property_name"), ("Enlace con ficha", "match"),
    ("Id cliente", "avantio_id"), ("E-mail", "email"), ("Teléfono", "phone"),
    ("Idioma", "language"), ("País", "country"), ("Newsletters", "email_opt_in"),
)


def retention_csv(items: list[dict]) -> str:
    """Semicolon separated with BOM, so Spanish Excel opens it with accents and columns."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(label for label, _ in CSV_COLUMNS)
    for item in items:
        values = []
        for _, field in CSV_COLUMNS:
            value = item[field]
            if field == "years":
                value = ", ".join(str(year) for year in value)
            elif field == "match":
                value = MATCH_LABELS[value]
            elif field == "email_opt_in":
                value = "" if value is None else "Sí" if value else "No"
            elif field == "total":
                value = f"{value:.2f}".replace(".", ",")
            elif isinstance(value, date):
                value = value.strftime("%d/%m/%Y")
            values.append("" if value is None else value)
        writer.writerow(values)
    return "\ufeff" + buffer.getvalue()
