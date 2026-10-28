"""Minimal, versioned operational imports and a read-only planning projection."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models import ImportBatch, PropertySnapshot, ReservationSnapshot

PARSER_VERSION = "2"
MAX_BYTES = 10_000_000
DATASETS = {"properties", "reservations"}
PROPERTY_COLUMNS = {"Código", "Estado", "Alojamiento", "Clase", "Capacidad personas"}
RESERVATION_COLUMNS = {
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
}
STATUS = {
    "confirmada": "confirmed",
    "prereserva": "pre_reservation",
    "bajo peticion": "pre_reservation",
    "peticion informacion": "inquiry",
    "pagada": "paid",
    "cancelada": "cancelled",
    "no disponible": "unavailable",
    "de propietario": "owner",
}
BLOCKING_STATUSES = frozenset(
    {"confirmed", "pre_reservation", "paid", "unavailable", "owner"}
)
RESERVATION_FIELDS = (
    "source_row", "row_hash", "locator", "property_id", "status_original", "status",
    "created_on", "start_date", "end_date", "nights", "adults", "children", "infants",
    "display_name", "portal", "total", "net", "tax", "paid", "pending",
)


def latest_source_batch(db: Session, dataset: str) -> ImportBatch | None:
    return db.scalar(
        select(ImportBatch)
        .where(ImportBatch.dataset == dataset, ImportBatch.status == "valid")
        .order_by(desc(ImportBatch.created_at), desc(ImportBatch.id))
    )


def selected_batch(db: Session, dataset: str, batch_id: int | None) -> ImportBatch | None:
    if batch_id is None:
        return latest_source_batch(db, dataset)
    batch = db.get(ImportBatch, batch_id)
    if not batch or batch.dataset != dataset or batch.status != "valid":
        raise ValueError(f"Instantánea de {dataset} no encontrada")
    return batch


def _text(row: dict, key: str, max_length: int, required: bool = False) -> str:
    value = (row.get(key) or "").strip()
    if required and not value:
        raise ValueError(f"falta {key}")
    if len(value) > max_length:
        raise ValueError(f"{key} supera {max_length} caracteres")
    return value


def _date(row: dict, key: str, optional: bool = False) -> date | None:
    value = _text(row, key, 30)
    if optional and not value:
        return None
    try:
        return datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError as exc:
        raise ValueError(f"{key} no es una fecha válida") from exc


def _integer(
    row: dict, key: str, minimum: int = 0, blank_default: int | None = None
) -> int:
    try:
        raw = _text(row, key, 20)
        value = blank_default if not raw and blank_default is not None else int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} no es un entero válido") from exc
    if value < minimum:
        raise ValueError(f"{key} debe ser al menos {minimum}")
    return value


def _money(row: dict, key: str) -> Decimal:
    try:
        value = Decimal(_text(row, key, 30, required=True))
    except InvalidOperation as exc:
        raise ValueError(f"{key} no es un importe válido") from exc
    if not value.is_finite() or value.as_tuple().exponent < -2 or abs(value) >= 10**10:
        raise ValueError(f"{key} no es un importe válido")
    return value


def _status(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value.casefold())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    try:
        return STATUS[folded.strip()]
    except KeyError as exc:
        raise ValueError("Estado de reserva no reconocido") from exc


def _reader(raw: bytes, dataset: str):
    if len(raw) > MAX_BYTES:
        raise ValueError("El CSV supera 10 MB")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("El CSV debe estar codificado en UTF-8") from exc
    title, separator, body = content.partition("\n")
    if not separator or "Listado de " not in title:
        raise ValueError("Falta la fila de título de la exportación")
    delimiter = ";" if dataset == "properties" else ","
    if dataset == "properties" and not title.startswith("Listado de propiedades"):
        raise ValueError("El archivo no es un listado de propiedades")
    if dataset == "reservations" and "Listado de reservas" not in title:
        raise ValueError("El archivo no es un listado de reservas")
    reader = csv.DictReader(io.StringIO(body, newline=""), delimiter=delimiter, strict=True)
    required = PROPERTY_COLUMNS if dataset == "properties" else RESERVATION_COLUMNS
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        raise ValueError("Faltan columnas obligatorias de la exportación")
    if len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise ValueError("Hay columnas duplicadas")
    return reader


def _property(row: dict) -> dict:
    original = _text(row, "Estado", 100, required=True)
    if original.startswith("Activado ("):
        active = True
    elif original.startswith("Desactivado ("):
        active = False
    else:
        raise ValueError("Estado de propiedad no reconocido")
    address = " ".join(
        value
        for value in (
            _text(row, "Tipo vía", 40),
            _text(row, "Dirección", 150),
            _text(row, "Número", 20),
            _text(row, "Escalera", 20),
            _text(row, "Planta", 20),
            _text(row, "Puerta", 20),
        )
        if value
    )
    if len(address) > 255:
        raise ValueError("La dirección supera 255 caracteres")
    return {
        "property_id": _text(row, "Código", 100, required=True),
        "name": _text(row, "Alojamiento", 255, required=True),
        "status_original": original,
        "active": active,
        "category": _text(row, "Clase", 100, required=True),
        "capacity": _integer(row, "Capacidad personas", minimum=1),
        "building": _text(row, "Edificio", 255) or None,
        "city": _text(row, "Población", 150) or None,
        "address": address or None,
    }


def _reservation(row: dict) -> dict:
    start, end = _date(row, "Fecha entrada"), _date(row, "Fecha salida")
    nights = _integer(row, "noches", minimum=1)
    if start >= end or (end - start).days != nights:
        raise ValueError("El intervalo no coincide con las noches")
    original = _text(row, "Estado", 100, required=True)
    status = _status(original)
    name = " ".join(
        part
        for part in (_text(row, "Ocupante: Nombre", 120), _text(row, "Ocupante: Apellidos", 120))
        if part
    ) or " ".join(
        part
        for part in (_text(row, "Cliente: Nombre", 120), _text(row, "Cliente: Apellidos", 120))
        if part
    )
    if len(name) > 255:
        raise ValueError("El nombre mostrado supera 255 caracteres")
    if status == "unavailable":
        name = "No disponible"
    elif status == "owner":
        name = "Uso de propietario"
    return {
        "locator": _text(row, "Localizador", 150, required=True),
        "property_id": _text(row, "id alojamiento", 100, required=True),
        "status_original": original,
        "status": status,
        "created_on": _date(row, "Fecha alta", optional=True),
        "start_date": start,
        "end_date": end,
        "nights": nights,
        "adults": _integer(row, "Adultos"),
        "children": _integer(row, "Niños"),
        "infants": _integer(row, "Bebés", blank_default=0),
        "display_name": name or "Huésped sin nombre",
        "portal": _text(row, "Portal", 100) or None,
        "total": _money(row, "Total reserva con tasas"),
        "net": _money(row, "Total reserva sin tasas"),
        "tax": _money(row, "Total tasas"),
        "paid": _money(row, "Pagado"),
        "pending": _money(row, "Pendiente"),
    }


def _canonical(row: dict) -> dict:
    return {
        key: value.isoformat()
        if isinstance(value, date)
        else f"{value:.2f}"
        if isinstance(value, Decimal)
        else value
        for key, value in row.items()
        if key not in {"source_row", "row_hash"}
    }


def _hash_row(row: dict) -> str:
    return hashlib.sha256(
        json.dumps(_canonical(row), sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def parse_export(raw: bytes, dataset: str) -> list[dict]:
    if dataset not in DATASETS:
        raise ValueError("Tipo de datos no válido")
    reader = _reader(raw, dataset)
    key = "property_id" if dataset == "properties" else "locator"
    rows, seen = [], set()
    while True:
        source_row = reader.line_num + 2  # title and header are physical lines 1 and 2
        try:
            source = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            raise ValueError(f"Fila {source_row}: CSV mal formado") from exc
        if None in source or any(value is None for value in source.values()):
            raise ValueError(f"Fila {source_row}: número de columnas incorrecto")
        try:
            row = _property(source) if dataset == "properties" else _reservation(source)
        except ValueError as exc:
            raise ValueError(f"Fila {source_row}: {exc}") from exc
        if row[key] in seen:
            raise ValueError(f"Fila {source_row}: clave duplicada")
        seen.add(row[key])
        row["source_row"] = source_row
        row["row_hash"] = _hash_row(row)
        rows.append(row)
        if len(rows) > 100_000:
            raise ValueError("El CSV supera 100.000 registros")
    if not rows:
        raise ValueError("CSV sin datos")
    return rows


def _current_rows(db: Session, dataset: str) -> list:
    batch = latest_source_batch(db, dataset)
    if not batch:
        return []
    model = PropertySnapshot if dataset == "properties" else ReservationSnapshot
    return list(db.scalars(select(model).where(model.batch_id == batch.id)))


def _changes(rows: list[dict], old_rows: list, dataset: str) -> list[dict]:
    key = "property_id" if dataset == "properties" else "locator"
    before = {getattr(row, key): row for row in old_rows}
    after = {row[key]: row for row in rows}
    changes = []
    for identifier in sorted(before.keys() | after.keys()):
        if identifier not in before:
            changes.append({"key": identifier, "kind": "added"})
        elif identifier not in after and dataset == "properties":
            changes.append({"key": identifier, "kind": "removed"})
        elif identifier in after and before[identifier].row_hash != after[identifier]["row_hash"]:
            previous = {
                field: getattr(before[identifier], field)
                for field in after[identifier]
                if field not in {"source_row", "row_hash"}
            }
            fields = [
                field
                for field, value in _canonical(previous).items()
                if value != _canonical(after[identifier])[field]
            ]
            changes.append({"key": identifier, "kind": "changed", "fields": fields})
    return changes


def _merge_reservations(
    old_rows: list, incoming: list[dict], source_batch_id: int | None
) -> list[dict]:
    merged = {
        row.locator: {
            **{field: getattr(row, field) for field in RESERVATION_FIELDS},
            "source_batch_id": row.source_batch_id or row.batch_id,
        }
        for row in old_rows
    }
    for row in incoming:
        merged[row["locator"]] = {**row, "source_batch_id": source_batch_id}
    return list(merged.values())


def _warnings(rows: list[dict], dataset: str, properties: list) -> list[dict]:
    if dataset != "reservations":
        return []
    warnings = []
    lookup = {item.property_id: item for item in properties}
    blocked = defaultdict(list)
    for row in rows:
        locator = row["locator"]
        provenance = {
            "source_row": row["source_row"],
            "source_batch_id": row.get("source_batch_id"),
        }
        prop = lookup.get(row["property_id"])
        if prop is None:
            warnings.append(
                {
                    "code": "unknown_property",
                    "locator": locator,
                    "property_id": row["property_id"],
                    **provenance,
                }
            )
        elif not prop.active:
            warnings.append(
                {
                    "code": "inactive_property",
                    "locator": locator,
                    "property_id": row["property_id"],
                    **provenance,
                }
            )
        if abs(row["total"] - row["net"] - row["tax"]) > Decimal("0.01"):
            warnings.append(
                {"code": "tax_mismatch", "locator": locator, **provenance}
            )
        if abs(row["total"] - row["paid"] - row["pending"]) > Decimal("0.01"):
            warnings.append(
                {"code": "balance_mismatch", "locator": locator, **provenance}
            )
        if row["status"] in BLOCKING_STATUSES:
            blocked[row["property_id"]].append(row)
    for property_id, items in blocked.items():
        items.sort(key=lambda item: item["start_date"])
        for i, first in enumerate(items):
            for second in items[i + 1 :]:
                if second["start_date"] >= first["end_date"]:
                    break
                warnings.append(
                    {
                        "code": "overlap",
                        "property_id": property_id,
                        "locators": [first["locator"], second["locator"]],
                    }
                )
    return warnings


def preview_import(db: Session, dataset: str, raw: bytes) -> dict:
    rows = parse_export(raw, dataset)
    old = _current_rows(db, dataset)
    checksum = hashlib.sha256(raw).hexdigest()
    current_batch = latest_source_batch(db, dataset)
    duplicate = current_batch if current_batch and current_batch.checksum == checksum else None
    created_dates = [row["created_on"] for row in rows if row.get("created_on")]
    coverage = (
        {
            "first_arrival": min(row["start_date"] for row in rows),
            "last_departure": max(row["end_date"] for row in rows),
            "first_created": min(created_dates, default=None),
            "last_created": max(created_dates, default=None),
        }
        if dataset == "reservations"
        else None
    )
    merged = _merge_reservations(old, rows, None) if dataset == "reservations" else rows
    return {
        "dataset": dataset,
        "checksum": checksum,
        "rows": len(rows),
        "effective_rows": len(merged),
        "carried_rows": len(merged) - len(rows) if dataset == "reservations" else 0,
        "duplicate_of": duplicate.id if duplicate else None,
        "coverage": coverage,
        "warnings": _warnings(merged, dataset, _current_rows(db, "properties")),
        "changes": _changes(rows, old, dataset),
    }


def commit_import(
    db: Session, dataset: str, raw: bytes, filename: str, actor_user_id: int, expected_checksum: str
) -> ImportBatch:
    checksum = hashlib.sha256(raw).hexdigest()
    if checksum != expected_checksum:
        raise ValueError("El archivo cambió desde la vista previa")
    current_batch = latest_source_batch(db, dataset)
    duplicate = current_batch if current_batch and current_batch.checksum == checksum else None
    if duplicate:
        return duplicate
    batch = ImportBatch(
        dataset=dataset,
        source="upload",
        filename=filename[:255],
        checksum=checksum,
        status="invalid",
        actor_user_id=actor_user_id,
        parser_version=PARSER_VERSION,
    )
    db.add(batch)
    db.flush()
    try:
        preview = preview_import(db, dataset, raw)
        rows = parse_export(raw, dataset)
        if dataset == "reservations":
            merged = _merge_reservations(_current_rows(db, dataset), rows, batch.id)
        else:
            merged = rows
        model = PropertySnapshot if dataset == "properties" else ReservationSnapshot
        db.add_all(model(batch_id=batch.id, **row) for row in merged)
        batch.row_count = len(rows)
        batch.warnings = _warnings(merged, dataset, _current_rows(db, "properties"))
        batch.changes = preview["changes"]
        batch.status = "valid"
    except (ValueError, csv.Error) as exc:
        batch.error = str(exc)[:1000]
    db.commit()
    return batch


def properties_view(db: Session, batch_id: int | None = None) -> list[dict]:
    batch = selected_batch(db, "properties", batch_id)
    if not batch:
        return []
    rows = db.scalars(
        select(PropertySnapshot)
        .where(PropertySnapshot.batch_id == batch.id)
        .order_by(PropertySnapshot.name)
    ).all()
    return [
        {
            "id": row.property_id,
            "name": row.name,
            "active": row.active,
            "category": row.category,
            "capacity": row.capacity,
            "building": row.building,
            "city": row.city,
            "address": row.address,
            "status_original": row.status_original,
            "source_row": row.source_row,
            "batch_id": batch.id,
        }
        for row in rows
    ]


def planning_view(
    db: Session,
    start: date,
    end: date,
    property_batch_id: int | None = None,
    reservation_batch_id: int | None = None,
) -> dict:
    if start >= end or (end - start).days > 92:
        raise ValueError("El intervalo debe ser de 1 a 92 días")
    pb = selected_batch(db, "properties", property_batch_id)
    rb = selected_batch(db, "reservations", reservation_batch_id)
    properties = (
        []
        if not pb
        else list(db.scalars(select(PropertySnapshot).where(PropertySnapshot.batch_id == pb.id)))
    )
    # A batch is an audit source, not a filter on the operational planning.
    # Older imports may predate the cumulative snapshot format, so fold every
    # valid source in upload order and retain the newest version per locator.
    by_locator: dict[str, ReservationSnapshot] = {}
    if rb:
        source_rows = db.scalars(
            select(ReservationSnapshot)
            .join(ImportBatch, ReservationSnapshot.batch_id == ImportBatch.id)
            .where(
                ImportBatch.dataset == "reservations",
                ImportBatch.status == "valid",
                ImportBatch.id <= rb.id,
            )
            .order_by(ImportBatch.id, ReservationSnapshot.id)
        )
        for row in source_rows:
            by_locator[row.locator] = row
    all_reservations = list(by_locator.values())
    reservations = [
        row for row in all_reservations if row.start_date < end and row.end_date > start
    ]
    coverage = None
    if all_reservations:
        created_dates = [row.created_on for row in all_reservations if row.created_on]
        coverage = {
            "first_arrival": min(row.start_date for row in all_reservations),
            "last_departure": max(row.end_date for row in all_reservations),
            "first_created": min(created_dates, default=None),
            "last_created": max(created_dates, default=None),
            "effective_rows": len(all_reservations),
        }
    shown = {
        p.property_id: p
        for p in properties
        if p.active and p.category in {"Apartamento", "Villa"}
    }
    active_property_ids = set(shown)
    reservations = [row for row in reservations if row.property_id in active_property_ids]
    visible_reservation_ids = {
        row.locator for row in all_reservations if row.property_id in active_property_ids
    }
    bars = []
    inquiries = []
    cancellations = []
    for row in reservations:
        item = {
            "locator": row.locator,
            "property_id": row.property_id,
            "status": row.status,
            "status_original": row.status_original,
            "start_date": row.start_date,
            "end_date": row.end_date,
            "display_name": row.display_name,
            "total": row.total,
            "portal": row.portal,
            "created_on": row.created_on,
            "nights": row.nights,
            "adults": row.adults,
            "children": row.children,
            "infants": row.infants,
            "net": row.net,
            "tax": row.tax,
            "paid": row.paid,
            "pending": row.pending,
        }
        if row.status == "inquiry":
            inquiries.append(item)
        elif row.status == "cancelled":
            cancellations.append(item)
        elif row.status in BLOCKING_STATUSES:
            bars.append(item)
    warning_rows = [
        {
            **{field: getattr(row, field) for field in RESERVATION_FIELDS},
            "source_batch_id": row.source_batch_id or row.batch_id,
        }
        for row in all_reservations
        if row.locator in visible_reservation_ids
    ]
    warnings = _warnings(warning_rows, "reservations", properties)
    return {
        "start": start,
        "end": end,
        "property_batch": _batch_summary(pb),
        "reservation_batch": _batch_summary(rb),
        "reservation_coverage": coverage,
        "reservations_in_range": len(reservations),
        "properties": [
            {
                "id": p.property_id,
                "name": p.name,
                "capacity": p.capacity,
                "building": p.building,
                "active": p.active,
                "category": p.category,
            }
            for p in sorted(shown.values(), key=lambda p: (p.building or "", p.name))
        ],
        "bars": bars,
        "inquiries": inquiries,
        "cancellations": cancellations,
        "warnings": warnings,
    }


def _batch_summary(batch: ImportBatch | None) -> dict | None:
    return (
        None
        if not batch
        else {
            "id": batch.id,
            "dataset": batch.dataset,
            "filename": batch.filename,
            "checksum": batch.checksum,
            "rows": batch.row_count,
            "created_at": batch.created_at,
            "actor_user_id": batch.actor_user_id,
            "parser_version": batch.parser_version,
        }
    )
