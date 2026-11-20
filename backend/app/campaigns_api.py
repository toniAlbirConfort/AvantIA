"""HTTP endpoints of the win-back campaigns (see app/campaigns.py).

Nothing here sends email: campaigns are prepared, their audience frozen and every email
previewed. The only public endpoint is the unsubscribe link printed in each email.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.auth import current_user
from app.campaigns import (
    DEFAULT_CONTENT,
    EXCLUSION_REASONS,
    FALLBACK_LANGUAGE,
    LANGUAGE_NAMES,
    LANGUAGES,
    VARIABLES,
    CampaignError,
    audience_summary,
    build_audience,
    default_name,
    default_offer,
    render,
    sender,
    unsubscribe,
    unsubscribe_page,
    validate_content,
    validate_offer,
)
from app.db import get_db
from app.models import AuditEvent, MailingCampaign, MailingRecipient, User

router = APIRouter()
Db = Annotated[Session, Depends(get_db)]
Operator = Annotated[User, Depends(current_user)]


class FiltersIn(BaseModel):
    target_year: int | None = Field(default=None, ge=2000, le=2100)
    base_years: list[int] = Field(default_factory=list, max_length=12)
    min_stays: int = Field(default=1, ge=1, le=20)
    channel: str | None = Field(default=None, max_length=40)
    contact: Literal["any", "email", "phone", "opt_in"] = "any"
    q: str = Field(default="", max_length=150)
    month_from: int | None = Field(default=None, ge=1, le=12)
    month_to: int | None = Field(default=None, ge=1, le=12)


class CampaignIn(BaseModel):
    name: str | None = Field(default=None, max_length=150)
    filters: FiltersIn


class CampaignPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    content: dict[str, dict[str, str]] | None = None
    offer: dict[str, str] | None = None


class RecipientPatch(BaseModel):
    excluded: bool


def _campaign(db: Session, campaign_id: int) -> MailingCampaign:
    campaign = db.get(MailingCampaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "Campaña no encontrada")
    return campaign


def _detail(db: Session, campaign: MailingCampaign) -> dict:
    author = db.get(User, campaign.created_by_id) if campaign.created_by_id else None
    return {
        "id": campaign.id,
        "name": campaign.name,
        "status": campaign.status,
        "filters": campaign.filters,
        "content": campaign.content,
        "offer": campaign.offer,
        "created_by": author.username if author else None,
        "created_at": campaign.created_at,
        "updated_at": campaign.updated_at,
        "audience_built_at": campaign.audience_built_at,
        "audience": audience_summary(db, campaign),
        "sender": sender(),
        "languages": [{"code": code, "label": LANGUAGE_NAMES[code]} for code in LANGUAGES],
        "fallback_language": FALLBACK_LANGUAGE,
        "variables": [{"name": name, "label": label} for name, label in VARIABLES.items()],
        "defaults": DEFAULT_CONTENT,
    }


def _audit(db: Session, user: User, action: str, campaign: MailingCampaign, **detail) -> None:
    db.add(AuditEvent(
        actor_user_id=user.id, action=action,
        detail={"campaign_id": campaign.id, "name": campaign.name, **detail},
    ))


@router.get("/api/campaigns")
def list_campaigns(db: Db, user: Operator) -> dict:
    campaigns = db.scalars(
        select(MailingCampaign).order_by(MailingCampaign.updated_at.desc())
    ).all()
    counts: dict[int, dict[str, int]] = {}
    for campaign_id, status, total in db.execute(
        select(MailingRecipient.campaign_id, MailingRecipient.status, func.count())
        .group_by(MailingRecipient.campaign_id, MailingRecipient.status)
    ):
        counts.setdefault(campaign_id, {})[status] = total
    authors = dict(db.execute(select(User.id, User.username)).all())
    return {"items": [
        {
            "id": item.id, "name": item.name, "status": item.status,
            "filters": item.filters,
            "languages": sorted(item.content or {}),
            "offer": item.offer,
            "included": counts.get(item.id, {}).get("included", 0),
            "candidates": sum(counts.get(item.id, {}).values()),
            "created_by": authors.get(item.created_by_id),
            "created_at": item.created_at, "updated_at": item.updated_at,
        }
        for item in campaigns
    ]}


@router.post("/api/campaigns")
def create_campaign(payload: CampaignIn, db: Db, user: Operator) -> dict:
    filters = payload.filters.model_dump()
    filters["target_year"] = filters["target_year"] or date.today().year
    if not filters["base_years"]:
        raise HTTPException(400, "Marca al menos un año en «Se alojaron en»")
    campaign = MailingCampaign(
        name=(payload.name or "").strip() or default_name(filters),
        status="draft",
        filters=filters,
        content={code: dict(fields) for code, fields in DEFAULT_CONTENT.items()},
        offer=default_offer(filters["target_year"]),
        created_by_id=user.id,
    )
    db.add(campaign)
    db.flush()
    try:
        build_audience(db, campaign)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    _audit(db, user, "campaign.created", campaign, filters=filters | {"q": bool(filters["q"])})
    db.commit()
    return _detail(db, campaign)


@router.get("/api/campaigns/{campaign_id}")
def get_campaign(campaign_id: int, db: Db, user: Operator) -> dict:
    return _detail(db, _campaign(db, campaign_id))


@router.patch("/api/campaigns/{campaign_id}")
def update_campaign(campaign_id: int, payload: CampaignPatch, db: Db, user: Operator) -> dict:
    campaign = _campaign(db, campaign_id)
    try:
        if payload.name is not None:
            campaign.name = payload.name.strip()
        if payload.content is not None:
            campaign.content = validate_content(payload.content)
        if payload.offer is not None:
            campaign.offer = validate_offer(payload.offer)
    except CampaignError as exc:
        raise HTTPException(400, str(exc)) from exc
    campaign.updated_at = datetime.now(UTC)
    _audit(db, user, "campaign.updated", campaign)
    db.commit()
    return _detail(db, campaign)


@router.post("/api/campaigns/{campaign_id}/audience")
def rebuild_audience(campaign_id: int, db: Db, user: Operator) -> dict:
    campaign = _campaign(db, campaign_id)
    try:
        build_audience(db, campaign)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    _audit(db, user, "campaign.audience_built", campaign)
    db.commit()
    return _detail(db, campaign)


@router.delete("/api/campaigns/{campaign_id}")
def delete_campaign(campaign_id: int, db: Db, user: Operator) -> dict:
    campaign = _campaign(db, campaign_id)
    _audit(db, user, "campaign.deleted", campaign)
    db.query(MailingRecipient).filter(MailingRecipient.campaign_id == campaign.id).delete()
    db.delete(campaign)
    db.commit()
    return {"ok": True}


def _recipient_view(row: MailingRecipient) -> dict:
    return {
        "id": row.id, "display_name": row.display_name, "first_name": row.first_name,
        "email": row.email, "language": row.language, "language_raw": row.language_raw,
        "status": row.status, "exclusion_reason": row.exclusion_reason,
        "exclusion_label": EXCLUSION_REASONS.get(row.exclusion_reason or ""),
        "avantio_id": row.avantio_id, **(row.data or {}),
    }


@router.get("/api/campaigns/{campaign_id}/recipients")
def campaign_recipients(
    campaign_id: int,
    db: Db,
    user: Operator,
    status: Literal["included", "excluded", "all"] = "included",
    reason: str | None = Query(default=None, max_length=30),
    language: str | None = Query(default=None, max_length=10),
    q: str = Query(default="", max_length=150),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    campaign = _campaign(db, campaign_id)
    query = select(MailingRecipient).where(MailingRecipient.campaign_id == campaign.id)
    if status != "all":
        query = query.where(MailingRecipient.status == status)
    if reason:
        query = query.where(MailingRecipient.exclusion_reason == reason)
    if language:
        query = query.where(MailingRecipient.language == language)
    if q.strip():
        needle = f"%{q.strip().lower()}%"
        query = query.where(or_(
            func.lower(MailingRecipient.display_name).like(needle),
            func.lower(MailingRecipient.email).like(needle),
        ))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(MailingRecipient.id).offset(offset).limit(limit)).all()
    return {"items": [_recipient_view(row) for row in rows], "total": total,
            "offset": offset, "limit": limit}


@router.patch("/api/campaigns/{campaign_id}/recipients/{recipient_id}")
def update_recipient(
    campaign_id: int, recipient_id: int, payload: RecipientPatch, db: Db, user: Operator
) -> dict:
    campaign = _campaign(db, campaign_id)
    row = db.get(MailingRecipient, recipient_id)
    if row is None or row.campaign_id != campaign.id:
        raise HTTPException(404, "Destinatario no encontrado")
    if payload.excluded and row.status == "included":
        row.status, row.exclusion_reason = "excluded", "manual"
    elif not payload.excluded and row.exclusion_reason == "manual":
        duplicate = db.scalar(
            select(MailingRecipient.id).where(
                MailingRecipient.campaign_id == campaign.id,
                MailingRecipient.status == "included",
                MailingRecipient.email == row.email,
            )
        )
        if duplicate:
            raise HTTPException(400, "Otro destinatario de la campaña ya tiene ese email")
        row.status, row.exclusion_reason = "included", None
    elif not payload.excluded:
        raise HTTPException(
            400, "Solo se pueden volver a incluir los huéspedes excluidos a mano"
        )
    campaign.updated_at = datetime.now(UTC)
    _audit(db, user, "campaign.recipient_" + ("excluded" if payload.excluded else "included"),
           campaign, recipient_id=row.id)
    db.commit()
    return {"recipient": _recipient_view(row), "audience": audience_summary(db, campaign)}


@router.get("/api/campaigns/{campaign_id}/preview")
def preview_campaign(
    campaign_id: int,
    db: Db,
    user: Operator,
    language: str = Query(default=FALLBACK_LANGUAGE, max_length=10),
    recipient_id: int | None = None,
) -> dict:
    campaign = _campaign(db, campaign_id)
    recipient = None
    if recipient_id is not None:
        recipient = db.get(MailingRecipient, recipient_id)
        if recipient is None or recipient.campaign_id != campaign.id:
            raise HTTPException(404, "Destinatario no encontrado")
    elif language not in LANGUAGES:
        raise HTTPException(400, "Idioma no admitido")
    try:
        return render(campaign, language=language, recipient=recipient)
    except CampaignError as exc:
        raise HTTPException(400, str(exc)) from exc


class PreviewIn(BaseModel):
    """Unsaved changes, so the preview follows the editor before the operator saves."""

    content: dict[str, dict[str, str]]
    offer: dict[str, str]
    language: str = Field(default=FALLBACK_LANGUAGE, max_length=10)
    recipient_id: int | None = None


@router.post("/api/campaigns/{campaign_id}/preview")
def preview_draft(campaign_id: int, payload: PreviewIn, db: Db, user: Operator) -> dict:
    campaign = _campaign(db, campaign_id)
    try:
        draft = MailingCampaign(
            id=campaign.id, name=campaign.name,
            content=validate_content(payload.content), offer=validate_offer(payload.offer),
        )
    except CampaignError as exc:
        raise HTTPException(400, str(exc)) from exc
    recipient = None
    if payload.recipient_id is not None:
        recipient = db.get(MailingRecipient, payload.recipient_id)
        if recipient is None or recipient.campaign_id != campaign.id:
            raise HTTPException(404, "Destinatario no encontrado")
    elif payload.language not in LANGUAGES:
        raise HTTPException(400, "Idioma no admitido")
    try:
        return render(draft, language=payload.language, recipient=recipient)
    except CampaignError as exc:
        raise HTTPException(400, str(exc)) from exc


def _recipient_by_token(db: Session, token: str) -> MailingRecipient | None:
    if not token or len(token) > 64:
        return None
    return db.scalar(select(MailingRecipient).where(MailingRecipient.token == token))


@router.get("/api/public/campaigns/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe_form(token: str, db: Db) -> HTMLResponse:
    recipient = _recipient_by_token(db, token)
    if recipient is None:
        return HTMLResponse(unsubscribe_page(FALLBACK_LANGUAGE, state="invalid"), 404)
    return HTMLResponse(unsubscribe_page(recipient.language, state="confirm", token=token))


@router.post("/api/public/campaigns/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe_confirm(token: str, db: Db) -> HTMLResponse:
    """The confirmation form and RFC 8058 one-click unsubscribe both land here."""
    recipient = _recipient_by_token(db, token)
    if recipient is None or unsubscribe(db, token) is None:
        return HTMLResponse(unsubscribe_page(FALLBACK_LANGUAGE, state="invalid"), 404)
    db.add(AuditEvent(
        action="campaign.unsubscribed",
        detail={"campaign_id": recipient.campaign_id, "recipient_id": recipient.id},
    ))
    db.commit()
    return HTMLResponse(unsubscribe_page(recipient.language, state="done"))
