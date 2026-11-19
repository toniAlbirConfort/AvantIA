"""In-app notifications: what is happening in AvantIA and what needs someone's attention.

Two ways to produce a notification:

* Conditions (``raise_condition`` / ``clear_condition``): a state that holds until its cause
  goes away (IMAP failing, stale data, an uncertain delivery...). ``scan`` re-evaluates them
  from the database every housekeeping tick, so they resolve themselves; a user can dismiss
  one and it stays quiet until the cause clears.
* Events (``record_event``): something that happened (a job failed). Repeats while the
  notification is open add occurrences instead of new rows.

Every kind must be declared in ``KINDS`` with its default severity and audience. Titles and
bodies are short Spanish text that reference objects by ID, never personal data or secrets.
See .agents/skills/avantia-avisos/SKILL.md before adding a new kind.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, delete, exists, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    BookingTask,
    Conversation,
    ImportBatch,
    MailCursor,
    Notification,
    NotificationRead,
    RegistrationCheck,
    ReservationSyncState,
    User,
    now,
)

logger = logging.getLogger(__name__)

SEVERITIES = ("critical", "action", "info")
AUDIENCES = ("all", "admin")
# Open notifications stop being relevant after this window even if never acted upon.
OVERDUE_WINDOW = timedelta(hours=48)
BOOKING_WINDOW = timedelta(days=7)
IMPORT_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class KindSpec:
    severity: str
    audience: str
    label: str


KINDS: dict[str, KindSpec] = {
    "mail.imap_failed": KindSpec("critical", "admin", "Lectura de correo"),
    "avantio.sync_failed": KindSpec("critical", "admin", "Sincronización de Avantio"),
    "avantio.sync_stale": KindSpec("action", "admin", "Datos de Avantio desactualizados"),
    "availability.csv_stale": KindSpec("action", "admin", "Disponibilidad desactualizada"),
    "job.failed": KindSpec("critical", "admin", "Trabajo en segundo plano"),
    "delivery.unknown": KindSpec("critical", "admin", "Envío incierto"),
    "inbox.overdue": KindSpec("action", "all", "Consultas sin responder"),
    "booking.created": KindSpec("action", "all", "Prerreserva nueva"),
    "registration.unregistered": KindSpec("action", "all", "Consulta sin registrar"),
    "import.invalid": KindSpec("action", "admin", "Importación inválida"),
}

JOB_LABELS = {
    "analyze": "análisis de mensajes",
    "auto_reply": "autorespuesta",
    "send": "envío de respuestas",
    "alert": "aviso al chat interno",
    "tariff_study": "estudio de tarifas",
    "registration_check": "comprobación de registro en Avantio",
}


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _clip(value: str | None, size: int) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    return value if len(value) <= size else value[: size - 1] + "…"


def _active(db: Session, key: str) -> Notification | None:
    return db.scalar(
        select(Notification)
        .where(Notification.dedupe_key == key, Notification.resolved_at.is_(None))
        .order_by(Notification.id.desc())
    )


def _create(
    db: Session, kind: str, key: str, title: str, body: str | None, link: str | None, at: datetime
) -> Notification:
    spec = KINDS[kind]
    item = Notification(
        kind=kind,
        severity=spec.severity,
        audience=spec.audience,
        title=_clip(title, 200),
        body=_clip(body, 500),
        link=link,
        dedupe_key=key,
        status="open",
        occurrences=1,
        created_at=at,
        last_seen_at=at,
    )
    db.add(item)
    db.flush()
    return item


def raise_condition(
    db: Session,
    kind: str,
    key: str,
    title: str,
    body: str | None = None,
    link: str | None = None,
    *,
    once: bool = False,
    at: datetime | None = None,
) -> Notification | None:
    """Make sure an active notification exists for ``key``; refresh its text if it does.

    ``once`` skips the key if it ever had a notification (one-shot objects such as an
    invalid import that has no cause to clear).
    """
    if kind not in KINDS:
        raise ValueError(f"Tipo de aviso no declarado: {kind}")
    at = at or now()
    if once and db.scalar(select(Notification.id).where(Notification.dedupe_key == key)):
        return None
    item = _active(db, key)
    if item is None:
        return _create(db, kind, key, title, body, link, at)
    item.title = _clip(title, 200)
    item.body = _clip(body, 500)
    item.link = link
    item.last_seen_at = at
    return item


def clear_condition(db: Session, key: str, at: datetime | None = None) -> int:
    """Resolve the active notification for ``key``; a dismissed one stops blocking new ones."""
    at = at or now()
    items = db.scalars(
        select(Notification).where(
            Notification.dedupe_key == key, Notification.resolved_at.is_(None)
        )
    ).all()
    for item in items:
        item.resolved_at = at
        if item.status == "open":
            item.status = "resolved"
    return len(items)


def record_event(
    db: Session,
    kind: str,
    key: str,
    title: str,
    body: str | None = None,
    link: str | None = None,
    at: datetime | None = None,
) -> Notification:
    """Add an occurrence to the open notification for ``key`` or open a new one."""
    if kind not in KINDS:
        raise ValueError(f"Tipo de aviso no declarado: {kind}")
    at = at or now()
    item = db.scalar(
        select(Notification)
        .where(Notification.dedupe_key == key, Notification.status == "open")
        .order_by(Notification.id.desc())
    )
    if item is None:
        return _create(db, kind, key, title, body, link, at)
    item.occurrences += 1
    item.title = _clip(title, 200)
    item.body = _clip(body, 500)
    item.last_seen_at = at
    # Unread again for everyone, so a repeated failure is noticed.
    db.execute(delete(NotificationRead).where(NotificationRead.notification_id == item.id))
    return item


def _sync_set(
    db: Session, kind: str, wanted: dict[str, tuple[str, str | None, str | None]], at: datetime
) -> None:
    """Raise one condition per wanted key and clear the active ones of ``kind`` not wanted."""
    for key, (title, body, link) in wanted.items():
        raise_condition(db, kind, key, title, body, link, at=at)
    stale = db.scalars(
        select(Notification.dedupe_key).where(
            Notification.kind == kind, Notification.resolved_at.is_(None)
        )
    ).all()
    for key in set(stale) - set(wanted):
        clear_condition(db, key, at)


# --- Watchers: each one turns current database state into conditions. -------------------


def _watch_imap(db: Session, at: datetime) -> None:
    settings = get_settings()
    key = "mail.imap"
    cursor = (
        db.scalar(select(MailCursor).where(MailCursor.mailbox == settings.imap_mailbox))
        if settings.imap_host
        else None
    )
    if cursor is None:
        clear_condition(db, key, at)
        return
    stale_after = timedelta(minutes=settings.notification_imap_stale_minutes)
    last_success = _aware(cursor.last_success_at) if cursor.last_success_at else None
    if cursor.last_error:
        raise_condition(
            db,
            "mail.imap_failed",
            key,
            "No se puede leer el buzón de correo",
            f"La última lectura falló ({cursor.last_error}). Los correos nuevos no entran en "
            "la bandeja hasta que se recupere la conexión.",
            "/integrations",
            at=at,
        )
    elif last_success is None or at - last_success > stale_after:
        minutes = int((at - last_success).total_seconds() // 60) if last_success else None
        raise_condition(
            db,
            "mail.imap_failed",
            key,
            "El buzón de correo no se lee desde hace "
            + (f"{minutes} min" if minutes is not None else "el arranque"),
            "El worker no completa la lectura del correo. Revisa el servicio worker.",
            "/integrations",
            at=at,
        )
    else:
        clear_condition(db, key, at)


def _watch_avantio_sync(db: Session, at: datetime) -> None:
    state = db.get(ReservationSyncState, "avantio_reservations")
    failed_key, stale_key = "avantio.sync_failed", "avantio.sync_stale"
    if state is None:
        clear_condition(db, failed_key, at)
        clear_condition(db, stale_key, at)
        return
    if state.status == "failed":
        raise_condition(
            db,
            "avantio.sync_failed",
            failed_key,
            "Falló la descarga de reservas de Avantio",
            _clip(state.last_error, 300) or "Sin detalle del error.",
            "/integrations",
            at=at,
        )
    else:
        clear_condition(db, failed_key, at)
    hours = state.interval_hours or get_settings().avantio_sync_interval_hours
    last_success = _aware(state.last_success_at) if state.last_success_at else None
    if last_success and at - last_success > timedelta(hours=2 * hours):
        age = int((at - last_success).total_seconds() // 3600)
        raise_condition(
            db,
            "avantio.sync_stale",
            stale_key,
            f"Las reservas de Avantio llevan {age} h sin actualizarse",
            f"La sincronización debería ejecutarse cada {hours} h. Mientras tanto no se "
            "comprueban las consultas sin registrar.",
            "/integrations",
            at=at,
        )
    else:
        clear_condition(db, stale_key, at)


def _watch_csv(db: Session, at: datetime) -> None:
    from app.availability import latest_batch

    key = "availability.csv_stale"
    batch = latest_batch(db)
    max_age = get_settings().availability_max_age_hours
    if batch is None:
        clear_condition(db, key, at)
        return
    age = int((at - _aware(batch.created_at)).total_seconds() // 3600)
    if age > max_age:
        raise_condition(
            db,
            "availability.csv_stale",
            key,
            f"La disponibilidad tiene {age} h de antigüedad",
            f"El límite es {max_age} h. Los borradores pedirán comprobación manual hasta que "
            "se importe un CSV nuevo.",
            "/imports",
            at=at,
        )
    else:
        clear_condition(db, key, at)


def _watch_delivery(db: Session, at: datetime) -> None:
    ids = db.scalars(select(Conversation.id).where(Conversation.status == "delivery_unknown")).all()
    _sync_set(
        db,
        "delivery.unknown",
        {
            f"delivery.unknown:{id}": (
                f"Envío incierto en la conversación #{id}",
                "No consta si la respuesta llegó. Comprueba el canal y registra si se envió.",
                f"/inbox/{id}",
            )
            for id in ids
        },
        at,
    )


def _watch_overdue(db: Session, at: datetime) -> None:
    minutes = get_settings().overdue_minutes
    count, oldest = db.execute(
        select(func.count(Conversation.id), func.min(Conversation.last_inbound_at)).where(
            Conversation.status == "pending",
            Conversation.last_inbound_at <= at - timedelta(minutes=minutes),
            Conversation.last_inbound_at >= at - OVERDUE_WINDOW,
        )
    ).one()
    wanted = {}
    if count:
        waited = int((at - _aware(oldest)).total_seconds() // 60)
        wait_text = f"{waited // 60} h" if waited >= 120 else f"{waited} min"
        wanted["inbox.overdue"] = (
            f"{count} consulta{'s' if count != 1 else ''} sin responder desde hace más de "
            f"{minutes} min",
            f"La más antigua espera desde hace {wait_text}.",
            "/inbox",
        )
    _sync_set(db, "inbox.overdue", wanted, at)


def _watch_bookings(db: Session, at: datetime) -> None:
    rows = db.execute(
        select(BookingTask.id, BookingTask.conversation_id).where(
            BookingTask.status == "open", BookingTask.created_at >= at - BOOKING_WINDOW
        )
    ).all()
    _sync_set(
        db,
        "booking.created",
        {
            f"booking.created:{id}": (
                f"Prerreserva nueva en la conversación #{conversation_id}",
                "Llegó un correo de un dominio de reservas. Compruébala en Avantio.",
                f"/bookings/{conversation_id}",
            )
            for id, conversation_id in rows
        },
        at,
    )


def _watch_registration(db: Session, at: datetime) -> None:
    rows = db.execute(
        select(RegistrationCheck.id, RegistrationCheck.conversation_id).where(
            RegistrationCheck.status == "alerted"
        )
    ).all()
    _sync_set(
        db,
        "registration.unregistered",
        {
            f"registration.unregistered:{id}": (
                f"Consulta #{conversation_id} sin registrar en Avantio",
                "Pasó el plazo y no hay ninguna reserva que coincida. Regístrala como "
                "petición de información o indica por qué no procede.",
                f"/inbox/{conversation_id}",
            )
            for id, conversation_id in rows
        },
        at,
    )


DATASET_LABELS = {
    "availability": "disponibilidad",
    "properties": "alojamientos",
    "reservations": "reservas",
    "customers": "clientes",
}


def _watch_imports(db: Session, at: datetime) -> None:
    batches = db.scalars(
        select(ImportBatch).where(
            ImportBatch.status == "invalid", ImportBatch.created_at >= at - IMPORT_WINDOW
        )
    ).all()
    for batch in batches:
        raise_condition(
            db,
            "import.invalid",
            f"import.invalid:{batch.id}",
            f"Importación de {DATASET_LABELS.get(batch.dataset, batch.dataset)} rechazada",
            _clip(batch.error, 300) or "El archivo no superó la validación.",
            "/imports",
            once=True,
            at=at,
        )


WATCHERS: tuple[Callable[[Session, datetime], None], ...] = (
    _watch_imap,
    _watch_avantio_sync,
    _watch_csv,
    _watch_delivery,
    _watch_overdue,
    _watch_bookings,
    _watch_registration,
    _watch_imports,
)


def purge_old(db: Session, at: datetime) -> int:
    """Delete closed notifications older than the retention period."""
    cutoff = at - timedelta(days=get_settings().notification_retention_days)
    ids = db.scalars(
        select(Notification.id).where(
            Notification.status != "open",
            func.coalesce(Notification.resolved_at, Notification.last_seen_at) < cutoff,
        )
    ).all()
    if ids:
        db.execute(delete(NotificationRead).where(NotificationRead.notification_id.in_(ids)))
        db.execute(delete(Notification).where(Notification.id.in_(ids)))
    return len(ids)


def scan(db: Session, current_time: datetime | None = None) -> None:
    """Re-evaluate every condition; one failing watcher does not block the others."""
    at = _aware(current_time or now())
    for watcher in WATCHERS:
        try:
            watcher(db, at)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Notification watcher %s failed", watcher.__name__)
    try:
        purge_old(db, at)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Notification purge failed")


def notify_job_failed(db: Session, kind: str, job_id: int, error: str | None) -> None:
    label = JOB_LABELS.get(kind, kind)
    record_event(
        db,
        "job.failed",
        f"job.failed:{kind}",
        f"Falló un trabajo de {label}",
        f"Trabajo #{job_id}: {_clip(error, 300) or 'sin detalle'}",
        "/integrations",
    )


# --- Reading side, per user. ------------------------------------------------------------


def _visible(user: User):
    return Notification.audience.in_(("all", "admin") if user.role == "admin" else ("all",))


def _unread_clause(user: User):
    return ~exists().where(
        NotificationRead.notification_id == Notification.id,
        NotificationRead.user_id == user.id,
    )


def view(item: Notification, read: bool) -> dict:
    return {
        "id": item.id,
        "kind": item.kind,
        "label": KINDS[item.kind].label if item.kind in KINDS else item.kind,
        "severity": item.severity,
        "audience": item.audience,
        "title": item.title,
        "body": item.body,
        "link": item.link,
        "status": item.status,
        "occurrences": item.occurrences,
        "created_at": item.created_at,
        "last_seen_at": item.last_seen_at,
        "resolved_at": item.resolved_at,
        "read": read,
    }


def counts(db: Session, user: User) -> dict:
    rows = db.execute(
        select(Notification.severity, func.count(Notification.id))
        .where(Notification.status == "open", _visible(user), _unread_clause(user))
        .group_by(Notification.severity)
    ).all()
    by_severity = {severity: 0 for severity in SEVERITIES} | dict(rows)
    return {"unread": sum(by_severity.values()), "by_severity": by_severity}


def list_for_user(db: Session, user: User, status: str = "open", limit: int = 100) -> list[dict]:
    read_ids = select(NotificationRead.notification_id).where(NotificationRead.user_id == user.id)
    query = select(Notification).where(_visible(user))
    if status == "open":
        query = query.where(Notification.status == "open")
    elif status != "all":
        query = query.where(Notification.status == status)
    severity_rank = case(
        {severity: rank for rank, severity in enumerate(SEVERITIES)},
        value=Notification.severity,
        else_=len(SEVERITIES),
    )
    query = query.order_by(
        (Notification.status == "open").desc(),
        severity_rank,
        Notification.last_seen_at.desc(),
        Notification.id.desc(),
    ).limit(limit)
    items = db.scalars(query).all()
    read = set(
        db.scalars(read_ids.where(NotificationRead.notification_id.in_([i.id for i in items])))
    )
    return [view(item, item.id in read) for item in items]


def get_visible(db: Session, user: User, notification_id: int) -> Notification | None:
    return db.scalar(select(Notification).where(Notification.id == notification_id, _visible(user)))


def mark_read(db: Session, user: User, ids: list[int] | None = None) -> int:
    """Mark the given notifications (or every open one the user sees) as read."""
    query = select(Notification.id).where(_visible(user), _unread_clause(user))
    if ids is None:
        query = query.where(Notification.status == "open")
    else:
        query = query.where(Notification.id.in_(ids))
    pending = db.scalars(query).all()
    at = now()
    for notification_id in pending:
        db.add(NotificationRead(notification_id=notification_id, user_id=user.id, read_at=at))
    db.commit()
    return len(pending)


def dismiss(db: Session, user: User, item: Notification) -> None:
    if item.status == "open":
        item.status = "dismissed"
        item.dismissed_by_id = user.id
    already = db.get(NotificationRead, (item.id, user.id))
    if already is None:
        db.add(NotificationRead(notification_id=item.id, user_id=user.id, read_at=now()))
    db.commit()


__all__ = [
    "AUDIENCES",
    "KINDS",
    "SEVERITIES",
    "clear_condition",
    "counts",
    "dismiss",
    "get_visible",
    "list_for_user",
    "mark_read",
    "notify_job_failed",
    "raise_condition",
    "record_event",
    "scan",
    "view",
]
