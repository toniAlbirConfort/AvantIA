"""Reports catalogue: HTML reports stored in the app plus tariff-lab studies with a PDF."""

from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models import Report, TariffStudy, User

MAX_REPORT_BYTES = 5_000_000
REPORTABLE_STUDY_STATUSES = ("completed", "partial")
SOURCES = ("claude", "app")


class ReportError(ValueError):
    pass


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _report_item(report: Report, authors: dict[int, str]) -> dict:
    return {
        "key": f"report-{report.id}",
        "id": report.id,
        "kind": "document",
        "source": report.source,
        "title": report.title,
        "description": report.description,
        "created_at": _iso(report.created_at),
        "created_by": authors.get(report.created_by_id) if report.created_by_id else None,
        "original_url": report.original_url,
    }


def _study_item(study: TariffStudy, authors: dict[int, str]) -> dict:
    period = f"{study.period_start:%d/%m/%Y} – {study.period_end:%d/%m/%Y}"
    groups = ", ".join(study.tariff_names or [])
    description = f"Estudio de tarifas {period} · {study.guests} huéspedes · {study.nights} noches"
    if groups:
        description += f" · {groups}"
    return {
        "key": f"study-{study.id}",
        "id": study.id,
        "kind": "tariff_study",
        "source": "app",
        "title": study.name,
        "description": description,
        "created_at": _iso(study.completed_at or study.created_at),
        "created_by": authors.get(study.created_by_id) if study.created_by_id else None,
        "status": study.status,
    }


def list_reports(db: Session) -> list[dict]:
    reports = db.scalars(select(Report).order_by(desc(Report.created_at)).limit(200)).all()
    studies = db.scalars(
        select(TariffStudy)
        .where(TariffStudy.status.in_(REPORTABLE_STUDY_STATUSES))
        .order_by(desc(TariffStudy.id))
        .limit(200)
    ).all()
    author_ids = {row.created_by_id for row in [*reports, *studies] if row.created_by_id}
    authors = (
        dict(db.execute(select(User.id, User.username).where(User.id.in_(author_ids))).all())
        if author_ids else {}
    )
    items = [_report_item(row, authors) for row in reports]
    items += [_study_item(row, authors) for row in studies]
    items.sort(key=lambda item: item["created_at"] or "", reverse=True)
    return items


def report_document(report: Report) -> str:
    """Return a complete HTML document; links open outside the embedded viewer."""
    body = report.html_body
    base = '<base target="_blank">'
    if re.search(r"<html[\s>]", body, re.IGNORECASE):
        if re.search(r"<head[\s>]", body, re.IGNORECASE):
            return re.sub(r"(<head[^>]*>)", r"\1" + base, body, count=1, flags=re.IGNORECASE)
        return body
    return (
        "<!doctype html><html lang=\"es\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"{base}</head><body>{body}</body></html>"
    )


def create_report(
    db: Session, *, title: str, description: str | None, raw: bytes,
    source: str, original_url: str | None, user_id: int | None,
) -> Report:
    title = title.strip()
    if not title:
        raise ReportError("El informe necesita un título")
    if len(title) > 200:
        raise ReportError("El título no puede superar 200 caracteres")
    if source not in SOURCES:
        raise ReportError("Origen de informe no válido")
    if len(raw) > MAX_REPORT_BYTES:
        raise ReportError("El archivo supera el máximo de 5 MB")
    try:
        html_body = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ReportError("El archivo debe ser HTML en UTF-8") from exc
    if not html_body.strip():
        raise ReportError("El archivo está vacío")
    original_url = (original_url or "").strip() or None
    if original_url and not original_url.startswith("https://"):
        raise ReportError("El enlace original debe empezar por https://")
    report = Report(
        title=title,
        description=(description or "").strip() or None,
        source=source,
        html_body=html_body,
        original_url=original_url,
        created_by_id=user_id,
    )
    db.add(report)
    db.flush()
    return report
