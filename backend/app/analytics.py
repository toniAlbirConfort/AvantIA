"""Year-over-year analysis of the consolidated «Listado de reservas» export.

Every year is compared over the same window (1 January up to the day of the
latest valid reservation import) so a year in progress is never measured
against complete ones. Occupancy uses the apartments that are active in the
current catalogue, which keeps the base homogeneous across years.
"""

from __future__ import annotations

import statistics
import unicodedata
from collections import defaultdict
from collections.abc import Callable
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PropertySnapshot, ReservationSnapshot
from app.planning import BLOCKING_STATUSES, consolidated_reservations, latest_source_batch

SOLD_STATUSES = frozenset({"confirmed", "paid"})
LONG_STAY_NIGHTS = 28
ORPHAN_GAP_NIGHTS = 3
DIRECT_PREFIXES = ("canal", "albir confort")
MIN_YEARS, MAX_YEARS = 2, 8


def _fold(value: str | None) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", (value or "").casefold())
        if not unicodedata.combining(character)
    ).strip()


def _rentable(prop: PropertySnapshot) -> bool:
    category = _fold(prop.category)
    return prop.active and "garaje" not in category and "parking" not in category


def channel_of(portal: str | None) -> str:
    folded = _fold(portal)
    if not folded or folded.startswith(DIRECT_PREFIXES):
        return "Directo"
    if "booking" in folded:
        return "Booking"
    if "expedia" in folded or "hotelbeds" in folded:
        return "Expedia / Hotelbeds"
    if "airbnb" in folded:
        return "Airbnb"
    return "Agencias y otros"


def stay_bucket(nights: int) -> str:
    if nights <= 3:
        return "1-3 noches"
    if nights <= 7:
        return "4-7 noches"
    if nights <= 14:
        return "8-14 noches"
    if nights < LONG_STAY_NIGHTS:
        return "15-27 noches"
    return "28 noches o más"


def guest_bucket(row: ReservationSnapshot) -> str:
    if row.children or row.infants:
        return "Familia con menores"
    if row.adults <= 1:
        return "1 adulto"
    if row.adults == 2:
        return "2 adultos"
    return "3 o más adultos"


def lead_bucket(row: ReservationSnapshot) -> str:
    if not row.created_on:
        return "Sin fecha de alta"
    days = (row.start_date - row.created_on).days
    if days < 15:
        return "Menos de 15 días"
    if days < 60:
        return "15-59 días"
    if days < 180:
        return "60-179 días"
    return "180 días o más"


SEGMENT_ORDER = {
    "stay": ["1-3 noches", "4-7 noches", "8-14 noches", "15-27 noches", "28 noches o más"],
    "guests": ["1 adulto", "2 adultos", "3 o más adultos", "Familia con menores"],
    "lead": [
        "Menos de 15 días", "15-59 días", "60-179 días", "180 días o más", "Sin fecha de alta",
    ],
}


def _same_day(year: int, reference: date) -> date:
    try:
        return reference.replace(year=year)
    except ValueError:  # 29 February in a non-leap year
        return date(year, 2, 28)


def _overlap(row: ReservationSnapshot, start: date, end: date) -> int:
    return max((min(row.end_date, end) - max(row.start_date, start)).days, 0)


def _nightly(row: ReservationSnapshot) -> float:
    return float(row.net) / row.nights if row.nights else 0.0


def _ratio(part: float, whole: float) -> float | None:
    return round(part / whole, 4) if whole else None


def _change(current: float | None, previous: float | None) -> float | None:
    if current is None or not previous:
        return None
    return round((current - previous) / previous, 4)


def _median(values: list[int]) -> float | None:
    return float(statistics.median(values)) if values else None


def _window_metrics(rows: list[ReservationSnapshot], units: int, start: date, end: date) -> dict:
    nights = 0
    revenue = 0.0
    for row in rows:
        if row.status in SOLD_STATUSES:
            overlap = _overlap(row, start, end)
            nights += overlap
            revenue += _nightly(row) * overlap
    capacity = units * (end - start).days
    return {
        "nights": nights,
        "revenue": round(revenue, 2),
        "occupancy": _ratio(nights, capacity),
        "adr": round(revenue / nights, 2) if nights else None,
        "revpar": round(revenue / capacity, 2) if capacity else None,
    }


def _segment(
    rows: list[ReservationSnapshot],
    years: list[int],
    windows: dict[int, tuple[date, date]],
    key: Callable[[ReservationSnapshot], str],
    order: list[str] | None = None,
    units: dict[str, int] | None = None,
) -> list[dict]:
    nights: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    revenue: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    totals: dict[int, int] = defaultdict(int)
    for row in rows:
        if row.status not in SOLD_STATUSES:
            continue
        label = key(row)
        for year in years:
            overlap = _overlap(row, *windows[year])
            if overlap:
                nights[label][year] += overlap
                revenue[label][year] += _nightly(row) * overlap
                totals[year] += overlap
    labels = list(order or []) + sorted(set(nights) - set(order or []))
    result = []
    for label in labels:
        if label not in nights and not (units and label in units):
            continue
        values = {}
        for year in years:
            sold = nights[label][year]
            start, end = windows[year]
            values[str(year)] = {
                "nights": sold,
                "adr": round(revenue[label][year] / sold, 2) if sold else None,
                "share": _ratio(sold, totals[year]),
                "occupancy": (
                    _ratio(sold, units[label] * (end - start).days) if units else None
                ),
            }
        last, previous = years[-1], years[-2]
        item = {
            "key": label,
            "values": values,
            "nights_change": _change(nights[label][last], nights[label][previous]),
        }
        if units:
            item["units"] = units.get(label, 0)
        result.append(item)
    return result


def _orphan_gaps(rows: list[ReservationSnapshot], start: date, end: date) -> dict:
    by_property: dict[str, list[ReservationSnapshot]] = defaultdict(list)
    for row in rows:
        if row.status in BLOCKING_STATUSES:
            by_property[row.property_id].append(row)
    gaps = nights = 0
    for stays in by_property.values():
        stays.sort(key=lambda row: row.start_date)
        for before, after in zip(stays, stays[1:]):
            gap = (after.start_date - before.end_date).days
            if 0 < gap <= ORPHAN_GAP_NIGHTS and start <= before.end_date < end:
                gaps += 1
                nights += gap
    return {"gaps": gaps, "nights": nights}


def _num(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:+.0f} %".replace(".", ",")


def _insights(result: dict) -> list[dict]:
    insights: list[dict] = []
    yearly = result["yearly"]
    if len(yearly) < 2:
        return insights
    current, previous = yearly[-1], yearly[-2]
    year, prior = current["year"], previous["year"]

    if current["occupancy"] is not None and previous["occupancy"] is not None:
        points = (current["occupancy"] - previous["occupancy"]) * 100
        adr = _change(current["adr"], previous["adr"])
        revpar = _change(current["revpar"], previous["revpar"])
        if points < -2 and (adr or 0) > 0:
            insights.append({
                "severity": "warning",
                "title": "Más precio, menos noches vendidas",
                "detail": (
                    f"En {year} la ocupación cae {_num(abs(points))} puntos frente a {prior} "
                    f"mientras el precio medio sube {_pct(adr)}; el ingreso por apartamento "
                    f"disponible varía {_pct(revpar)}."
                ),
            })
        elif points < -2:
            insights.append({
                "severity": "warning",
                "title": "La ocupación baja",
                "detail": f"En {year} la ocupación cae {_num(abs(points))} puntos frente a {prior}.",
            })
        elif points > 2:
            insights.append({
                "severity": "positive",
                "title": "La ocupación mejora",
                "detail": f"En {year} la ocupación sube {_num(points)} puntos frente a {prior}.",
            })

    for pace_key, label in (("rest_of_year", "lo que queda de año"), ("next_year", "el año siguiente")):
        rows = [item[pace_key] for item in result["pace"]]
        if len(rows) < 2:
            continue
        reference = [item["on_books"] for item in rows[:-1] if item["on_books"]]
        if not reference:
            continue
        average = sum(reference) / len(reference)
        change = _change(rows[-1]["on_books"], average)
        if change is not None and change <= -0.1:
            insights.append({
                "severity": "warning",
                "title": f"Ritmo de reservas por detrás para {label}",
                "detail": (
                    f"A esta fecha hay {rows[-1]['on_books']} noches reservadas, "
                    f"{_pct(change)} frente a la media de años anteriores en la misma fecha "
                    f"({average:.0f})."
                ),
            })
        elif change is not None and change >= 0.1:
            insights.append({
                "severity": "positive",
                "title": f"Ritmo de reservas por delante para {label}",
                "detail": (
                    f"Hay {rows[-1]['on_books']} noches reservadas, {_pct(change)} frente a la "
                    f"media de años anteriores en la misma fecha."
                ),
            })

    total_previous = previous["nights"] or 1
    declines = []
    for segment_key, segment_label in (
        ("stay", "Estancias de"), ("guests", "Huéspedes:"), ("channel", "Canal"), ("group", "Grupo"),
    ):
        for item in result["segments"][segment_key]:
            before = item["values"][str(prior)]["nights"]
            after = item["values"][str(year)]["nights"]
            if before >= total_previous * 0.05 and after < before:
                declines.append((after - before, segment_label, item["key"], before, after))
    for lost, segment_label, key, before, after in sorted(declines)[:3]:
        if lost / before > -0.1:
            continue
        insights.append({
            "severity": "warning",
            "title": f"{segment_label} {key}: {lost} noches",
            "detail": (
                f"Pasa de {before} a {after} noches ({_pct(_change(after, before))}) "
                f"en la misma ventana de {prior} a {year}."
            ),
        })

    occupancy = current["occupancy"] or 0
    for item in result["segments"]["group"]:
        group_occupancy = item["values"][str(year)]["occupancy"]
        if occupancy and group_occupancy is not None and group_occupancy < occupancy * 0.5:
            insights.append({
                "severity": "warning",
                "title": f"Bajo rendimiento: {item['key']}",
                "detail": (
                    f"Ocupación del {group_occupancy * 100:.0f} % frente al {occupancy * 100:.0f} % "
                    f"del conjunto ({item['units']} alojamientos)."
                ),
            })

    first = yearly[0]
    inquiries = _change(current["inquiries"], first["inquiries"])
    if inquiries is not None and inquiries <= -0.15:
        insights.append({
            "severity": "warning",
            "title": "Entran menos peticiones de información",
            "detail": (
                f"{current['inquiries']} peticiones en {year} frente a {first['inquiries']} en "
                f"{first['year']} en la misma ventana ({_pct(inquiries)})."
            ),
        })

    gaps = result["orphan_gaps"]
    if gaps["nights"]:
        insights.append({
            "severity": "info",
            "title": "Noches sueltas sin vender",
            "detail": (
                f"{gaps['nights']} noches en {gaps['gaps']} huecos de 1 a {ORPHAN_GAP_NIGHTS} "
                "noches entre estancias durante los últimos 12 meses."
            ),
        })
    return insights


def reservation_analysis(db: Session, years: int = 4) -> dict:
    years = max(MIN_YEARS, min(MAX_YEARS, years))
    reservation_batch = latest_source_batch(db, "reservations")
    property_batch = latest_source_batch(db, "properties")
    if not reservation_batch or not property_batch:
        return {
            "has_data": False,
            "missing": [
                name
                for name, batch in (("reservations", reservation_batch), ("properties", property_batch))
                if not batch
            ],
        }

    properties = {
        prop.property_id: prop
        for prop in db.scalars(
            select(PropertySnapshot).where(PropertySnapshot.batch_id == property_batch.id)
        )
        if _rentable(prop)
    }
    units = len(properties)
    rows = [
        row
        for row in consolidated_reservations(db, reservation_batch).values()
        if row.property_id in properties
    ]
    as_of = reservation_batch.created_at.date()
    year_list = list(range(as_of.year - years + 1, as_of.year + 1))
    windows = {year: (date(year, 1, 1), _same_day(year, as_of)) for year in year_list}

    yearly = []
    for year in year_list:
        start, end = windows[year]
        metrics = _window_metrics(rows, units, start, end)
        arrivals = [r for r in rows if r.status in SOLD_STATUSES and start <= r.start_date < end]
        cancelled = sum(1 for r in rows if r.status == "cancelled" and start <= r.start_date < end)
        long_nights = sum(
            _overlap(r, start, end)
            for r in rows
            if r.status in SOLD_STATUSES and r.nights >= LONG_STAY_NIGHTS
        )
        yearly.append({
            "year": year,
            **metrics,
            "arrivals": len(arrivals),
            "median_stay": _median([r.nights for r in arrivals]),
            "median_lead_days": _median(
                [(r.start_date - r.created_on).days for r in arrivals if r.created_on]
            ),
            "cancellation_rate": _ratio(cancelled, cancelled + len(arrivals)),
            "long_stay_share": _ratio(long_nights, metrics["nights"]),
            "inquiries": sum(
                1 for r in rows if r.status == "inquiry" and r.created_on and start <= r.created_on < end
            ),
            "bookings_created": sum(
                1 for r in rows if r.status in SOLD_STATUSES and r.created_on and start <= r.created_on < end
            ),
        })

    monthly = []
    for year in year_list:
        months = []
        for month in range(1, 13):
            start = date(year, month, 1)
            end = date(year + (month == 12), month % 12 + 1, 1)
            state = "closed" if end <= as_of else "partial" if start <= as_of else "forward"
            months.append({"month": month, "state": state, **_window_metrics(rows, units, start, end)})
        monthly.append({"year": year, "months": months})

    pace = []
    for year in year_list:
        cutoff = _same_day(year, as_of)
        item = {"year": year, "as_of": cutoff.isoformat()}
        for key, start, end in (
            ("rest_of_year", cutoff, date(year + 1, 1, 1)),
            ("next_year", date(year + 1, 1, 1), date(year + 2, 1, 1)),
        ):
            on_books = sum(
                _overlap(r, start, end)
                for r in rows
                if r.status in SOLD_STATUSES and r.created_on and r.created_on <= cutoff
            )
            final = (
                _window_metrics(rows, units, start, end)["nights"] if end <= as_of else None
            )
            item[key] = {
                "on_books": on_books,
                "occupancy": _ratio(on_books, units * (end - start).days),
                "final": final,
            }
        pace.append(item)

    group_units: dict[str, int] = defaultdict(int)
    group_of: dict[str, str] = {}
    for prop in properties.values():
        label = f"{prop.building or 'Sin edificio'} · {prop.capacity} pers."
        group_of[prop.property_id] = label
        group_units[label] += 1

    segments = {
        "group": _segment(
            rows, year_list, windows, lambda r: group_of[r.property_id],
            sorted(group_units), dict(group_units),
        ),
        "channel": _segment(rows, year_list, windows, lambda r: channel_of(r.portal)),
        "stay": _segment(rows, year_list, windows, lambda r: stay_bucket(r.nights), SEGMENT_ORDER["stay"]),
        "guests": _segment(rows, year_list, windows, guest_bucket, SEGMENT_ORDER["guests"]),
        "lead": _segment(rows, year_list, windows, lead_bucket, SEGMENT_ORDER["lead"]),
    }

    past_sold = [r for r in rows if r.status in SOLD_STATUSES and r.end_date <= as_of]
    unpaid = [r for r in past_sold if r.pending > 0]
    quality = {
        "stale_inquiries": sum(1 for r in rows if r.status == "inquiry" and r.end_date <= as_of),
        "unpaid_past_stays": len(unpaid),
        "unpaid_past_amount": round(float(sum(r.pending for r in unpaid)), 2),
        "confirmed_past_stays": sum(1 for r in past_sold if r.status == "confirmed"),
        "over_capacity": sum(
            1
            for r in rows
            if r.status in SOLD_STATUSES and r.adults + r.children > properties[r.property_id].capacity
        ),
        "zero_value_sold": sum(1 for r in rows if r.status in SOLD_STATUSES and r.net <= 0),
    }

    result = {
        "has_data": True,
        "as_of": as_of.isoformat(),
        "units": units,
        "years": year_list,
        "window": {"start": "01-01", "end": as_of.strftime("%m-%d")},
        "yearly": yearly,
        "monthly": monthly,
        "pace": pace,
        "segments": segments,
        "orphan_gaps": _orphan_gaps(rows, as_of - timedelta(days=365), as_of),
        "data_quality": quality,
    }
    result["insights"] = _insights(result)
    return result
