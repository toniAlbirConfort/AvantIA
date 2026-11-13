from __future__ import annotations

import hashlib
from datetime import date

import pytest

from app.customers import (
    commit_customers,
    customer_detail,
    customers_view,
    mask_account,
    parse_customers,
    preview_customers,
    redact_cards,
)
from app.models import Customer, ImportBatch, ReservationSnapshot

# Fictional customers that reproduce the shape of the Avantio customer export.
HEADER = (
    "Id cliente;Nombre;Primer apellido;Segundo apellido;Fecha nacimiento;Sexo;"
    "Nacionalidad (País);Calle;Número;Puerta;Código postal;Ciudad;Provincia;País;DNI/ID;"
    "E-mail;E-mail alternativo;Teléfono;Teléfono alternativo 2;Teléfono alternativo 1;"
    "Idioma del cliente;Clase;Tipo cliente;Cuenta bancaria;Código fiscal;Observaciones;"
    "Fecha de alta;Newsletters;Cuenta contable;Región;Periodo de estancia"
)
JOHN = (
    "100001;JOHN PAUL;EXAMPLE;;01/02/1970;Masculino;Reino Unido;1 Test Street;;;"
    "AB12CD;Testtown;Testshire;Reino Unido;P0000001;john@example.test;"
    "john@example.test;+44 7000000001;;;Inglés (UK);Normal;Turista;;;EXPEDIA;"
    "11/02/2026;Sí;;;"
)
ANNA = (
    "100002;ANNA;MUSTER;;;Femenino;Polonia;ul. Testowa 1;;;00-001;Testowo;"
    "Testowo;Polonia;-;anna@guest.example.test;;+48 500 000 002;-;;Polaco;Normal;Turista;"
    "4111111111111111 02/26;;;27/03/2025;No;;;"
)
# Notes with a stray ';' and an embedded CRLF line break, as Avantio exports them.
PEDRO = (
    "100003;Pedro;PRUEBA;EJEMPLO;22/03/1965;Masculino;ESPAÑOL;Calle Falsa 123;;;28000;"
    "MADRID;MADRID;España;00000000T;pedro@example.test;;0;;600000003;Español (ES);"
    "Normal;Turista;;;Amigo de Luis ; pide planta alta\r\nTarjeta 4111 1111 1111 1111;"
    "20/04/2015;No;;;"
)


def export(*rows: str) -> bytes:
    return ("﻿Lista;\n" + HEADER + "\n" + "\n".join(rows) + "\n").encode()


def commit(db, raw: bytes, filename: str = "clientes.csv") -> ImportBatch:
    return commit_customers(db, raw, filename, None, hashlib.sha256(raw).hexdigest())


def test_parse_tolerates_avantio_quirks():
    rows, warnings = parse_customers(export(JOHN, ANNA, PEDRO))
    by_id = {row["avantio_id"]: row for row in rows}
    assert len(rows) == 3

    john = by_id["100001"]
    assert john["display_name"] == "JOHN PAUL EXAMPLE"
    assert john["birth_date"] == date(1970, 2, 1)
    assert john["registered_on"] == date(2026, 2, 11)
    assert john["email_opt_in"] is True
    assert john["notes"] == "EXPEDIA"
    assert john["phone"] == "+44 7000000001"

    anna = by_id["100002"]
    assert anna["document_number"] is None
    assert anna["phone_alt2"] is None
    assert anna["bank_account_masked"] == "••••1111"
    assert anna["email_opt_in"] is False

    pedro = by_id["100003"]
    assert pedro["phone"] is None
    assert pedro["phone_alt1"] == "600000003"
    assert pedro["registered_on"] == date(2015, 4, 20)
    assert pedro["notes"] == "Amigo de Luis ; pide planta alta\nTarjeta [tarjeta ••••1111]"
    assert {"code": "extra_separators", "customer_id": "100003", "source_row": 5} in warnings


def test_parse_rejects_other_exports():
    with pytest.raises(ValueError, match="listado de clientes"):
        parse_customers(b"Listado de reservas\nLocalizador\n1\n")
    with pytest.raises(ValueError, match="columnas obligatorias"):
        parse_customers(b"Lista;\nId cliente;Nombre\n1;Ana\n")


def test_card_helpers_keep_only_last_digits():
    assert mask_account("ES00 0000 0000 0000 0000 1332") == "••••1332"
    assert mask_account("123") == "••••"
    assert redact_cards("móvil 600000000") == "móvil 600000000"
    assert redact_cards("visa 4111-1111-1111-1111 ok") == "visa [tarjeta ••••1111] ok"


def test_import_upserts_and_keeps_absent_customers(db):
    first = commit(db, export(JOHN, ANNA))
    assert first.status == "valid" and first.row_count == 2
    assert {change["kind"] for change in first.changes} == {"added"}

    changed = JOHN.replace("EXPEDIA", "EXPEDIA VIP")
    preview = preview_customers(db, export(changed, PEDRO))
    assert preview["carried_rows"] == 1
    assert preview["effective_rows"] == 3
    assert {(c["key"], c["kind"]) for c in preview["changes"]} == {
        ("100001", "changed"), ("100003", "added"),
    }

    second = commit(db, export(changed, PEDRO), "clientes-2.csv")
    assert second.status == "valid"
    assert db.query(Customer).count() == 3
    john = db.query(Customer).filter_by(avantio_id="100001").one()
    assert john.notes == "EXPEDIA VIP" and john.batch_id == second.id
    anna = db.query(Customer).filter_by(avantio_id="100002").one()
    assert anna.batch_id == first.id

    assert commit(db, export(changed, PEDRO), "again.csv").id == second.id


def test_commit_rejects_changed_file(db):
    with pytest.raises(ValueError, match="cambió"):
        commit_customers(db, export(JOHN), "x.csv", None, "0" * 64)


def test_list_search_and_detail(db):
    commit(db, export(JOHN, ANNA, PEDRO))
    listing = customers_view(db)
    assert listing["total"] == 3 and listing["has_data"]
    assert [item["display_name"] for item in listing["items"]] == [
        "ANNA MUSTER", "JOHN PAUL EXAMPLE", "Pedro PRUEBA EJEMPLO",
    ]
    assert customers_view(db, q="prueba ejémplo")["total"] == 1
    assert customers_view(db, q="7000000001")["items"][0]["avantio_id"] == "100001"
    assert customers_view(db, q="john@example")["total"] == 1
    assert customers_view(db, q="nadie")["total"] == 0

    batch = ImportBatch(dataset="reservations", source="upload", filename="r.csv",
                        checksum="c", status="valid")
    db.add(batch)
    db.flush()
    base = dict(batch_id=batch.id, source_row=3, row_hash="h", property_id="P1",
                status_original="Pagada", status="paid", created_on=date(2026, 2, 11),
                start_date=date(2026, 2, 15), end_date=date(2026, 2, 19), nights=4,
                adults=2, children=0, infants=0, portal="Expedia", total=500, net=450,
                tax=50, paid=500, pending=0)
    db.add_all([
        ReservationSnapshot(locator="R-1", display_name="John Paul Example", **base),
        ReservationSnapshot(locator="R-2", display_name="John Paul Examples", **base),
    ])
    db.commit()

    detail = customer_detail(db, "100001")
    assert detail is not None
    assert detail["email"] == "john@example.test"
    assert detail["country"] == "Reino Unido"
    assert [item["locator"] for item in detail["reservations"]] == ["R-1"]
    assert "search_text" not in detail and "row_hash" not in detail
    assert customer_detail(db, "999") is None
