from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
from datetime import UTC, date, datetime
from email.utils import parseaddr
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, or_, select, text
from sqlalchemy.orm import Session

from app.auth import admin_user, authenticate, current_user, hasher, issue_token
from app.availability import aware, import_csv, latest_batch
from app.avantio import availability_provider
from app.config import READ_ONLY_MODE, get_settings
from app.db import get_db
from app.ingest import enqueue, ingest
from app.integrations import ollama_translate_email
from app.models import (
    AuditEvent,
    BookingTask,
    Conversation,
    Draft,
    ImportBatch,
    MailCursor,
    Message,
    ReservationSyncState,
    User,
    now,
)
from app.planning import (
    DATASETS,
    commit_import,
    latest_source_batch,
    planning_view,
    preview_import,
    properties_view,
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
                "html_body": item.html_body,
                "sender": item.sender,
                "recipient": item.recipient,
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


def _sender_identity(value: str | None, customer_name: str | None = None) -> tuple[str, str, str]:
    raw = (value or "").strip()
    name, address = parseaddr(raw)
    address = address.strip()
    key = address.casefold() or raw.casefold() or "unknown"
    display_name = (
        name.strip()
        or (customer_name or "").strip()
        or address
        or raw
        or "Remitente desconocido"
    )
    return key, display_name, address or raw


def _inbox_thread_items(conversations: list[Conversation], db: Session) -> list[dict]:
    if not conversations:
        return []
    ids = [item.id for item in conversations]
    counts = dict(
        db.execute(
            select(Message.conversation_id, func.count(Message.id))
            .where(Message.conversation_id.in_(ids))
            .group_by(Message.conversation_id)
        ).all()
    )
    latest_ids = (
        select(func.max(Message.id).label("id"))
        .where(Message.conversation_id.in_(ids))
        .group_by(Message.conversation_id)
        .subquery()
    )
    latest_messages = {
        row.conversation_id: row
        for row in db.execute(
            select(Message.conversation_id, Message.created_at, Message.body).join(
                latest_ids, Message.id == latest_ids.c.id
            )
        ).all()
    }
    result = []
    for item in conversations:
        latest = latest_messages.get(item.id)
        preview = re.sub(r"\s+", " ", latest.body or "").strip() if latest else ""
        result.append(
            {
                "type": "thread",
                "id": item.id,
                "channel": item.channel,
                "kind": item.kind,
                "customer_name": item.customer_name,
                "customer_address": item.customer_address,
                "subject": item.subject,
                "status": item.status,
                "priority": item.priority,
                "assigned_user_id": item.assigned_user_id,
                "last_inbound_at": item.last_inbound_at,
                "message_count": counts.get(item.id, 0),
                "last_message_at": latest.created_at if latest else None,
                "preview": preview[:240],
            }
        )
    return result


@app.get("/api/inbox")
def inbox(
    db: Db,
    user: Operator,
    group_by: str = Query("thread", pattern="^(thread|sender)$"),
    from_at: datetime | None = Query(default=None, alias="from"),
    to_at: datetime | None = Query(default=None, alias="to"),
    q: str | None = Query(default=None, max_length=200),
    status: str | None = None,
    channel: str | None = None,
    priority: str | None = None,
    kind: str | None = None,
    sender: str | None = Query(default=None, max_length=255),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    if (from_at and from_at.tzinfo is None) or (to_at and to_at.tzinfo is None):
        raise HTTPException(422, "Las fechas deben incluir zona horaria")
    if from_at:
        from_at = from_at.astimezone(UTC)
    if to_at:
        to_at = to_at.astimezone(UTC)
    if from_at and to_at and from_at >= to_at:
        raise HTTPException(422, "La fecha inicial debe ser anterior a la fecha final")

    query = select(Conversation)
    for column, value in (
        (Conversation.status, status),
        (Conversation.channel, channel),
        (Conversation.priority, priority),
        (Conversation.kind, kind),
    ):
        if value:
            query = query.where(column == value)
    if from_at:
        query = query.where(Conversation.last_inbound_at >= from_at)
    if to_at:
        query = query.where(Conversation.last_inbound_at < to_at)
    search = (q or "").strip()
    if search:
        pattern = f"%{search}%"
        latest_body = (
            select(Message.body)
            .where(Message.conversation_id == Conversation.id)
            .order_by(desc(Message.id))
            .limit(1)
            .scalar_subquery()
        )
        query = query.where(
            or_(
                Conversation.customer_name.ilike(pattern),
                Conversation.customer_address.ilike(pattern),
                Conversation.subject.ilike(pattern),
                latest_body.ilike(pattern),
            )
        )

    if group_by == "thread":
        if sender:
            all_sender_conversations = db.scalars(
                query.where(Conversation.channel == "email").order_by(
                    desc(Conversation.last_inbound_at), desc(Conversation.id)
                )
            ).all()
            sender_key = _sender_identity(sender)[0]
            conversations_page = [
                item
                for item in all_sender_conversations
                if _sender_identity(item.customer_address, item.customer_name)[0] == sender_key
            ]
            total = len(conversations_page)
            conversations_page = conversations_page[offset : offset + limit]
        else:
            total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
            conversations_page = db.scalars(
                query.order_by(desc(Conversation.last_inbound_at), desc(Conversation.id))
                .offset(offset)
                .limit(limit)
            ).all()
        items = _inbox_thread_items(conversations_page, db)
    else:
        all_conversations = db.scalars(
            query.order_by(desc(Conversation.last_inbound_at), desc(Conversation.id))
        ).all()
        sender_groups: dict[str, dict] = {}
        whatsapp_conversations: list[Conversation] = []
        for item in all_conversations:
            if item.channel != "email":
                whatsapp_conversations.append(item)
                continue
            sender_key, display_name, address = _sender_identity(
                item.customer_address, item.customer_name
            )
            group = sender_groups.get(sender_key)
            if group is None:
                sender_groups[sender_key] = {
                    "type": "sender",
                    "sender": sender_key,
                    "display_name": display_name,
                    "address": address,
                    "channel": "email",
                    "conversation_count": 1,
                    "last_inbound_at": item.last_inbound_at,
                }
            else:
                group["conversation_count"] += 1
        items = list(sender_groups.values()) + _inbox_thread_items(whatsapp_conversations, db)
        items.sort(key=lambda item: (item["last_inbound_at"], item.get("id", 0)), reverse=True)
        total = len(items)
        items = items[offset : offset + limit]
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@app.get("/api/conversations/{id}")
def conversation(id: int, db: Db, user: Operator) -> dict:
    return _conversation(_get_conv(db, id), db, detail=True)


@app.post("/api/conversations/{conversation_id}/messages/{message_id}/translate")
async def translate_email(
    conversation_id: int, message_id: int, db: Db, user: Operator
) -> dict:
    conv = _get_conv(db, conversation_id)
    if conv.channel != "email":
        raise HTTPException(409, "Solo se pueden traducir emails")
    message = db.get(Message, message_id)
    if not message or message.conversation_id != conv.id:
        raise HTTPException(404, "Email no encontrado")
    if message.direction != "inbound":
        raise HTTPException(409, "Solo se pueden traducir emails recibidos")
    body = message.body or ""
    if not body.strip():
        raise HTTPException(422, "El email no tiene cuerpo para traducir")
    try:
        translated = await ollama_translate_email(body)
    except RuntimeError as exc:
        raise HTTPException(503, "Ollama no está disponible para traducir el email") from exc
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(502, "No se pudo traducir el email; inténtalo de nuevo") from exc
    return {
        "message_id": message.id,
        "language": "es",
        "body": translated["body"],
    }


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
    if READ_ONLY_MODE:
        raise HTTPException(423, "Modo de solo lectura: no se pueden aprobar ni enviar respuestas")
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
        db, raw=raw, filename=file.filename or "upload.csv", source="upload",
        mapping=parsed, actor_user_id=admin.id,
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
def imports(db: Db, user: Operator, limit: int = Query(500, ge=1, le=2000)) -> list[dict]:
    return [
        {
            "id": b.id,
            "dataset": b.dataset,
            "filename": b.filename,
            "source": b.source,
            "status": b.status,
            "rows": b.row_count,
            "error": b.error,
            "created_at": b.created_at,
            "actor_user_id": b.actor_user_id,
            "parser_version": b.parser_version,
            "warning_count": len(b.warnings or []),
            "change_count": len(b.changes or []),
        }
        for b in db.scalars(select(ImportBatch).order_by(desc(ImportBatch.id)).limit(limit)).all()
    ]


@app.get("/api/imports/{batch_id}")
def import_detail(batch_id: int, db: Db, user: Operator) -> dict:
    batch = db.get(ImportBatch, batch_id)
    if not batch:
        raise HTTPException(404, "Importación no encontrada")
    return {
        "id": batch.id, "dataset": batch.dataset, "filename": batch.filename,
        "source": batch.source, "status": batch.status, "rows": batch.row_count,
        "error": batch.error, "created_at": batch.created_at,
        "checksum": batch.checksum, "actor_user_id": batch.actor_user_id,
        "parser_version": batch.parser_version, "warnings": batch.warnings or [],
        "changes": batch.changes or [],
        "warning_count": len(batch.warnings or []), "change_count": len(batch.changes or []),
    }


@app.post("/api/source-imports/{dataset}/preview")
async def preview_source_import(
    dataset: str, db: Db, admin: Admin, file: Annotated[UploadFile, File()]
) -> dict:
    if dataset not in DATASETS:
        raise HTTPException(404, "Tipo de datos no válido")
    raw = await file.read(10_000_001)
    try:
        return preview_import(db, dataset, raw)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/source-imports/{dataset}")
async def upload_source_import(
    dataset: str, db: Db, admin: Admin,
    file: Annotated[UploadFile, File()], expected_checksum: Annotated[str, Form()],
) -> dict:
    if dataset not in DATASETS:
        raise HTTPException(404, "Tipo de datos no válido")
    raw = await file.read(10_000_001)
    current_batch = latest_source_batch(db, dataset)
    existing = (
        current_batch.id
        if current_batch and current_batch.checksum == expected_checksum
        else None
    )
    try:
        batch = commit_import(
            db, dataset, raw, file.filename or "upload.csv", admin.id, expected_checksum
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.add(
        AuditEvent(
            actor_user_id=admin.id,
            action="source.import_reused" if existing == batch.id else "source.imported",
            detail={"batch_id": batch.id, "dataset": dataset, "status": batch.status},
        )
    )
    db.commit()
    return {"id": batch.id, "status": batch.status, "rows": batch.row_count, "error": batch.error,
            "warnings": batch.warnings or [], "changes": batch.changes or []}


@app.get("/api/properties")
def properties(db: Db, user: Operator, batch_id: int | None = None) -> list[dict]:
    try:
        return properties_view(db, batch_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/planning")
def planning(
    db: Db, user: Operator, start: date, end: date,
    property_batch_id: int | None = None, reservation_batch_id: int | None = None,
) -> dict:
    try:
        return planning_view(db, start, end, property_batch_id, reservation_batch_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/integrations")
def integrations(db: Db, user: Operator) -> dict:
    settings = get_settings()
    batch = latest_batch(db)
    cursor = db.scalar(select(MailCursor).where(MailCursor.mailbox == settings.imap_mailbox))
    reservation_sync = db.get(ReservationSyncState, "avantio_reservations")
    age = (datetime.now(UTC) - aware(batch.created_at)).total_seconds() / 3600 if batch else None
    return {
        "mode": {"read_only": READ_ONLY_MODE, "outbound_enabled": not READ_ONLY_MODE},
        "whatsapp": {
            "sending_enabled": not READ_ONLY_MODE,
            "configured": bool(
                settings.gowa_basic_auth
                and settings.gowa_webhook_secret
                and settings.gowa_device_id
            )
        },
        "imap": {
            "configured": bool(
                settings.imap_host and settings.imap_user and settings.imap_password
            ),
            "connected": bool(cursor and cursor.last_success_at),
            "mailbox": settings.imap_mailbox,
            "read_only": True,
            "last_success_at": cursor.last_success_at if cursor else None,
            "last_error": cursor.last_error if cursor else None,
        },
        "smtp": {
            "configured": bool(settings.smtp_host and settings.smtp_from),
            "sending_enabled": not READ_ONLY_MODE,
        },
        "ollama": {"configured": bool(settings.ollama_url and settings.ollama_model)},
        "csv": {
            "last_import_at": batch.created_at if batch else None,
            "age_hours": age,
            "stale": age is None or age > settings.availability_max_age_hours,
        },
        "avantio": {
            "configured": bool(
                settings.avantio_export_url.strip()
                and settings.avantio_username.strip()
                and settings.avantio_password
            ),
            "status": reservation_sync.status if reservation_sync else "idle",
            "interval_hours": settings.avantio_sync_interval_hours,
            "last_attempt_at": reservation_sync.last_attempt_at if reservation_sync else None,
            "last_success_at": reservation_sync.last_success_at if reservation_sync else None,
            "last_batch_id": reservation_sync.last_batch_id if reservation_sync else None,
            "row_count": reservation_sync.row_count if reservation_sync else 0,
            "last_error": reservation_sync.last_error if reservation_sync else None,
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


@app.post("/webhooks/github/deploy")
async def github_deploy_webhook(request: Request) -> dict:
    settings = get_settings()
    if not settings.github_deploy_webhook_secret:
        raise HTTPException(503, "Webhook de despliegue no configurado")

    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 5 * 1024 * 1024:
            raise HTTPException(413, "Evento de GitHub demasiado grande")
    body = bytes(raw)

    received = request.headers.get("X-Hub-Signature-256", "")
    expected = "sha256=" + hmac.new(
        settings.github_deploy_webhook_secret.encode(), body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(received, expected):
        raise HTTPException(401, "Firma de GitHub no válida")

    try:
        event = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "Evento de GitHub no válido") from exc
    if not isinstance(event, dict):
        raise HTTPException(400, "Evento de GitHub no válido")

    if request.headers.get("X-GitHub-Event") != "pull_request":
        return {"status": "ignored"}

    repository = event.get("repository") or {}
    pull_request = event.get("pull_request") or {}
    if not isinstance(repository, dict) or not isinstance(pull_request, dict):
        raise HTTPException(400, "Evento de GitHub no válido")
    base = pull_request.get("base") or {}
    if not isinstance(base, dict):
        raise HTTPException(400, "Evento de GitHub no válido")
    head = pull_request.get("head") or {}
    if not isinstance(head, dict):
        raise HTTPException(400, "Evento de GitHub no válido")
    if str(repository.get("full_name", "")).lower() != "tonialbirconfort/avantia":
        return {"status": "ignored"}
    if (
        event.get("action") != "closed"
        or pull_request.get("merged") is not True
        or base.get("ref") != "main"
    ):
        return {"status": "ignored"}

    revision = pull_request.get("merge_commit_sha") or head.get("sha")
    if not isinstance(revision, str) or not revision:
        raise HTTPException(400, "El evento de merge no contiene una revisión")

    signal_path = Path(settings.deploy_signal_path)
    temporary_path = signal_path.with_name(f".{signal_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        signal_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(
            json.dumps(
                {
                    "delivery": request.headers.get("X-GitHub-Delivery", ""),
                    "revision": revision,
                    "pull_request": pull_request.get("number"),
                }
            ),
            encoding="utf-8",
        )
        os.replace(temporary_path, signal_path)
    except OSError as exc:
        raise HTTPException(503, "No se pudo solicitar el despliegue") from exc
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass

    return {"status": "accepted", "revision": revision}
