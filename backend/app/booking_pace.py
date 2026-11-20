"""Booking pace, weeks that lost bookings and the price strategy followed so far.

Read-only comparison for the tariff laboratory. For each SA group it reports:

* the nights of next year already booked today against the nights of this year that
  were booked on the same date one year earlier, per month;
* the weeks of the current year whose occupancy fell well below the average of the
  three previous years, with a starting-point offer;
* how prices have actually evolved: the median price paid per night in the same
  dates every year, the SA tariffs of next year and, if any, the latest projection.

Nothing here changes tariffs or writes to Avantio.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TariffProjection, TariffSeason
from app.tariff_lab import (
    BOOKABLE_STATUSES,
    NON_SELLABLE_STATUSES,
    _current_properties,
    _effective_reservations,
    _property_mappings,
)
from app.tariff_projection import (
    LONG_STAY_NIGHTS,
    PACE_BEHIND,
    PACE_SLIGHTLY_BEHIND,
    final_seasons,
    shift_date,
)

COMPARISON_YEARS = 3
ANNUAL_YEARS = 8
MIN_SEASON_NIGHTS = 14
MIN_ANNUAL_NIGHTS = 30
PACE_MIN_NIGHTS = 30
DEFAULT_DROP_THRESHOLD = 0.15
# Starting-point offers by drop in occupancy points; reviewed by a person, never applied.
OFFER_RULES = (
    (0.30, 0.10, "Oferta −10 % o estancia mínima más corta"),
    (0.20, 0.07, "Oferta −7 %"),
    (0.0, 0.05, "Oferta −5 % o noche gratis por 7"),
)
ALL_GROUPS = "Todos"


def _days(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def _offer(drop: float) -> dict:
    for minimum, discount, label in OFFER_RULES:
        if drop >= minimum:
            return {"discount": discount, "label": label}
    return {"discount": OFFER_RULES[-1][1], "label": OFFER_RULES[-1][2]}


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    first = date(year, month, 1)
    following = date(year + (month == 12), month % 12 + 1, 1)
    return first, following - timedelta(days=1)


def _median(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return Decimal(statistics.median(values)).quantize(Decimal("0.01"))


def _weighted_rate(rows: list[tuple[date, date, Decimal]]) -> str | None:
    weight = sum((end - start).days + 1 for start, end, _ in rows)
    if not weight:
        return None
    total = sum(rate * ((end - start).days + 1) for start, end, rate in rows)
    return f"{total / weight:.2f}"


def booking_pace_view(
    db: Session, today: date | None = None, threshold: float = DEFAULT_DROP_THRESHOLD
) -> dict:
    today = today or date.today()
    year = today.year
    next_year = year + 1
    first_year = year - ANNUAL_YEARS + 1
    active = {item.property_id for item in _current_properties(db)}
    group_of = {
        property_id: mapping.tariff_name
        for property_id, mapping in _property_mappings(db).items()
        if property_id in active
    }
    groups = sorted(set(group_of.values()))
    units: dict[str, int] = defaultdict(int)
    for group in group_of.values():
        units[group] += 1

    sold: dict[tuple[str, date], int] = defaultdict(int)
    blocked: dict[tuple[str, date], int] = defaultdict(int)
    prices: dict[tuple[str, date], list[Decimal]] = defaultdict(list)
    bookings = []
    window_start = date(first_year, 1, 1)
    for row in _effective_reservations(db):
        group = group_of.get(row.property_id)
        if group is None or row.end_date <= window_start or row.nights <= 0:
            continue
        if row.status in BOOKABLE_STATUSES:
            bookings.append((group, row))
            nightly = Decimal(row.total) / row.nights
            for day in _days(max(row.start_date, window_start), row.end_date - timedelta(days=1)):
                sold[(group, day)] += 1
                if row.nights < LONG_STAY_NIGHTS and row.total > 0:
                    prices[(group, day)].append(nightly)
        elif row.status in NON_SELLABLE_STATUSES:
            for day in _days(max(row.start_date, window_start), row.end_date - timedelta(days=1)):
                blocked[(group, day)] += 1

    def occupancy(selected: list[str], start: date, end: date) -> float | None:
        capacity = nights = 0
        for day in _days(start, end):
            for group in selected:
                capacity += max(units[group] - blocked[(group, day)], 0)
                nights += sold[(group, day)]
        return round(nights / capacity, 3) if capacity else None

    def median_price(group: str, start: date, end: date, minimum: int) -> dict | None:
        values = [
            value
            for day in _days(start, min(end, today - timedelta(days=1)))
            for value in prices[(group, day)]
        ]
        if len(values) < minimum:
            return None
        return {"rate": f"{_median(values):.2f}", "nights": len(values)}

    seasons_next: dict[str, list[tuple[date, date, Decimal]]] = defaultdict(list)
    for row in db.scalars(
        select(TariffSeason).where(
            TariffSeason.rate_plan == "SA",
            TariffSeason.start_date <= date(next_year, 12, 31),
            TariffSeason.end_date >= date(next_year, 1, 1),
        )
    ):
        seasons_next[row.tariff_name].append(
            (
                max(row.start_date, date(next_year, 1, 1)),
                min(row.end_date, date(next_year, 12, 31)),
                Decimal(row.nightly_rate),
            )
        )
    projection = db.scalar(
        select(TariffProjection)
        .where(TariffProjection.base_year == next_year)
        .order_by(TariffProjection.id.desc())
    )
    projected: dict[str, list[tuple[date, date, Decimal]]] = defaultdict(list)
    if projection is not None:
        for item in final_seasons(projection):
            projected[item["tariff_name"]].append(
                (
                    date.fromisoformat(item["start"]),
                    date.fromisoformat(item["end"]),
                    Decimal(item["rate"]),
                )
            )

    def rate_on(rows: list[tuple[date, date, Decimal]], day: date) -> str | None:
        return next((f"{rate:.2f}" for start, end, rate in rows if start <= day <= end), None)

    def booked(selected: set[str], stay_year: int, month: int, cutoff: date) -> int:
        first, last = _month_bounds(stay_year, month)
        nights = 0
        for group, row in bookings:
            if group not in selected or row.created_on is None or row.created_on > cutoff:
                continue
            start, end = max(row.start_date, first), min(row.end_date - timedelta(days=1), last)
            if start <= end:
                nights += (end - start).days + 1
        return nights

    comparison_years = list(range(year - COMPARISON_YEARS, year))
    cutoff_previous = shift_date(today, year - 1)
    result_groups: dict[str, dict] = {}
    for key in [ALL_GROUPS, *groups]:
        selected = groups if key == ALL_GROUPS else [key]
        weeks = []
        for week in range(1, 53):
            monday = date.fromisocalendar(year, week, 1)
            sunday = monday + timedelta(days=6)
            current = occupancy(selected, monday, sunday) if sunday < today else None
            previous = [
                occupancy(
                    selected,
                    date.fromisocalendar(item, week, 1),
                    date.fromisocalendar(item, week, 7),
                )
                for item in comparison_years
            ]
            known = [value for value in previous if value is not None]
            average = round(statistics.mean(known), 3) if len(known) == len(previous) else None
            entry = {
                "week": week,
                "start": monday.isoformat(),
                "end": sunday.isoformat(),
                "occupancy": current,
                "average": average,
                "drop": None,
                "offer": None,
            }
            if current is not None and average is not None and current < average - threshold:
                entry["drop"] = round(average - current, 3)
                entry["offer"] = _offer(average - current)
            if key != ALL_GROUPS:
                middle_next = date.fromisocalendar(next_year, week, 3)
                entry["real_price"] = (median_price(key, monday, sunday, 5) or {}).get("rate")
                entry["sa_next"] = rate_on(seasons_next[key], middle_next)
                entry["projected"] = rate_on(
                    projected[key], date.fromisocalendar(next_year + 1, week, 3)
                )
                if entry["offer"] and entry["sa_next"]:
                    discount = Decimal(str(entry["offer"]["discount"]))
                    entry["offer"]["rate"] = f"{Decimal(entry['sa_next']) * (1 - discount):.2f}"
            weeks.append(entry)

        selected_set = set(selected)
        months = []
        for month in range(1, 13):
            now = booked(selected_set, next_year, month, today)
            before = booked(selected_set, year, month, cutoff_previous)
            first, last = _month_bounds(year, month)
            final = sum(sold[(group, day)] for group in selected for day in _days(first, last))
            change = round(now / before - 1, 3) if before else None
            if before < PACE_MIN_NIGHTS:
                status = "little_reference"
            elif change <= PACE_BEHIND:
                status = "behind"
            elif change <= PACE_SLIGHTLY_BEHIND:
                status = "slightly_behind"
            else:
                status = "on_track"
            months.append(
                {
                    "month": month,
                    "booked": now,
                    "booked_previous": before,
                    "final_previous": final,
                    "change": change,
                    "status": status,
                }
            )

        info: dict = {
            "units": sum(units[group] for group in selected),
            "weeks": weeks,
            "pace": months,
        }
        if key != ALL_GROUPS:
            annual = {}
            for item in range(first_year, year + 1):
                value = median_price(key, date(item, 1, 1), date(item, 12, 31), MIN_ANNUAL_NIGHTS)
                if value:
                    annual[str(item)] = value["rate"]
            base_growth = annual.get(str(year - 4))
            current_rate = annual.get(str(year))
            info["annual"] = annual
            info["growth_4y"] = (
                round((float(current_rate) / float(base_growth)) ** 0.25 - 1, 3)
                if base_growth and current_rate
                else None
            )
            yearly: dict[str, list[float]] = defaultdict(list)
            factors = []
            for start, end, rate in seasons_next[key]:
                by_year = {
                    item: median_price(
                        key,
                        shift_date(start, item),
                        shift_date(end, item, is_end=True),
                        MIN_SEASON_NIGHTS,
                    )
                    for item in range(year - 3, year + 1)
                }
                for item in range(year - 2, year + 1):
                    if by_year[item] and by_year[item - 1]:
                        yearly[str(item)].append(
                            float(by_year[item]["rate"]) / float(by_year[item - 1]["rate"]) - 1
                        )
                if by_year[year] and by_year[year]["nights"] >= MIN_ANNUAL_NIGHTS:
                    factors.append(float(rate) / float(by_year[year]["rate"]))
            info["yearly_change"] = {
                str(item): round(statistics.median(yearly[str(item)]), 3)
                if len(yearly[str(item)]) >= 3
                else None
                for item in range(year - 2, year + 1)
            }
            info["sa_next_vs_real"] = round(statistics.median(factors), 3) if factors else None
            info["sa_next_average"] = _weighted_rate(seasons_next[key])
            info["projected_average"] = _weighted_rate(projected[key])
        result_groups[key] = info

    return {
        "as_of": today.isoformat(),
        "year": year,
        "next_year": next_year,
        "comparison_years": comparison_years,
        "threshold": threshold,
        "offer_rules": [
            {"min_drop": minimum, "discount": discount, "label": label}
            for minimum, discount, label in OFFER_RULES
        ],
        "projection": {
            "id": projection.id,
            "name": projection.name,
            "target_year": projection.target_year,
        }
        if projection is not None
        else None,
        "groups": result_groups,
    }
