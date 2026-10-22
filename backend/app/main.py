from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select, text
from sqlalchemy.orm import Session

from app.auth import admin_user, authenticate, current_user, hasher, issue_token
from app.availability import aware, import_csv, latest_batch
from app.avantio import availability_provider
from app.config import get_settings
from app.db import get_db
from app.ingest import enqueue, ingest
from app.models import (
    AuditEvent,
    AvailabilitySlot,
    BookingTask,
    Conversation,
    Draft,
    ImportBatch,
    MailCursor,
    Message,
    User,
    now,
)

app = FastAPI(title="AvantIA", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().cors_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Db = Annotated[Session, Depends(get_db)]
Operator = Annotated[User, Depends(current_user)]
Admin = Annotated[User, Depends(admin_user)]


class LoginIn(BaseModel):
    username: str
    password: str


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    password: str
    role: str = "operator"


class DraftIn(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    expected_draft_id: int | None = None


class AssignmentIn(BaseModel):
    user_id: int | None


class UnknownResolution(BaseModel):
    outcome: str  # sent | not_sent
    note: str = Field(min_length=3, max_length=500)


def _user(user: User) -> dict:
    return {"id": user.id, "username": user.username, "role": user.role, "active": user.active}


def _draft(draft: Draft) -> dict:
    return {
        "id": draft.id,
        "text": draft.text,
        "status": draft.status,
        "approved_by_id": draft.approved_by_id,
        "approved_at": draft.approved_at,
        "sent_at": draft.sent_at,
        "error": draft.error,
    }


def _live_analysis(db: Session, conv: Conversation) -> tuple[dict, bool]:
    analysis = dict(conv.analysis or {})
    if conv.kind == "booking" or not analysis:
        return analysis, False
    current = (
        availability_provider()
        .check(
            db,
            property_query=analysis.get("property_query"),
            arrival=analysis.get("arrival"),
            departure=analysis.get("departure"),
            guests=analysis.get("guests"),
        )
        .as_dict()
    )
    changed = current != analysis.get("availability")
    analysis["availability"] = current
    analysis["availability_changed"] = changed
    return analysis, changed


def _conversation(conv: Conversation, db: Session, detail: bool = False) -> dict:
    latest = db.scalar(
        select(Draft).where(Draft.conversation_id == conv.id).order_by(desc(Draft.id))
    )
    data = {
        "id": conv.id,
        "channel": conv.channel,
        "kind": conv.kind,
        "customer_name": conv.customer_name,
        "customer_address": conv.customer_address,
        "subject": conv.subject,
        "status": conv.status,
        "priority": conv.priority,
        "assigned_user_id": conv.assigned_user_id,
        "analysis": _live_analysis(db, conv)[0] if detail else conv.analysis,
        "created_at": conv.created_at,
        "updated_at": conv.updated_at,
        "last_inbound_at": conv.last_inbound_at,
        "draft": _draft(latest) if latest else None,
    }
    if detail:
        messages = db.scalars(
            select(Message).where(Message.conversation_id == conv.id).order_by(Message.id)
        ).all()
        data["messages"] = [
            {
                "id": item.id,
                "direction": item.direction,
                "body": item.body,
                "sender": item.sender,
                "subject": item.subject,
                "source_url": item.source_url,
                "provider_id": item.provider_id,
                "created_at": item.created_at,
            }
            for item in messages
        ]
        data["drafts"] = [
            _draft(item)
            for item in db.scalars(
                select(Draft).where(Draft.conversation_id == conv.id).order_by(Draft.id)
            ).all()
        ]
        task = db.scalar(select(BookingTask).where(BookingTask.conversation_id == conv.id))
        data["booking_task"] = (
            {"id": task.id, "status": task.status, "extracted": task.extracted} if task else None
        )
        data["audit"] = [
            {
                "action": a.action,
                "actor_user_id": a.actor_user_id,
                "detail": a.detail,
                "created_at": a.created_at,
            }
            for a in db.scalars(
                select(AuditEvent)
                .where(AuditEvent.conversation_id == conv.id)
                .order_by(desc(AuditEvent.id))
                .limit(50)
            ).all()
        ]
    return data


def _get_conv(db: Session, id: int) -> Conversation:
    conv = db.get(Conversation, id)
    if not conv:
        raise HTTPException(404, "Consulta no encontrada")
    return conv


@app.get("/health")
def health(db: Db) -> dict:
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.post("/api/login")
def login(body: LoginIn, db: Db) -> dict:
    user = authenticate(db, body.username, body.password)
    if not user:
        raise HTTPException(401, "Credenciales incorrectas")
    return {"token": issue_token(user), "user": _user(user)}


@app.get("/api/me")
def me(user: Operator) -> dict:
    return _user(user)


@app.get("/api/users")
def users(db: Db, user: Operator) -> list[dict]:
    return [_user(item) for item in db.scalars(select(User).where(User.active)).all()]


@app.post("/api/users")
def create_user(body: UserIn, db: Db, admin: Admin) -> dict:
    if body.role not in {"admin", "operator"}:
        raise HTTPException(400, "Rol no válido")
    if db.scalar(select(User.id).where(User.username == body.username)):
        raise HTTPException(409, "Usuario existente")
    user = User(username=body.username, password_hash=hasher.hash(body.password), role=body.role)
    db.add(user)
    db.commit()
    return _user(user)


@app.get("/api/summary")
def summary(db: Db, user: Operator) -> dict:
    batch = latest_batch(db)
    return {
        "pending": db.scalar(
            select(func.count()).select_from(Conversation).where(Conversation.status == "pending")
        ),
        "delivery_unknown": db.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.status == "delivery_unknown")
        ),
        "bookings": db.scalar(
            select(func.count()).select_from(BookingTask).where(BookingTask.status == "open")
        ),
        "last_import_at": batch.created_at if batch else None,
        "last_import_rows": batch.row_count if batch else 0,
    }


@app.get("/api/conversations")
def conversations(
    db: Db,
    user: Operator,
    status: str | None = None,
    channel: str | None = None,
    priority: str | None = None,
    kind: str | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> list[dict]:
    query = select(Conversation)
    for column, value in (
        (Conversation.status, status),
        (Conversation.channel, channel),
        (Conversation.priority, priority),
        (Conversation.kind, kind),
    ):
        if value:
            query = query.where(column == value)
    items = db.scalars(query.order_by(desc(Conversation.last_inbound_at)).limit(limit)).all()
    return [_conversation(item, db) for item in items]


@app.get("/api/conversations/{id}")
def conversation(id: int, db: Db, user: Operator) -> dict:
    return _conversation(_get_conv(db, id), db, detail=True)


@app.put("/api/conversations/{id}/draft")
def edit_draft(id: int, body: DraftIn, db: Db, user: Operator) -> dict:
    conv = _get_conv(db, id)
    if conv.kind == "booking" or conv.status not in {"pending", "error"}:
        raise HTTPException(409, "La consulta no admite edición")
    live, _ = _live_analysis(db, conv)
    live.pop("availability_changed", None)
    conv.analysis = live
    draft = db.scalar(select(Draft).where(Draft.conversation_id == id).order_by(desc(Draft.id)))
    if draft and body.expected_draft_id is not None and draft.id != body.expected_draft_id:
        raise HTTPException(409, "Hay un borrador nuevo; revisa la conversación")
    if draft and draft.created_at and aware(draft.created_at) < aware(conv.last_inbound_at):
        raise HTTPException(409, "Ha llegado un mensaje nuevo; espera el borrador actualizado")
    if not draft or draft.status != "pending":
        draft = Draft(conversation_id=id, text=body.text.strip())
        db.add(draft)
    else:
        draft.text = body.text.strip()
    db.add(AuditEvent(actor_user_id=user.id, conversation_id=id, action="draft.edited", detail={}))
    db.commit()
    return _draft(draft)


@app.post("/api/conversations/{id}/approve")
def approve(id: int, db: Db, user: Operator) -> dict:
    conv = _get_conv(db, id)
    if conv.kind == "booking" or conv.status != "pending":
        raise HTTPException(409, "La consulta no admite aprobación")
    if _live_analysis(db, conv)[1]:
        raise HTTPException(409, "La disponibilidad cambió; revisa y guarda el borrador")
    draft = db.scalar(select(Draft).where(Draft.conversation_id == id).order_by(desc(Draft.id)))
    if not draft or draft.status != "pending" or not draft.text.strip():
        raise HTTPException(409, "Falta un borrador pendiente")
    if draft.created_at and aware(draft.created_at) < aware(conv.last_inbound_at):
        raise HTTPException(409, "Ha llegado un mensaje nuevo; espera el borrador actualizado")
    draft.status = "queued"
    draft.approved_by_id = user.id
    draft.approved_at = now()
    conv.status = "queued"
    enqueue(db, key=f"send:{draft.id}", kind="send", payload={"draft_id": draft.id})
    db.add(
        AuditEvent(
            actor_user_id=user.id,
            conversation_id=id,
            action="draft.approved",
            detail={"draft_id": draft.id},
        )
    )
    db.commit()
    return _draft(draft)


@app.post("/api/conversations/{id}/pending")
def leave_pending(id: int, db: Db, user: Operator) -> dict:
    conv = _get_conv(db, id)
    if conv.status not in {"pending", "error"}:
        raise HTTPException(409, "Estado no modificable")
    conv.status = "pending"
    db.add(
        AuditEvent(
            actor_user_id=user.id, conversation_id=id, action="conversation.pending", detail={}
        )
    )
    db.commit()
    return _conversation(conv, db)


@app.post("/api/conversations/{id}/assign")
def assign(id: int, body: AssignmentIn, db: Db, user: Operator) -> dict:
    conv = _get_conv(db, id)
    if body.user_id is not None:
        assignee = db.get(User, body.user_id)
        if not assignee or not assignee.active:
            raise HTTPException(400, "Operador no disponible")
    conv.assigned_user_id = body.user_id
    db.add(
        AuditEvent(
            actor_user_id=user.id,
            conversation_id=id,
            action="conversation.assigned",
            detail={"user_id": body.user_id},
        )
    )
    db.commit()
    return _conversation(conv, db)


@app.post("/api/conversations/{id}/resolve-delivery")
def resolve_delivery(id: int, body: UnknownResolution, db: Db, admin: Admin) -> dict:
    conv = _get_conv(db, id)
    draft = db.scalar(select(Draft).where(Draft.conversation_id == id).order_by(desc(Draft.id)))
    if not draft or draft.status != "delivery_unknown" or body.outcome not in {"sent", "not_sent"}:
        raise HTTPException(409, "Resolución no válida")
    if body.outcome == "sent":
        draft.status = "sent"
        draft.sent_at = now()
        conv.status = "sent"
    else:
        draft.status = "failed"
        conv.status = "pending"
        db.add(Draft(conversation_id=id, text=draft.text, status="pending"))
    db.add(
        AuditEvent(
            actor_user_id=admin.id,
            conversation_id=id,
            action="delivery.resolved",
            detail={"outcome": body.outcome, "note": body.note},
        )
    )
    db.commit()
    return _conversation(conv, db)


@app.post("/api/bookings/{id}/close")
def close_booking(id: int, db: Db, user: Operator) -> dict:
    task = db.get(BookingTask, id)
    if not task:
        raise HTTPException(404, "Prerreserva no encontrada")
    task.status = "closed"
    conv = db.get(Conversation, task.conversation_id)
    conv.status = "closed"
    db.add(
        AuditEvent(
            actor_user_id=user.id, conversation_id=conv.id, action="booking.closed", detail={}
        )
    )
    db.commit()
    return {"status": "closed"}


@app.post("/api/imports")
async def upload_csv(
    db: Db,
    admin: Admin,
    file: Annotated[UploadFile, File()],
    mapping: Annotated[str, Form()] = "{}",
) -> dict:
    try:
        parsed = json.loads(mapping)
        if not isinstance(parsed, dict):
            raise ValueError("mapeo no válido")
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "Mapeo JSON inválido") from exc
    raw = await file.read(10_000_001)
    batch = import_csv(
        db, raw=raw, filename=file.filename or "upload.csv", source="upload", mapping=parsed
    )
    db.add(
        AuditEvent(
            actor_user_id=admin.id,
            action="csv.imported",
            detail={"batch_id": batch.id, "status": batch.status},
        )
    )
    db.commit()
    return {"id": batch.id, "status": batch.status, "rows": batch.row_count, "error": batch.error}


@app.get("/api/imports")
def imports(db: Db, user: Operator) -> list[dict]:
    return [
        {
            "id": b.id,
            "filename": b.filename,
            "source": b.source,
            "status": b.status,
            "rows": b.row_count,
            "error": b.error,
            "created_at": b.created_at,
        }
        for b in db.scalars(select(ImportBatch).order_by(desc(ImportBatch.id)).limit(50)).all()
    ]


@app.get("/api/properties")
def properties(db: Db, user: Operator) -> list[dict]:
    batch = latest_batch(db)
    if not batch:
        return []
    slots = db.scalars(select(AvailabilitySlot).where(AvailabilitySlot.batch_id == batch.id)).all()
    properties: dict[str, dict] = {}
    for slot in slots:
        item = properties.setdefault(
            slot.property_id,
            {
                "id": slot.property_id,
                "name": slot.property_name,
                "capacity": slot.capacity,
                "periods": 0,
            },
        )
        item["periods"] += 1
    return sorted(properties.values(), key=lambda item: item["name"])


@app.get("/api/integrations")
def integrations(db: Db, user: Operator) -> dict:
    settings = get_settings()
    batch = latest_batch(db)
    cursor = db.scalar(select(MailCursor).where(MailCursor.mailbox == settings.imap_mailbox))
    age = (datetime.now(UTC) - aware(batch.created_at)).total_seconds() / 3600 if batch else None
    return {
        "whatsapp": {
            "configured": bool(
                settings.gowa_basic_auth
                and settings.gowa_webhook_secret
                and settings.gowa_device_id
            )
        },
        "imap": {
            "configured": bool(settings.imap_host),
            "last_success_at": cursor.last_success_at if cursor else None,
            "last_error": cursor.last_error if cursor else None,
        },
        "smtp": {"configured": bool(settings.smtp_host and settings.smtp_from)},
        "ollama": {"configured": bool(settings.ollama_url and settings.ollama_model)},
        "csv": {
            "last_import_at": batch.created_at if batch else None,
            "age_hours": age,
            "stale": age is None or age > settings.availability_max_age_hours,
        },
    }


@app.post("/webhooks/gowa")
async def gowa_webhook(request: Request, db: Db) -> dict:
    settings = get_settings()
    raw = await request.body()
    received = request.headers.get("X-Hub-Signature-256", "")
    expected = (
        "sha256=" + hmac.new(settings.gowa_webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
    )
    if not settings.gowa_webhook_secret or not hmac.compare_digest(received, expected):
        raise HTTPException(401, "Firma no válida")
    try:
        event = json.loads(raw)
        if event["device_id"] != settings.gowa_device_id:
            raise ValueError("Dispositivo no válido")
        if event["event"] != "message":
            return {"status": "ignored"}
        payload = event["payload"]
        chat_id = str(payload["chat_id"])
        if chat_id.endswith("@g.us") or payload.get("is_from_me"):
            return {"status": "ignored"}
        provider_id = str(payload["id"])
        body = str(
            payload.get("body")
            or payload.get("caption")
            or payload.get("text")
            or "[Mensaje multimedia para revisar]"
        )
        message, duplicate = ingest(
            db,
            channel="whatsapp",
            external_key=chat_id,
            provider_id=provider_id,
            body=body,
            sender=str(payload.get("from") or chat_id),
            customer_name=payload.get("sender_display_name"),
        )
        db.commit()
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        db.rollback()
        raise HTTPException(400, "Evento GOWA no válido") from exc
    return {"status": "accepted", "duplicate": duplicate, "message_id": message.id}
