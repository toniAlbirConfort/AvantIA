"""Avantio customer export: tolerant parser, upsert import and read-only views.

The Avantio "Listado de clientes" export is a `;` separated file without quoting.
Records end with LF, while free-text notes may contain CR or CRLF line breaks and
even stray `;`. The parser therefore splits records on LF only and folds any extra
separators back into the notes column, the only free-text field of the export.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date, datetime

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from app.models import Customer, ImportBatch, now
from app.planning import MAX_BYTES, latest_source_batch, reservations_view

DATASET = "customers"
PARSER_VERSION = "1"
NOTES_COLUMN = "Observaciones"
# Export column -> model field.
COLUMNS = {
    "Id cliente": "avantio_id",
    "Nombre": "first_name",
    "Primer apellido": "last_name",
    "Segundo apellido": "second_last_name",
    "Fecha nacimiento": "birth_date",
    "Sexo": "sex",
    "Nacionalidad (País)": "nationality",
    "Calle": "street",
    "Número": "street_number",
    "Puerta": "door",
    "Código postal": "postal_code",
    "Ciudad": "city",
    "Provincia": "province",
    "País": "country",
    "Región": "region",
    "DNI/ID": "document_number",
    "E-mail": "email",
    "E-mail alternativo": "email_alt",
    "Teléfono": "phone",
    "Teléfono alternativo 1": "phone_alt1",
    "Teléfono alternativo 2": "phone_alt2",
    "Idioma del cliente": "language",
    "Clase": "customer_class",
    "Tipo cliente": "customer_type",
    "Cuenta bancaria": "bank_account_masked",
    "Código fiscal": "tax_code",
    "Cuenta contable": "accounting_account",
    NOTES_COLUMN: "notes",
    "Fecha de alta": "registered_on",
    "Newsletters": "email_opt_in",
    "Periodo de estancia": "stay_period",
}
REQUIRED = {"Id cliente", "Nombre", "Primer apellido", "E-mail", NOTES_COLUMN, "Fecha de alta"}
DATE_FIELDS = {"birth_date", "registered_on"}
PHONE_FIELDS = {"phone", "phone_alt1", "phone_alt2"}
LENGTHS = {
    column.name: column.type.length
    for column in Customer.__table__.columns
    if getattr(column.type, "length", None)
}
PLACEHOLDERS = {"-", "--", ".", "?"}
CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
EXPIRY_SUFFIX = re.compile(r"\s+\d{2}\s*/\s*\d{2,4}\s*$")
SUMMARY_FIELDS = (
    "avantio_id", "display_name", "email", "phone", "country", "document_number",
    "customer_class",
)


def fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).split())


def _luhn(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        number = int(char)
        if index % 2:
            number = number * 2 - 9 if number > 4 else number * 2
        total += number
    return total % 10 == 0


def redact_cards(text: str) -> str:
    """Replace anything that looks like a payment card number with its last four digits."""

    def replace(match: re.Match) -> str:
        digits = re.sub(r"\D", "", match.group())
        if 13 <= len(digits) <= 19 and _luhn(digits):
            return f"[tarjeta ••••{digits[-4:]}]"
        return match.group()

    return CARD.sub(replace, text)


def mask_account(value: str) -> str:
    """Keep only the last four characters of a bank account or card number."""
    compact = re.sub(r"[^0-9A-Za-z]", "", EXPIRY_SUFFIX.sub("", value))
    return f"••••{compact[-4:]}" if len(compact) > 4 else "••••"


def _clean(field: str, value: str) -> str | None:
    value = value.strip()
    if field != "notes":
        value = " ".join(value.split())
    if not value or value in PLACEHOLDERS or (field in PHONE_FIELDS and value.strip("0 ") == ""):
        return None
    return value


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%d/%m/%Y").date()


def _customer(values: dict[str, str], source_row: int, warnings: list[dict]) -> dict | None:
    row: dict = {}
    for column, field in COLUMNS.items():
        row[field] = _clean(field, values.get(column, ""))
    customer_id = row["avantio_id"]
    if not customer_id or not customer_id.isdigit():
        warnings.append({"code": "missing_customer_id", "source_row": source_row})
        return None
    for field in DATE_FIELDS:
        try:
            row[field] = _parse_date(row[field])
        except ValueError:
            warnings.append(
                {"code": "invalid_date", "customer_id": customer_id, "field": field,
                 "source_row": source_row}
            )
            row[field] = None
    opt_in = fold(row["email_opt_in"] or "")
    row["email_opt_in"] = True if opt_in == "si" else False if opt_in == "no" else None
    if row["bank_account_masked"]:
        row["bank_account_masked"] = mask_account(row["bank_account_masked"])
    if row["notes"]:
        row["notes"] = redact_cards(row["notes"].replace("\r", "\n")).strip()
    for field, value in row.items():
        limit = LENGTHS.get(field)
        if isinstance(value, str) and limit and len(value) > limit:
            row[field] = value[:limit]
    name = " ".join(
        part for part in (row["first_name"], row["last_name"], row["second_last_name"]) if part
    )
    row["display_name"] = (name or f"Cliente {customer_id}")[:255]
    # Ignore leading punctuation such as "( NO USAR)" so those records sort by their name.
    row["sort_name"] = re.sub(r"^\W+", "", fold(row["display_name"]))[:255]
    phones = [row[field] for field in sorted(PHONE_FIELDS) if row[field]]
    row["search_text"] = fold(
        " ".join(
            filter(
                None,
                [
                    row["display_name"], row["email"], row["email_alt"],
                    row["document_number"], customer_id,
                    *phones, *(re.sub(r"\D", "", phone) for phone in phones),
                ],
            )
        )
    )
    row["source_row"] = source_row
    row["row_hash"] = _hash(row)
    return row


def _hash(row: dict) -> str:
    canonical = {
        key: value.isoformat() if isinstance(value, date) else value
        for key, value in row.items()
        if key not in {"source_row", "row_hash"}
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def parse_customers(raw: bytes) -> tuple[list[dict], list[dict]]:
    if len(raw) > MAX_BYTES:
        raise ValueError("El CSV supera 10 MB")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("El CSV debe estar codificado en UTF-8") from exc
    lines = content.replace("\r\n", "\r").split("\n")
    if len(lines) < 2:
        raise ValueError("Falta la fila de título de la exportación")
    title = lines[0].replace("\r", "").strip().rstrip(";").strip()
    if title != "Lista" and not title.startswith("Listado de clientes"):
        raise ValueError("El archivo no es un listado de clientes")
    header = [column.strip() for column in lines[1].replace("\r", "").split(";")]
    if not REQUIRED.issubset(header):
        raise ValueError("Faltan columnas obligatorias de la exportación de clientes")
    if len(set(header)) != len(header):
        raise ValueError("Hay columnas duplicadas")
    notes_index = header.index(NOTES_COLUMN)
    rows: dict[str, dict] = {}
    warnings: list[dict] = []
    for index, line in enumerate(lines[2:], start=3):
        if not line.strip():
            continue
        parts = line.split(";")
        extra = len(parts) - len(header)
        if extra < 0:
            warnings.append({"code": "short_row", "source_row": index})
            continue
        if extra:
            parts = [
                *parts[:notes_index],
                ";".join(parts[notes_index : notes_index + extra + 1]),
                *parts[notes_index + extra + 1 :],
            ]
        row = _customer(dict(zip(header, parts, strict=True)), index, warnings)
        if row is None:
            continue
        if extra:
            warnings.append(
                {"code": "extra_separators", "customer_id": row["avantio_id"], "source_row": index}
            )
        if row["avantio_id"] in rows:
            warnings.append(
                {"code": "duplicate_customer", "customer_id": row["avantio_id"],
                 "source_row": index}
            )
        rows[row["avantio_id"]] = row
        if len(rows) > 200_000:
            raise ValueError("El CSV supera 200.000 clientes")
    if not rows:
        raise ValueError("CSV sin datos")
    return list(rows.values()), warnings


def _existing(db: Session) -> dict[str, tuple[int, str]]:
    return {
        avantio_id: (id_, row_hash)
        for avantio_id, id_, row_hash in db.execute(
            select(Customer.avantio_id, Customer.id, Customer.row_hash)
        )
    }


def _changes(rows: list[dict], existing: dict[str, tuple[int, str]]) -> list[dict]:
    changes = []
    for row in rows:
        previous = existing.get(row["avantio_id"])
        if previous is None:
            changes.append({"key": row["avantio_id"], "kind": "added"})
        elif previous[1] != row["row_hash"]:
            changes.append({"key": row["avantio_id"], "kind": "changed"})
    return changes


def preview_customers(db: Session, raw: bytes) -> dict:
    rows, warnings = parse_customers(raw)
    existing = _existing(db)
    checksum = hashlib.sha256(raw).hexdigest()
    current = latest_source_batch(db, DATASET)
    incoming = {row["avantio_id"] for row in rows}
    carried = sum(1 for key in existing if key not in incoming)
    return {
        "dataset": DATASET,
        "checksum": checksum,
        "rows": len(rows),
        "effective_rows": len(rows) + carried,
        "carried_rows": carried,
        "duplicate_of": current.id if current and current.checksum == checksum else None,
        "coverage": None,
        "warnings": warnings,
        "changes": _changes(rows, existing),
    }


def commit_customers(
    db: Session,
    raw: bytes,
    filename: str,
    actor_user_id: int | None,
    expected_checksum: str,
    source: str = "upload",
) -> ImportBatch:
    checksum = hashlib.sha256(raw).hexdigest()
    if checksum != expected_checksum:
        raise ValueError("El archivo cambió desde la vista previa")
    current = latest_source_batch(db, DATASET)
    if current and current.checksum == checksum:
        return current
    batch = ImportBatch(
        dataset=DATASET,
        source=source,
        filename=filename[:255],
        checksum=checksum,
        status="invalid",
        actor_user_id=actor_user_id,
        parser_version=PARSER_VERSION,
    )
    db.add(batch)
    db.flush()
    try:
        rows, warnings = parse_customers(raw)
        existing = _existing(db)
        changes = _changes(rows, existing)
        changed = {change["key"] for change in changes}
        stamp = now()
        inserts, updates = [], []
        for row in rows:
            if row["avantio_id"] not in changed:
                continue
            values = {**row, "batch_id": batch.id, "updated_at": stamp}
            if row["avantio_id"] in existing:
                updates.append({**values, "id": existing[row["avantio_id"]][0]})
            else:
                inserts.append({**values, "created_at": stamp})
        for start in range(0, len(inserts), 2000):
            db.execute(insert(Customer), inserts[start : start + 2000])
        for start in range(0, len(updates), 2000):
            db.execute(update(Customer), updates[start : start + 2000])
        batch.row_count = len(rows)
        batch.warnings = warnings
        batch.changes = changes
        batch.status = "valid"
    except ValueError as exc:
        batch.error = str(exc)[:1000]
    db.commit()
    return batch


def _summary(customer: Customer) -> dict:
    return {field: getattr(customer, field) for field in SUMMARY_FIELDS}


def customers_view(db: Session, *, q: str = "", offset: int = 0, limit: int = 40) -> dict:
    tokens = fold(q).split()
    query = select(Customer).where(
        *(Customer.search_text.contains(token, autoescape=True) for token in tokens)
    )
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = db.scalars(
        query.order_by(Customer.sort_name, Customer.id).offset(offset).limit(limit)
    ).all()
    batch = latest_source_batch(db, DATASET)
    return {
        "items": [_summary(item) for item in items],
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_data": total > 0 or db.scalar(select(Customer.id).limit(1)) is not None,
        "last_import_at": batch.created_at if batch else None,
    }


def _customer_reservations(db: Session, customer: Customer) -> list[dict]:
    """Reservations whose guest name matches the customer's full name.

    Reservation snapshots do not carry the Avantio customer id, so the link is made
    by exact (accent and case insensitive) name and flagged as such in the view.
    """
    short = " ".join(part for part in (customer.first_name, customer.last_name) if part)
    if not short:
        return []
    names = {fold(short), fold(customer.display_name)}
    found = reservations_view(db, q=short, offset=0, limit=10_000)["items"]
    return [item for item in found if fold(item["display_name"]) in names]


def customer_detail(db: Session, avantio_id: str) -> dict | None:
    customer = db.scalar(select(Customer).where(Customer.avantio_id == avantio_id))
    if customer is None:
        return None
    hidden = {"id", "batch_id", "row_hash", "sort_name", "search_text"}
    data = {
        column.name: getattr(customer, column.name)
        for column in Customer.__table__.columns
        if column.name not in hidden
    }
    batch = db.get(ImportBatch, customer.batch_id)
    return {
        **data,
        "source": {
            "batch_id": customer.batch_id,
            "filename": batch.filename if batch else None,
            "imported_at": batch.created_at if batch else None,
        },
        "reservations": _customer_reservations(db, customer),
    }
