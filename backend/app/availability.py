from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AvailabilitySlot, ImportBatch

FIELDS = ("property_id", "property_name", "capacity", "start_date", "end_date", "status")
STATUS = {
    "available": "available",
    "free": "available",
    "libre": "available",
    "disponible": "available",
    "occupied": "occupied",
    "booked": "occupied",
    "blocked": "occupied",
    "ocupado": "occupied",
    "reservado": "occupied",
}


def _date(value: str) -> date:
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Fecha inválida: {value[:30]}")


def parse_csv(raw: bytes, mapping: dict[str, str]) -> list[dict]:
    if len(raw) > 10_000_000:
        raise ValueError("El CSV supera 10 MB")
    text = raw.decode("utf-8-sig")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError("CSV sin cabecera")
    aliases = {field: mapping.get(field) or field for field in FIELDS}
    missing = [field for field, column in aliases.items() if column not in reader.fieldnames]
    if missing:
        raise ValueError("Faltan columnas: " + ", ".join(missing))
    rows = []
    property_meta: dict[str, tuple[str, int]] = {}
    for line, row in enumerate(reader, 2):
        try:
            property_id = row[aliases["property_id"]].strip()
            name = row[aliases["property_name"]].strip()
            capacity = int(row[aliases["capacity"]])
            start = _date(row[aliases["start_date"]])
            end = _date(row[aliases["end_date"]])
            status = STATUS[row[aliases["status"]].strip().lower()]
            if not property_id or not name or capacity < 1 or start >= end:
                raise ValueError("identificador, capacidad o intervalo inválido")
            meta = (name, capacity)
            if property_id in property_meta and property_meta[property_id] != meta:
                raise ValueError("atributos contradictorios del alojamiento")
            property_meta[property_id] = meta
            rows.append(
                dict(
                    property_id=property_id,
                    property_name=name,
                    capacity=capacity,
                    start_date=start,
                    end_date=end,
                    status=status,
                )
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"Fila {line}: {exc}") from exc
        if len(rows) > 100_000:
            raise ValueError("El CSV supera 100.000 filas")
    if not rows:
        raise ValueError("CSV sin datos")
    return rows


def import_csv(
    db: Session, *, raw: bytes, filename: str, source: str, mapping: dict[str, str] | None = None
) -> ImportBatch:
    checksum = hashlib.sha256(raw).hexdigest()
    prior = db.scalar(
        select(ImportBatch)
        .where(ImportBatch.checksum == checksum, ImportBatch.status == "valid")
        .order_by(desc(ImportBatch.id))
    )
    if prior:
        return prior
    batch = ImportBatch(source=source, filename=filename[:255], checksum=checksum, status="invalid")
    db.add(batch)
    db.flush()
    try:
        rows = parse_csv(raw, mapping or {})
        for row in rows:
            db.add(AvailabilitySlot(batch_id=batch.id, **row))
        batch.row_count = len(rows)
        batch.status = "valid"
    except (UnicodeError, csv.Error, ValueError) as exc:
        batch.error = str(exc)[:1000]
    db.commit()
    return batch


def latest_batch(db: Session) -> ImportBatch | None:
    return db.scalar(
        select(ImportBatch)
        .where(ImportBatch.status == "valid")
        .order_by(desc(ImportBatch.created_at), desc(ImportBatch.id))
    )


def aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@dataclass
class AvailabilityResult:
    batch_id: int | None
    imported_at: datetime | None
    stale: bool
    requested: str
    status: str
    alternatives: list[dict]

    def as_dict(self) -> dict:
        return dict(
            batch_id=self.batch_id,
            imported_at=self.imported_at.isoformat() if self.imported_at else None,
            stale=self.stale,
            requested=self.requested,
            status=self.status,
            alternatives=self.alternatives,
        )


def _covered(slots: list[AvailabilitySlot], start: date, end: date) -> bool:
    if any(s.status == "occupied" and s.start_date < end and start < s.end_date for s in slots):
        return False
    cursor = start
    for slot in sorted((s for s in slots if s.status == "available"), key=lambda s: s.start_date):
        if slot.start_date <= cursor < slot.end_date:
            cursor = max(cursor, slot.end_date)
            if cursor >= end:
                return True
    return False


def check_availability(
    db: Session,
    *,
    property_query: str | None,
    arrival: str | None,
    departure: str | None,
    guests: int | None,
) -> AvailabilityResult:
    batch = latest_batch(db)
    age = datetime.now(UTC) - aware(batch.created_at) if batch else None
    stale = batch is None or age > timedelta(hours=get_settings().availability_max_age_hours)
    base = dict(
        batch_id=batch.id if batch else None,
        imported_at=batch.created_at if batch else None,
        stale=stale,
        requested=property_query or "",
        alternatives=[],
    )
    if stale or not arrival or not departure or not guests or not property_query:
        return AvailabilityResult(**base, status="manual_check")
    try:
        start, end = _date(arrival), _date(departure)
    except ValueError:
        return AvailabilityResult(**base, status="manual_check")
    if start < date.today() or start >= end or guests < 1:
        return AvailabilityResult(**base, status="manual_check")
    slots = list(db.scalars(select(AvailabilitySlot).where(AvailabilitySlot.batch_id == batch.id)))
    properties: dict[str, list[AvailabilitySlot]] = {}
    for slot in slots:
        properties.setdefault(slot.property_id, []).append(slot)
    needle = property_query.casefold().strip()
    matches = [
        key
        for key, values in properties.items()
        if key.casefold() == needle or values[0].property_name.casefold() == needle
    ]
    if len(matches) != 1:
        return AvailabilityResult(**base, status="ambiguous_property")
    requested = matches[0]
    property_slots = properties[requested]
    requested_status = (
        "available"
        if property_slots[0].capacity >= guests and _covered(property_slots, start, end)
        else "unavailable"
    )
    alternatives = [
        dict(property_id=key, name=values[0].property_name, capacity=values[0].capacity)
        for key, values in properties.items()
        if key != requested and values[0].capacity >= guests and _covered(values, start, end)
    ]
    alternatives.sort(key=lambda item: (item["capacity"], item["name"]))
    return AvailabilityResult(
        **{
            **base,
            "requested": requested,
            "status": requested_status,
            "alternatives": alternatives[:10],
        }
    )


def env_csv_mapping() -> dict[str, str]:
    raw = get_settings().csv_column_map
    return json.loads(raw) if raw else {}
