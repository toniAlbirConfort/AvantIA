from __future__ import annotations

import csv
import hashlib
import io
from datetime import UTC, date, datetime, timedelta

from app.availability import check_availability, import_csv
from app.models import ImportBatch, PropertySnapshot, ReservationSnapshot
from app.planning import commit_import, parse_export, planning_view, preview_import

PROPERTY_HEADERS = [
    "Código",
    "Estado",
    "Alojamiento",
    "Clase",
    "Capacidad personas",
    "Edificio",
    "Población",
    "Información adicional del alojamiento",
    "Propietario",
]
RESERVATION_HEADERS = [
    "Localizador",
    "Fecha alta",
    "Estado",
    "Fecha entrada",
    "Fecha salida",
    "noches",
    "Total reserva con tasas",
    "Total reserva sin tasas",
    "Total tasas",
    "Pagado",
    "Pendiente",
    "Adultos",
    "Niños",
    "Bebés",
    "id alojamiento",
    "Cliente: Nombre",
    "Cliente: Apellidos",
    "Ocupante: Nombre",
    "Ocupante: Apellidos",
    "Portal",
    "Cliente: E-mail",
    "Cliente: Número documento",
    "Mis comentarios",
]


def export(title: str, headers: list[str], rows: list[list[str | int]], delimiter: str) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=delimiter)
    writer.writerow([title])
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue().encode()


def properties() -> bytes:
    return export(
        "Listado de propiedades",
        PROPERTY_HEADERS,
        [
            [
                "A",
                "Activado (desde el 01/01/2020)",
                "Casa Azul",
                "Apartamento",
                4,
                "Centro",
                "Albir",
                "Código puerta secreto",
                "Propietario privado",
            ],
            [
                "B",
                "Desactivado (desde el 01/01/2020)",
                "Garaje",
                "Garaje/Parking",
                1,
                "Centro",
                "Albir",
                "",
                "",
            ],
        ],
        ";",
    )


def reservation(
    locator: str,
    property_id: str,
    status: str,
    start: date,
    end: date,
    paid: int = 0,
    pending: int = 110,
    comment: str = "",
    created: date | None = None,
) -> list:
    return [
        locator,
        (created or start).strftime("%d/%m/%Y"),
        status,
        start.strftime("%d/%m/%Y"),
        end.strftime("%d/%m/%Y"),
        (end - start).days,
        "110.00",
        "100.00",
        "10.00",
        paid,
        pending,
        2,
        0,
        0,
        property_id,
        "Cliente",
        "Prueba",
        "Ocupante",
        "Ejemplo",
        "WEB",
        "privado@example.test",
        "DOC-SECRET",
        comment,
    ]


def reservations(rows: list[list]) -> bytes:
    return export("Listado de reservas", RESERVATION_HEADERS, rows, ",")


def commit(db, dataset: str, raw: bytes):
    result = commit_import(db, dataset, raw, f"{dataset}.csv", 1, hashlib.sha256(raw).hexdigest())
    assert result.status == "valid"
    return result


def test_parser_handles_multiline_and_drops_private_fields(db):
    start = date.today() + timedelta(days=10)
    raw = reservations(
        [
            reservation(
                "R1",
                "A",
                "Confirmada",
                start,
                start + timedelta(days=2),
                comment="línea uno\nlínea dos",
            ),
            reservation(
                "R2",
                "A",
                "Petición Información",
                start + timedelta(days=3),
                start + timedelta(days=4),
            ),
        ]
    )
    parsed = parse_export(raw, "reservations")
    assert len(parsed) == 2
    assert parsed[0]["source_row"] == 3
    assert parsed[1]["source_row"] == 5
    assert (
        not {"Cliente: E-mail", "Cliente: Número documento", "Mis comentarios"} & parsed[0].keys()
    )
    assert b"privado@example.test" in raw
    commit(db, "properties", properties())
    commit(db, "reservations", raw)
    stored = db.query(ReservationSnapshot).first()
    assert "email" not in stored.__dict__
    assert "comment" not in stored.__dict__
    assert "Código puerta secreto" not in str(db.query(PropertySnapshot).first().__dict__)
    view = planning_view(db, start, start + timedelta(days=5))
    assert "privado@example.test" not in str(view)
    assert "DOC-SECRET" not in str(view)


def test_snapshot_diff_history_idempotence_and_invalid_fallback(db):
    start = date.today() + timedelta(days=10)
    commit(db, "properties", properties())
    first_raw = reservations(
        [
            reservation("R1", "A", "Confirmada", start, start + timedelta(days=2)),
            reservation(
                "R2",
                "A",
                "Petición Información",
                start + timedelta(days=3),
                start + timedelta(days=4),
            ),
        ]
    )
    first = commit(db, "reservations", first_raw)
    assert commit(db, "reservations", first_raw).id == first.id
    second_raw = reservations(
        [reservation("R1", "A", "Prereserva", start, start + timedelta(days=2))]
    )
    preview = preview_import(db, "reservations", second_raw)
    assert preview["coverage"]["first_arrival"] == start
    assert preview["coverage"]["last_departure"] == start + timedelta(days=2)
    assert preview["changes"] == [
        {"key": "R1", "kind": "changed", "fields": ["status_original", "status"]},
    ]
    assert preview["effective_rows"] == 2
    assert preview["carried_rows"] == 1
    second = commit(db, "reservations", second_raw)
    historical = planning_view(db, start, start + timedelta(days=3), reservation_batch_id=first.id)
    current = planning_view(db, start, start + timedelta(days=3))
    assert historical["bars"][0]["status"] == "confirmed"
    assert historical["inquiries"] == []  # outside this three-day window
    assert current["bars"][0]["status"] == "pre_reservation"
    assert {row.locator for row in db.query(ReservationSnapshot).filter_by(batch_id=second.id)} == {
        "R1", "R2"
    }
    assert db.query(ReservationSnapshot).filter_by(locator="R1", batch_id=second.id).one().source_batch_id == second.id
    assert current["reservation_coverage"]["first_arrival"] == start
    assert current["reservations_in_range"] == 1
    empty_range = planning_view(db, start - timedelta(days=3), start - timedelta(days=1))
    assert empty_range["reservations_in_range"] == 0
    assert empty_range["reservation_coverage"]["first_arrival"] == start
    bad = b"Listado de reservas\nLocalizador,Estado\nR2,Confirmada"
    rejected = commit_import(db, "reservations", bad, "bad.csv", 1, hashlib.sha256(bad).hexdigest())
    assert rejected.status == "invalid"
    assert (
        planning_view(db, start, start + timedelta(days=3))["reservation_batch"]["id"] == second.id
    )
    assert db.query(ImportBatch).count() == 4
    assert preview_import(db, "reservations", first_raw)["duplicate_of"] is None
    restored = commit(db, "reservations", first_raw)
    assert restored.id not in {first.id, second.id}
    assert planning_view(db, start, start + timedelta(days=3))["bars"][0]["status"] == "confirmed"


def test_warnings_and_nonblocking_inquiries(db):
    start = date.today() + timedelta(days=10)
    commit(db, "properties", properties())
    raw = reservations(
        [
            reservation("C1", "A", "Confirmada", start, start + timedelta(days=3)),
            reservation(
                "P1", "A", "Prereserva", start + timedelta(days=2), start + timedelta(days=4)
            ),
            reservation(
                "I1",
                "A",
                "Petición Información",
                start + timedelta(days=4),
                start + timedelta(days=5),
            ),
            reservation("G1", "B", "Confirmada", start, start + timedelta(days=1), pending=100),
        ]
    )
    batch = commit(db, "reservations", raw)
    codes = {issue["code"] for issue in batch.warnings}
    assert {"overlap", "inactive_property", "balance_mismatch"} <= codes
    view = planning_view(db, start, start + timedelta(days=6))
    assert {bar["locator"] for bar in view["bars"]} == {"C1", "P1"}
    assert {item["locator"] for item in view["inquiries"]} == {"I1"}
    assert {item["id"] for item in view["properties"]} == {"A"}
    assert "inactive_property" not in {issue["code"] for issue in view["warnings"]}
    detail = view["bars"][0]
    assert {
        "created_on", "nights", "adults", "children", "infants", "net", "tax", "paid", "pending"
    } <= detail.keys()
    assert detail["portal"] == "WEB"
    assert not {"source_row", "source_batch_id", "batch_id"} & detail.keys()
    stored_reservation = db.query(ReservationSnapshot).filter_by(locator="G1").one()
    assert stored_reservation.source_row > 0
    assert stored_reservation.source_batch_id == batch.id


def test_reservations_block_even_when_availability_says_free(db):
    start = date.today() + timedelta(days=10)
    commit(db, "properties", properties())
    commit(
        db,
        "reservations",
        reservations(
            [
                reservation("C1", "A", "Confirmada", start, start + timedelta(days=2)),
                reservation(
                    "P1", "A", "Prereserva", start + timedelta(days=2), start + timedelta(days=4)
                ),
                reservation(
                    "I1",
                    "A",
                    "Petición Información",
                    start + timedelta(days=4),
                    start + timedelta(days=5),
                ),
            ]
        ),
    )
    import_csv(
        db,
        raw=(
            "property_id,property_name,capacity,start_date,end_date,status\n"
            f"A,Casa Azul,4,{start},{start + timedelta(days=7)},available"
        ).encode(),
        filename="availability.csv",
        source="upload",
    )

    def status(a: int, b: int) -> str:
        return check_availability(
            db,
            property_query="A",
            arrival=str(start + timedelta(days=a)),
            departure=str(start + timedelta(days=b)),
            guests=2,
        ).status

    assert status(0, 1) == "unavailable"
    assert status(2, 3) == "unavailable"
    assert status(4, 5) == "available"  # inquiry is visible but does not block
    assert status(5, 6) == "available"  # checkout boundary is exclusive
    reservation_batch = db.query(ImportBatch).filter_by(dataset="reservations").one()
    reservation_batch.created_at = datetime.now(UTC) - timedelta(hours=7)
    db.commit()
    assert status(5, 6) == "manual_check"


def test_segmented_imports_preserve_old_year_and_track_each_source(db):
    start = date.today() + timedelta(days=10)
    commit(db, "properties", properties())
    old = commit(
        db,
        "reservations",
        reservations(
            [reservation("OLD", "A", "Pagada", start, start + timedelta(days=2),
                         paid=110, pending=0, created=date(2025, 7, 1))]
        ),
    )
    new = commit(
        db,
        "reservations",
        reservations(
            [reservation("NEW", "A", "Confirmada", start + timedelta(days=2),
                         start + timedelta(days=4), created=date(2026, 3, 1))]
        ),
    )
    current = planning_view(db, start, start + timedelta(days=5))
    assert {
        locator: db.query(ReservationSnapshot)
        .filter_by(locator=locator, batch_id=new.id)
        .one()
        .source_batch_id
        for locator in ("OLD", "NEW")
    } == {"OLD": old.id, "NEW": new.id}
    assert current["reservation_coverage"]["effective_rows"] == 2
    assert {item["locator"] for item in planning_view(
        db, start, start + timedelta(days=5), reservation_batch_id=old.id
    )["bars"]} == {"OLD"}
    assert db.query(ReservationSnapshot).filter_by(batch_id=new.id).count() == 2
    db.query(ReservationSnapshot).filter_by(batch_id=new.id, locator="OLD").delete()
    db.commit()  # Simulate an older non-consolidated import that omitted OLD.
    assert {item["locator"] for item in planning_view(
        db, start, start + timedelta(days=5)
    )["bars"]} == {"OLD", "NEW"}

    cancelled = commit(
        db, "reservations",
        reservations(
            [reservation("OLD", "A", "Cancelada", start, start + timedelta(days=2),
                         created=date(2025, 7, 1))]
        ),
    )
    view = planning_view(db, start, start + timedelta(days=5))
    assert {item["locator"] for item in view["bars"]} == {"NEW"}
    assert {item["locator"] for item in view["cancellations"]} == {"OLD"}
    assert db.query(ReservationSnapshot).filter_by(locator="OLD", batch_id=cancelled.id).one().source_batch_id == cancelled.id


def test_paid_owner_unavailable_block_and_cancelled_does_not(db):
    start = date.today() + timedelta(days=10)
    commit(db, "properties", properties())
    commit(
        db, "reservations",
        reservations([
            reservation("PAID", "A", "Pagada", start, start + timedelta(days=1),
                        paid=110, pending=0),
            reservation("OWNER", "A", "De Propietario", start + timedelta(days=1),
                        start + timedelta(days=2)),
            reservation("BLOCK", "A", "No Disponible", start + timedelta(days=2),
                        start + timedelta(days=3)),
            reservation("CANCEL", "A", "Cancelada", start + timedelta(days=3),
                        start + timedelta(days=4)),
        ]),
    )
    view = planning_view(db, start, start + timedelta(days=5))
    assert {item["locator"] for item in view["bars"]} == {"PAID", "OWNER", "BLOCK"}
    assert view["cancellations"][0]["locator"] == "CANCEL"
    assert next(item for item in view["bars"] if item["locator"] == "OWNER")[
        "display_name"
    ] == "Uso de propietario"
    import_csv(
        db,
        raw=(
            "property_id,property_name,capacity,start_date,end_date,status\n"
            f"A,Casa Azul,4,{start},{start + timedelta(days=5)},available"
        ).encode(),
        filename="availability.csv",
        source="upload",
    )
    for offset in range(3):
        assert check_availability(
            db, property_query="A", arrival=str(start + timedelta(days=offset)),
            departure=str(start + timedelta(days=offset + 1)), guests=2,
        ).status == "unavailable"
    assert check_availability(
        db, property_query="A", arrival=str(start + timedelta(days=3)),
        departure=str(start + timedelta(days=4)), guests=2,
    ).status == "available"
