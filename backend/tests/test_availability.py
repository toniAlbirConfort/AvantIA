from __future__ import annotations

import csv
import hashlib
import io
from datetime import UTC, date, datetime, timedelta

from app.availability import check_availability, import_csv
from app.models import ImportBatch
from app.planning import commit_import


def csv_data(start: date, end: date) -> bytes:
    rows = [
        "property_id,property_name,capacity,start_date,end_date,status",
        f"A,Casa Azul,4,{start},{end},available",
        f"A,Casa Azul,4,{start + timedelta(days=2)},{start + timedelta(days=4)},occupied",
        f"B,Casa Verde,5,{start},{end},available",
        f"C,Estudio,2,{start},{end},available",
    ]
    return "\n".join(rows).encode()


def seed_sources(db, start: date) -> None:
    properties = (
        "Listado de propiedades;\n"
        "Código;Estado;Alojamiento;Clase;Capacidad personas;Edificio;Población\n"
        "A;Activado (desde el 01/01/2020);Casa Azul;Apartamento;4;Centro;Albir\n"
        "B;Activado (desde el 01/01/2020);Casa Verde;Apartamento;5;Centro;Albir\n"
        "C;Activado (desde el 01/01/2020);Estudio;Apartamento;2;Centro;Albir\n"
    ).encode()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Listado de reservas"])
    writer.writerow(
        [
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
        ]
    )
    writer.writerow(
        [
            "INQUIRY-1",
            start.strftime("%d/%m/%Y"),
            "Petición Información",
            (start + timedelta(days=20)).strftime("%d/%m/%Y"),
            (start + timedelta(days=21)).strftime("%d/%m/%Y"),
            1,
            100,
            90,
            10,
            0,
            100,
            2,
            0,
            0,
            "A",
            "Cliente",
            "Prueba",
            "",
            "",
            "Web",
        ]
    )
    for dataset, raw in (("properties", properties), ("reservations", buf.getvalue().encode())):
        batch = commit_import(
            db, dataset, raw, f"{dataset}.csv", 1, hashlib.sha256(raw).hexdigest()
        )
        assert batch.status == "valid"


def test_occupied_interval_overrides_available_and_alternative_requires_capacity(db):
    start = date.today() + timedelta(days=10)
    end = start + timedelta(days=7)
    seed_sources(db, start)
    batch = import_csv(db, raw=csv_data(start, end), filename="calendar.csv", source="upload")
    assert batch.status == "valid"
    result = check_availability(
        db,
        property_query="A",
        arrival=str(start + timedelta(days=2)),
        departure=str(start + timedelta(days=4)),
        guests=4,
    )
    assert result.status == "unavailable"
    assert [item["property_id"] for item in result.alternatives] == ["B"]
    # Checkout is exclusive: an arrival on the checkout date does not overlap the block.
    next_result = check_availability(
        db,
        property_query="A",
        arrival=str(start + timedelta(days=4)),
        departure=str(start + timedelta(days=6)),
        guests=4,
    )
    assert next_result.status == "available"


def test_invalid_csv_keeps_last_valid_snapshot(db):
    start = date.today() + timedelta(days=10)
    seed_sources(db, start)
    good = import_csv(
        db, raw=csv_data(start, start + timedelta(days=7)), filename="good.csv", source="upload"
    )
    bad = import_csv(db, raw=b"property_id,capacity\nA,4", filename="bad.csv", source="upload")
    assert bad.status == "invalid"
    assert db.query(ImportBatch).count() == 4
    result = check_availability(
        db,
        property_query="B",
        arrival=str(start),
        departure=str(start + timedelta(days=2)),
        guests=4,
    )
    assert result.batch_id == good.id
    assert result.status == "available"


def test_ambiguous_or_missing_data_never_claims_available(db):
    start = date.today() + timedelta(days=10)
    seed_sources(db, start)
    import_csv(
        db, raw=csv_data(start, start + timedelta(days=7)), filename="calendar.csv", source="upload"
    )
    assert (
        check_availability(
            db,
            property_query="Casa",
            arrival=str(start),
            departure=str(start + timedelta(days=2)),
            guests=2,
        ).status
        == "ambiguous_property"
    )
    assert (
        check_availability(
            db, property_query="A", arrival=None, departure=str(start + timedelta(days=2)), guests=2
        ).status
        == "manual_check"
    )


def test_stale_snapshot_requires_manual_check(db):
    start = date.today() + timedelta(days=10)
    batch = import_csv(
        db, raw=csv_data(start, start + timedelta(days=7)), filename="calendar.csv", source="upload"
    )
    batch.created_at = datetime.now(UTC) - timedelta(hours=7)
    db.commit()
    result = check_availability(
        db,
        property_query="B",
        arrival=str(start),
        departure=str(start + timedelta(days=2)),
        guests=2,
    )
    assert result.stale
    assert result.status == "manual_check"
