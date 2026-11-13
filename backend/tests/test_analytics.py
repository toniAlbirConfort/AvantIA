from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from types import SimpleNamespace

from app.analytics import channel_of, conversion_funnel, reservation_analysis, stay_bucket
from app.models import ImportBatch, PropertySnapshot, ReservationSnapshot

AS_OF = datetime(2026, 3, 1, 9, tzinfo=UTC)


def batch(db, dataset: str, created_at: datetime = AS_OF) -> ImportBatch:
    item = ImportBatch(
        dataset=dataset, source="upload", filename=f"{dataset}.csv", checksum=dataset * 4,
        status="valid", row_count=0, created_at=created_at,
    )
    db.add(item)
    db.flush()
    return item


def prop(db, batch_id: int, property_id: str, *, active=True, category="Apartamento", capacity=4):
    db.add(PropertySnapshot(
        batch_id=batch_id, source_row=1, row_hash=property_id, property_id=property_id,
        name=f"Apto {property_id}", status_original="Activado", active=active, category=category,
        capacity=capacity, building="Bloque A", city="Albir",
    ))


def reservation(
    db, batch_id: int, locator: str, property_id: str, start: date, end: date, *,
    status="paid", net="1000", created=None, portal="CANAL - EMAIL", adults=2, children=0,
    pending="0",
):
    db.add(ReservationSnapshot(
        batch_id=batch_id, source_row=1, row_hash=locator, locator=locator,
        property_id=property_id, status_original=status, status=status,
        created_on=created or date(start.year - 1, 12, 1), start_date=start, end_date=end,
        nights=(end - start).days, adults=adults, children=children, infants=0,
        display_name="Huésped", portal=portal, total=Decimal(net), net=Decimal(net),
        tax=Decimal("0"), paid=Decimal(net), pending=Decimal(pending),
    ))


def setup_catalog(db) -> ImportBatch:
    properties = batch(db, "properties")
    prop(db, properties.id, "A")
    prop(db, properties.id, "B")
    prop(db, properties.id, "OLD", active=False)
    prop(db, properties.id, "G", category="Garaje/Parking", capacity=1)
    return properties


def test_without_imports_reports_missing_sources(db):
    assert reservation_analysis(db) == {"has_data": False, "missing": ["reservations", "properties"]}


def test_years_are_compared_over_the_same_window(db):
    setup_catalog(db)
    reservations = batch(db, "reservations")
    # 2025: 10 nights at 100 € inside the window, plus nights after 1 March that must not count.
    reservation(db, reservations.id, "R1", "A", date(2025, 2, 1), date(2025, 2, 11), net="1000")
    reservation(db, reservations.id, "R2", "B", date(2025, 2, 25), date(2025, 3, 5), net="800")
    # 2026: 5 nights at 200 €.
    reservation(db, reservations.id, "R3", "A", date(2026, 2, 1), date(2026, 2, 6), net="1000")
    # Inactive unit, garage and cancellations are excluded from sold nights.
    reservation(db, reservations.id, "R4", "OLD", date(2026, 1, 5), date(2026, 1, 20))
    reservation(db, reservations.id, "R5", "G", date(2026, 1, 5), date(2026, 1, 20))
    reservation(db, reservations.id, "R6", "B", date(2026, 1, 5), date(2026, 1, 8), status="cancelled")
    db.commit()

    result = reservation_analysis(db, years=2)

    assert result["has_data"] and result["units"] == 2 and result["as_of"] == "2026-03-01"
    y2025, y2026 = result["yearly"]
    assert y2025["year"] == 2025 and y2025["nights"] == 14  # 10 + 4 nights before 1 March
    assert y2025["revenue"] == 1400.0 and y2025["adr"] == 100.0
    assert y2025["occupancy"] == round(14 / (2 * 59), 4)
    assert y2026["nights"] == 5 and y2026["adr"] == 200.0
    assert y2026["cancellation_rate"] == 0.5
    february_2025 = result["monthly"][0]["months"][1]
    assert february_2025["state"] == "closed" and february_2025["nights"] == 14
    assert result["monthly"][1]["months"][2]["state"] == "partial"
    assert result["monthly"][1]["months"][5]["state"] == "forward"
    titles = [item["title"] for item in result["insights"]]
    assert "Más precio, menos noches vendidas" in titles


def test_newest_import_of_a_locator_wins(db):
    setup_catalog(db)
    first = batch(db, "reservations", datetime(2026, 2, 1, tzinfo=UTC))
    reservation(db, first.id, "R1", "A", date(2026, 1, 10), date(2026, 1, 20))
    second = batch(db, "reservations")
    reservation(db, second.id, "R1", "A", date(2026, 1, 10), date(2026, 1, 20), status="cancelled")
    db.commit()

    assert reservation_analysis(db, years=2)["yearly"][-1]["nights"] == 0


def test_pace_counts_only_bookings_created_by_the_same_date(db):
    setup_catalog(db)
    reservations = batch(db, "reservations")
    # Booked before 1 March 2025 for next year; booked later in 2025 for next year.
    reservation(db, reservations.id, "P1", "A", date(2026, 1, 10), date(2026, 1, 20), created=date(2025, 2, 1))
    reservation(db, reservations.id, "P2", "B", date(2026, 1, 10), date(2026, 1, 15), created=date(2025, 6, 1))
    reservation(db, reservations.id, "P3", "A", date(2027, 1, 10), date(2027, 1, 13), created=date(2026, 2, 1))
    db.commit()

    pace = reservation_analysis(db, years=2)["pace"]
    assert pace[0]["next_year"] == {"on_books": 10, "occupancy": round(10 / 730, 4), "final": None}
    assert pace[1]["next_year"]["on_books"] == 3


def test_segments_and_data_quality(db):
    setup_catalog(db)
    reservations = batch(db, "reservations")
    reservation(db, reservations.id, "L1", "A", date(2026, 1, 1), date(2026, 2, 5), portal="BOOKING.COM")
    reservation(
        db, reservations.id, "L2", "B", date(2026, 1, 1), date(2026, 1, 4), adults=4, children=1,
        pending="50",
    )
    reservation(db, reservations.id, "I1", "B", date(2025, 6, 1), date(2025, 6, 5), status="inquiry")
    db.commit()

    result = reservation_analysis(db, years=2)
    stays = {item["key"]: item for item in result["segments"]["stay"]}
    assert stays["28 noches o más"]["values"]["2026"]["nights"] == 35
    assert stays["1-3 noches"]["values"]["2026"]["nights"] == 3
    channels = {item["key"] for item in result["segments"]["channel"]}
    assert channels == {"Booking", "Directo"}
    group = result["segments"]["group"][0]
    assert group["key"] == "Bloque A · 4 pers." and group["units"] == 2
    quality = result["data_quality"]
    assert quality["stale_inquiries"] == 1
    assert quality["unpaid_past_stays"] == 1 and quality["unpaid_past_amount"] == 50.0
    assert quality["over_capacity"] == 1


def test_channel_and_stay_buckets():
    assert channel_of("CANAL - RECEPCION") == "Directo"
    assert channel_of("") == "Directo"
    assert channel_of("HOTELBEDS PRODUCTS,S.L.U.") == "Expedia / Hotelbeds"
    assert channel_of("Airbnb.com") == "Airbnb"
    assert channel_of("VIAJES OLYMPIA") == "Agencias y otros"
    assert stay_bucket(3) == "1-3 noches" and stay_bucket(28) == "28 noches o más"


def test_conversion_funnel_matches_the_avantio_report():
    """Figures from the report Albir Confort builds by hand from Avantio."""
    counts = {
        "owner": 7, "confirmed": 76, "pre_reservation": 3, "paid": 312, "inquiry": 11,
        "dismissed": 167, "cancelled": 56, "unavailable": 2, "abandoned_cart": 1,
    }
    rows = [
        SimpleNamespace(status=status, created_on=date(2026, 5, 1))
        for status, count in counts.items()
        for _ in range(count)
    ]
    rows.append(SimpleNamespace(status="paid", created_on=date(2025, 12, 31)))
    rows.append(SimpleNamespace(status="paid", created_on=None))

    funnel = conversion_funnel(rows, [2025, 2026], date(2026, 11, 13))
    current = funnel[1]
    assert current["total"] == 635 and current["partial"] is True
    assert current["by_status"]["dismissed"] == 167
    assert {name: group["count"] for name, group in current["groups"].items()} == {
        "converted": 391, "pending": 11, "lost": 223, "other": 10,
    }
    assert current["groups"]["converted"]["share"] == 0.6157
    assert sum(group["count"] for group in current["groups"].values()) == current["total"]
    assert current["same_window"]["total"] == 635
    previous = funnel[0]
    assert previous["total"] == 1 and previous["partial"] is False
    assert previous["same_window"]["total"] == 0


def test_conversion_counts_requests_outside_the_rentable_catalogue(db):
    setup_catalog(db)
    reservations = batch(db, "reservations")
    created = date(2026, 2, 1)
    reservation(db, reservations.id, "R1", "A", date(2026, 6, 1), date(2026, 6, 5), created=created)
    reservation(
        db, reservations.id, "R2", "OLD", date(2026, 6, 1), date(2026, 6, 5),
        status="inquiry", created=created,
    )
    db.commit()

    current = reservation_analysis(db)["conversion"][-1]
    assert current["total"] == 2
    assert current["groups"]["converted"]["share"] == 0.5
