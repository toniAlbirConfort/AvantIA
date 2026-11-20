from __future__ import annotations

import csv
import hashlib
import io
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select

from app.models import (
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


def test_pending_is_total_minus_paid_like_the_booking_card(db):
    # 32011081: the export says 2100.85 pending, the booking card 3214.68 - 2150 = 1064.68.
    batch = commit(db, export([base("R3", total="3214.68", paid="2150.00", pending="2100.85")
                               + extra("HACE TRANSFERENCIA DEPOSITO")]))  # fmt: skip
    row = db.scalar(select(ReservationSnapshot).where(ReservationSnapshot.locator == "R3"))
    assert row.pending == Decimal("1064.68")
    assert db.get(ReservationDetail, "R3").export_pending == Decimal("2100.85")
    assert "balance_mismatch" not in {issue["code"] for issue in batch.warnings}

