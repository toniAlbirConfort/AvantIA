"""Reservation incidents found in the Avantio data, for the "Incidencias" view.

Recomputed from the consolidated reservations of the latest valid import with the same rules
as the importer warnings (``planning._warnings``), so "current" always means relative to
today: a stay or an overlap that has not ended yet. Every incident has a stable ``key`` that
survives new imports while the incident persists, so a person can mark it as reviewed.

Kinds:
* ``overlap``: two blocking reservations of the same apartment share nights.
* ``amounts``: a sold stay whose total does not match paid + pending, or net + tax.
* ``inactive``: a future stay or inquiry in an apartment marked inactive in the catalogue.
* ``unknown``: a reservation in an apartment missing from the catalogue.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    PropertySnapshot,
    ReservationDetail,
    ReservationIssueReview,
    ReservationSnapshot,
    User,
    now,
)
from app.planning import (
    RESERVATION_FIELDS,
    _warnings,
    consolidated_reservations,
    details_for,
    latest_source_batch,
)

KINDS = ("overlap", "amounts", "inactive", "unknown")
NOTE_MAX = 300
# A difference within this margin is explained (rounding in Avantio's percentage calculator).
EXPLAIN_TOLERANCE = Decimal("1.00")
PERCENT = r"([+-]?\s*\d{1,2}(?:[.,]\d{1,2})?)\s*%\s*(?:de\s+)?"
COMMISSION_NOTE = re.compile(PERCENT + r"comisi", re.IGNORECASE)
DISCOUNT_NOTE = re.compile(PERCENT + r"(?:dto|desc)", re.IGNORECASE)


class ReservationIssueError(ValueError):
    pass


def _money(value: Decimal | None) -> float:
    return float(value or 0)


def _reservation(row: ReservationSnapshot, detail: ReservationDetail | None = None) -> dict:
    return {
        **_detail_view(detail),
        "locator": row.locator,
        "property_id": row.property_id,
        "status": row.status,
        "status_original": row.status_original,
        "created_on": row.created_on,
        "start_date": row.start_date,
        "end_date": row.end_date,
        "nights": row.nights,
        "guests": row.adults + row.children,
        "display_name": row.display_name,
        "portal": row.portal,
        "total": _money(row.total),
        "net": _money(row.net),
        "tax": _money(row.tax),
        "paid": _money(row.paid),
        "pending": _money(row.pending),
    }


def _detail_view(detail: ReservationDetail | None) -> dict:
    """Context from the extra export columns (never emails: they are only for matching)."""
    if detail is None:
        return {"comments": None, "handled_by": None, "agent_locator": None}
    return {
        "comments": detail.comments,
        "handled_by": detail.handled_by,
        "agent_locator": detail.agent_locator,
        "commission": _money(detail.commission) if detail.commission else None,
        "extras_total": _money(detail.extras_total) if detail.extras_total else None,
        "invoice_total": _money(detail.invoice_total) if detail.invoice_total else None,
        "invoice_number": detail.invoice_number,
    }


def _percentages(pattern: re.Pattern, text: str | None) -> list[Decimal]:
    found = []
    for value in pattern.findall(text or ""):
        try:
            found.append(abs(Decimal(value.replace(" ", "").replace(",", "."))))
        except ArithmeticError:
            continue
    return [value for value in dict.fromkeys(found) if 0 < value < 100]


def explain_balance(row: ReservationSnapshot, detail: ReservationDetail | None) -> dict | None:
    """Why paid + pending differs from the total, when the export says so with certainty.

    Avantio's pending amount is what the customer pays; when the channel's commission is
    entered as an offer, the total is what is left after it. Checked in this order: the
    commission column, then a percentage noted in the comments ("+18% COMISION",
    "11% DTO"), then extras.
    """
    if detail is None:
        return None
    asked, total = row.paid + row.pending, row.total

    def close(a: Decimal, b: Decimal) -> bool:
        return abs(a - b) <= EXPLAIN_TOLERANCE

    if detail.commission and close(asked - detail.commission, total):
        return {
            "source": "commission",
            "text": f"Comisión del canal registrada en Avantio ({detail.commission:.2f} €)",
        }
    for value in _percentages(COMMISSION_NOTE, detail.comments):
        if close(asked * (1 - value / 100), total):
            return {
                "source": "comment_commission",
                "text": f"Comisión del {value.normalize():f} % anotada en los comentarios",
            }
    for value in _percentages(DISCOUNT_NOTE, detail.comments):
        if close(asked * (1 - value / 100), total) or close(total * (1 - value / 100), asked):
            return {
                "source": "comment_discount",
                "text": f"Descuento del {value.normalize():f} % anotado en los comentarios",
            }
    if detail.extras_total and close(asked, total + detail.extras_total):
        return {
            "source": "extras",
            "text": f"Extras de {detail.extras_total:.2f} € fuera del total",
        }
    return None


def _property(row: PropertySnapshot | None, property_id: str) -> dict:
    if row is None:
        return {"id": property_id, "name": None, "known": False}
    return {
        "id": row.property_id,
        "name": row.name,
        "known": True,
        "active": row.active,
        "status_original": row.status_original,
        "building": row.building,
        "city": row.city,
        "category": row.category,
    }


def _row_for_rules(row: ReservationSnapshot) -> dict:
    return {
        **{field: getattr(row, field) for field in RESERVATION_FIELDS},
        "source_batch_id": row.source_batch_id or row.batch_id,
    }


def _reviews(db: Session) -> dict[str, dict]:
    users = {user.id: user.username for user in db.scalars(select(User)).all()}
    return {
        item.issue_key: {
            "by": users.get(item.reviewed_by_id) if item.reviewed_by_id else None,
            "at": item.reviewed_at,
            "note": item.note,
        }
        for item in db.scalars(select(ReservationIssueReview)).all()
    }


def _empty(batch=None) -> dict:
    return {
        "batch": {"id": batch.id, "created_at": batch.created_at} if batch else None,
        **{kind: [] for kind in KINDS},
        "counts": {kind: {"total": 0, "current": 0, "pending": 0} for kind in KINDS},
    }


def collect(db: Session, today: date | None = None) -> dict:
    """Every incident of the current reservation data, newest problems first."""
    today = today or date.today()
    batch = latest_source_batch(db, "reservations")
    if batch is None:
        return _empty()
    rows = consolidated_reservations(db, batch)
    property_batch = latest_source_batch(db, "properties")
    properties = (
        list(
            db.scalars(
                select(PropertySnapshot).where(PropertySnapshot.batch_id == property_batch.id)
            )
        )
        if property_batch
        else []
    )
    by_property = {item.property_id: item for item in properties}
    warnings = _warnings(
        [_row_for_rules(row) for row in rows.values()], "reservations", properties, today
    )
    reviews = _reviews(db)
    result = _empty(batch)
    referenced = set()
    for warning in warnings:
        referenced.update(warning.get("locators") or [warning.get("locator")])
    details = details_for(db, referenced - {None})

    amounts: dict[str, dict] = {}
    for warning in warnings:
        code = warning["code"]
        if code == "overlap":
            first, second = (rows.get(locator) for locator in warning["locators"])
            if first is None or second is None:
                continue
            start = max(first.start_date, second.start_date)
            end = min(first.end_date, second.end_date)
            key = "overlap:" + "|".join(sorted(warning["locators"]))
            result["overlap"].append(
                {
                    "key": key,
                    "property": _property(
                        by_property.get(warning["property_id"]), warning["property_id"]
                    ),
                    "reservations": [
                        _reservation(first, details.get(first.locator)),
                        _reservation(second, details.get(second.locator)),
                    ],
                    "overlap_start": start,
                    "overlap_end": end,
                    "nights": (end - start).days,
                    "current": end > today,
                }
            )
            continue
        row = rows.get(warning.get("locator"))
        if row is None:
            continue
        if code in {"balance_mismatch", "tax_mismatch"}:
            item = amounts.setdefault(
                row.locator,
                {
                    "key": f"amounts:{row.locator}",
                    "reservation": _reservation(row, details.get(row.locator)),
                    "property": _property(by_property.get(row.property_id), row.property_id),
                    "balance_difference": None,
                    "tax_difference": None,
                    "explanation": None,
                    "current": row.end_date >= today,
                },
            )
            if code == "balance_mismatch":
                item["balance_difference"] = _money(row.paid + row.pending - row.total)
                item["explanation"] = explain_balance(row, details.get(row.locator))
            else:
                item["tax_difference"] = _money(row.net + row.tax - row.total)
        elif code in {"inactive_property", "unknown_property"}:
            kind = "inactive" if code == "inactive_property" else "unknown"
            result[kind].append(
                {
                    "key": f"{kind}:{row.locator}",
                    "reservation": _reservation(row, details.get(row.locator)),
                    "property": _property(by_property.get(row.property_id), row.property_id),
                    "current": row.end_date >= today,
                }
            )
    result["amounts"] = list(amounts.values())

    for kind in KINDS:
        items = result[kind]
        for item in items:
            item["review"] = reviews.get(item["key"])
        items.sort(key=_sort_key(kind), reverse=True)
        result["counts"][kind] = {
            "total": len(items),
            "current": sum(1 for item in items if item["current"]),
            "pending": sum(1 for item in items if needs_attention(item)),
            "explained": sum(1 for item in items if item.get("explanation")),
        }
    return result


def _sort_key(kind: str):
    """Current and unreviewed first, then the most relevant: largest gap, nearest dates."""

    def key(item: dict):
        weight = (item["current"], item["review"] is None, not item.get("explanation"))
        if kind == "overlap":
            return (*weight, item["overlap_end"])
        if kind == "amounts":
            gap = max(abs(item["balance_difference"] or 0), abs(item["tax_difference"] or 0))
            return (*weight, gap)
        return (*weight, item["reservation"]["end_date"])

    return key


def needs_attention(item: dict) -> bool:
    """Current, not reviewed and not explained by the export (what notifications count)."""
    return item["current"] and not item["review"] and not item.get("explanation")


def pending_summary(db: Session, today: date | None = None) -> dict[str, list[dict]]:
    """Current, unreviewed incidents by kind (what notifications count)."""
    data = collect(db, today)
    return {kind: [item for item in data[kind] if needs_attention(item)] for kind in KINDS}


def _check_key(key: str) -> str:
    key = (key or "").strip()
    kind = key.split(":", 1)[0]
    if kind not in KINDS or len(key) <= len(kind) + 1 or len(key) > 320:
        raise ReservationIssueError("Incidencia no válida")
    return key


def mark_reviewed(db: Session, key: str, user: User, note: str | None = None) -> dict:
    key = _check_key(key)
    note = (note or "").strip()[:NOTE_MAX] or None
    item = db.get(ReservationIssueReview, key)
    if item is None:
        item = ReservationIssueReview(issue_key=key)
        db.add(item)
    item.reviewed_by_id = user.id
    item.reviewed_at = now()
    item.note = note
    db.commit()
    return {"by": user.username, "at": item.reviewed_at, "note": item.note}


def unmark_reviewed(db: Session, key: str) -> None:
    key = _check_key(key)
    item = db.get(ReservationIssueReview, key)
    if item is not None:
        db.delete(item)
        db.commit()


def describe_dates(start: date | datetime, end: date | datetime) -> str:
    return f"{start:%d/%m/%Y}–{end:%d/%m/%Y}"
