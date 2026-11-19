from __future__ import annotations

import csv
import hashlib
import io
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select

from app import notifications, reservation_issues
from app.models import (
    ImportBatch,
    Notification,
    PropertySnapshot,
    ReservationDetail,
    ReservationSnapshot,
)
from app.planning import commit_import, parse_details

TODAY = date(2026, 11, 19)
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
BASE = [
    "Localizador", "Fecha alta", "Estado", "Fecha entrada", "Fecha salida", "noches",
    "Total reserva con tasas", "Total reserva sin tasas", "Total tasas", "Pagado", "Pendiente",
    "Adultos", "Niños", "Bebés", "id alojamiento", "Cliente: Nombre", "Cliente: Apellidos",
    "Ocupante: Nombre", "Ocupante: Apellidos", "Portal",
]  # fmt: skip
EXTRA = [
    "Alquiler con tasas", "Extras con tasas", "Comisión Portal/Intermediario: Comisión calculada",
    "Factura", "Fecha factura", "Total factura", "Localizador agente", "Cliente: Id Cliente",
    "Cliente: E-mail", "Ocupante: E-mail", "Atendido por", "Mis comentarios",
    "Cliente: Número documento", "Cliente: Teléfono",
]  # fmt: skip


def export(rows: list[list], extra: bool = True) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(["Listado de reservas"])
    writer.writerow(BASE + (EXTRA if extra else []))
    writer.writerows(rows)
    return buffer.getvalue().encode()


def base(locator: str, total="3288.73", paid="0", pending="4110.91") -> list:
    return [
        locator, "16/08/2026", "Confirmada", "21/10/2026", "21/11/2026", 31,
        total, "2989.75", "298.98", paid, pending, 2, 0, 0, "A",
        "Ann", "Boe", "Ann", "Boe", "VIAJES SIDETOURS, S.L.",
    ]  # fmt: skip


def extra(comment="", commission="0", email="Ann.Boe@Example.test") -> list:
    return [
        "3288.73", "0", commission, "A26/1, A26/2", "01/10/2026 15/10/2026", "990.85",
        "1016015", "1617163", email, "", "Amapola", comment, "DOC-SECRET", "+4712345678",
    ]  # fmt: skip


def commit(db, raw: bytes):
    batch = commit_import(db, "reservations", raw, "r.csv", 1, hashlib.sha256(raw).hexdigest())
    assert batch.status == "valid", batch.error
    return batch


def test_details_are_stored_once_per_reservation_and_never_documents(db):
    comment = "+20% COMISION + 11% DTO L/ ESTANCIA\nTarjeta 4111 1111 1111 1111"
    batch = commit(db, export([base("R1") + extra(comment)]))
    item = db.get(ReservationDetail, "R1")
    assert item.batch_id == batch.id and item.handled_by == "Amapola"
    assert item.customer_email == "ann.boe@example.test" and item.occupant_email is None
    assert (item.rent_total, item.commission, item.invoice_total) == (
        Decimal("3288.73"),
        Decimal("0"),
        Decimal("990.85"),
    )
    assert item.invoice_number == "A26/1, A26/2" and item.invoice_date == date(2026, 10, 15)
    assert "4111 1111 1111 1111" not in item.comments and "COMISION" in item.comments
    stored = " ".join(str(value) for value in vars(item).values())
    assert "DOC-SECRET" not in stored and "12345678" not in stored

    commit(db, export([base("R1", paid="100") + extra("nuevo comentario")]))
    assert db.query(ReservationDetail).count() == 1
    assert db.get(ReservationDetail, "R1").comments == "nuevo comentario"


def test_exports_without_extra_columns_still_import(db):
    raw = export([base("R2")], extra=False)
    assert parse_details(raw) == {}
    commit(db, raw)
    assert db.query(ReservationDetail).count() == 0
    assert db.scalar(select(ReservationSnapshot.locator)) == "R2"


def scenario(db, rows):
    properties = ImportBatch(dataset="properties", source="t", filename="p", checksum="p",
                             status="valid", warnings=[], created_at=NOW)  # fmt: skip
    db.add(properties)
    db.flush()
    db.add(PropertySnapshot(batch_id=properties.id, source_row=1, row_hash="A", property_id="A",
                            name="BJC", status_original="Activo", active=True,
                            category="Apartamento", capacity=2))  # fmt: skip
    batch = ImportBatch(dataset="reservations", source="t", filename="r", checksum="r",
                        status="valid", warnings=[], created_at=NOW)  # fmt: skip
    db.add(batch)
    db.flush()
    for locator, total, asked, detail in rows:
        db.add(ReservationSnapshot(
            batch_id=batch.id, source_row=1, row_hash=locator, locator=locator, property_id="A",
            status_original="Confirmada", status="confirmed", start_date=date(2026, 12, 1),
            end_date=date(2026, 12, 8), nights=7, adults=2, children=0, infants=0,
            display_name="X", total=Decimal(total), net=Decimal(total) / Decimal("1.1"),
            tax=Decimal(total) - Decimal(total) / Decimal("1.1"), paid=Decimal("0"),
            pending=Decimal(asked)))  # fmt: skip
        if detail is not None:
            db.add(ReservationDetail(locator=locator, **detail))
    db.commit()


def test_differences_explained_by_the_export_do_not_need_attention(db):
    scenario(
        db,
        [
            ("EXP", "683.06", "833.00", {"comments": "MUEVO AL 6103\n+18% COMISION"}),
            ("SID", "3288.73", "4110.91", {"comments": "+20% COMISION + 11% DTO L/ ESTANCIA"}),
            ("COL", "500.00", "600.00", {"commission": Decimal("100")}),
            ("DTO", "900.00", "1000.00", {"comments": "10 % de descuento por repetidor"}),
            ("RAW", "3426.35", "3587.59", {"comments": "6143. EP", "handled_by": "Ana"}),
            ("NOD", "100.00", "150.00", None),
        ],
    )
    result = reservation_issues.collect(db, TODAY)
    by_locator = {item["reservation"]["locator"]: item for item in result["amounts"]}
    explained = {k: (v["explanation"] or {}).get("source") for k, v in by_locator.items()}
    assert explained == {
        "EXP": "comment_commission",
        "SID": "comment_commission",
        "COL": "commission",
        "DTO": "comment_discount",
        "RAW": None,
        "NOD": None,
    }
    sid = by_locator["SID"]["explanation"]["text"]
    assert sid == "Comisión del 20 % anotada en los comentarios"
    assert by_locator["RAW"]["reservation"]["comments"] == "6143. EP"
    assert by_locator["RAW"]["reservation"]["handled_by"] == "Ana"
    assert "customer_email" not in by_locator["RAW"]["reservation"]
    assert result["counts"]["amounts"] == {"total": 6, "current": 6, "pending": 2, "explained": 4}
    assert [item["reservation"]["locator"] for item in result["amounts"]][:2] == ["RAW", "NOD"]

    notifications.scan(db, NOW)
    notice = db.scalar(select(Notification).where(Notification.kind == "reservations.amounts"))
    assert notice.title.startswith("2 reservas vigentes o futuras")
    assert "sin explicar" in notice.title and "RAW" in notice.body and "EXP" not in notice.body
