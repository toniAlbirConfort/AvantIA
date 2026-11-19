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
from zoneinfo import ZoneInfo

from sqlalchemy import case, delete, exists, func, select
from sqlalchemy.orm import Session

from app.classification import classification_review_clause
from app.config import get_settings
from app.models import (
    AuditEvent,
    BookingTask,
    Conversation,
    ImportBatch,
    MailCursor,
    Message,
    Notification,
    NotificationMute,
    NotificationRead,
    RegistrationCheck,
    ReservationSyncState,
    TariffStudy,
    User,
    now,
)

logger = logging.getLogger(__name__)

SEVERITIES = ("critical", "action", "info")
AUDIENCES = ("all", "admin", "user")
# Open notifications stop being relevant after this window even if never acted upon.
OVERDUE_WINDOW = timedelta(hours=48)
BOOKING_WINDOW = timedelta(days=7)
IMPORT_WINDOW = timedelta(hours=24)
RECENT_WINDOW = timedelta(days=7)
LLM_WINDOW = timedelta(minutes=30)
LLM_FAILURES = 3


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
    "booking.created": KindSpec("action", "all", "Prerreservas"),
    "registration.unregistered": KindSpec("action", "all", "Consulta sin registrar"),
    "import.invalid": KindSpec("action", "admin", "Importación inválida"),
    "whatsapp.unavailable": KindSpec("critical", "admin", "WhatsApp (GOWA)"),
    "llm.failing": KindSpec("action", "admin", "Análisis con IA"),
    "conversation.assigned": KindSpec("info", "user", "Conversación asignada"),
    "auto_reply.failed": KindSpec("action", "admin", "Autorespuesta"),
    "classification.review": KindSpec("info", "all", "Clasificación por revisar"),
    "tariff.study_finished": KindSpec("info", "admin", "Estudio de tarifas"),
    "tariff.study_failed": KindSpec("critical", "admin", "Estudio de tarifas fallido"),
    "import.warnings": KindSpec("info", "admin", "Advertencias de importación"),
    "job.recovered": KindSpec("action", "admin", "Trabajo interrumpido"),
    "maintenance.done": KindSpec("info", "admin", "Mantenimiento"),
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
    db: Session,
    kind: str,
    key: str,
    title: str,
    body: str | None,
    link: str | None,
    at: datetime,
    user_id: int | None = None,
) -> Notification:
    spec = KINDS[kind]
    if (spec.audience == "user") != (user_id is not None):
        raise ValueError(f"El aviso {kind} lleva destinatario solo si es personal")
    item = Notification(
        kind=kind,
        severity=spec.severity,
        audience=spec.audience,
        user_id=user_id,
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
    user_id: int | None = None,
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
        return _create(db, kind, key, title, body, link, at, user_id)
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
    item.link = link
    item.last_seen_at = at
    # Unread again for everyone, so a repeated failure is noticed.
    db.execute(delete(NotificationRead).where(NotificationRead.notification_id == item.id))
    return item


def _sync_set(db: Session, kind: str, wanted: dict[str, tuple], at: datetime) -> None:
    """Raise one condition per wanted key and clear the active ones of ``kind`` not wanted.

    Values are ``(title, body, link)`` or, for personal kinds, ``(title, body, link, user_id)``.
    """
    for key, (title, body, link, *rest) in wanted.items():
        user_id = rest[0] if rest else None
        raise_condition(db, kind, key, title, body, link, at=at, user_id=user_id)
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


def _local_window_start(at: datetime, window: timedelta) -> datetime:
    """Local midnight of the day the window starts: what the inbox "Desde" filter uses."""
    zone = ZoneInfo(get_settings().auto_reply_timezone)
    day = (at - window).astimezone(zone).date()
    return datetime(day.year, day.month, day.day, tzinfo=zone).astimezone(UTC)


def _inbox_link(window_start: datetime, **filters: str) -> str:
    zone = ZoneInfo(get_settings().auto_reply_timezone)
    params = {**filters, "from": window_start.astimezone(zone).date().isoformat()}
    return "/inbox?" + "&".join(f"{key}={value}" for key, value in params.items())


def _since_text(window_start: datetime) -> str:
    zone = ZoneInfo(get_settings().auto_reply_timezone)
    return window_start.astimezone(zone).strftime("%d/%m")


def _conversations(count: int) -> str:
    return f"{count} {'conversación' if count == 1 else 'conversaciones'}"


def _id_list(ids: list[int], total: int) -> str:
    shown = ", ".join(f"#{id}" for id in ids)
    return shown + (f" y {total - len(ids)} más" if total > len(ids) else "")


def _watch_overdue(db: Session, at: datetime) -> None:
    """Pending conversations nobody answered: counted exactly as the inbox filter shows them."""
    minutes = get_settings().overdue_minutes
    window_start = _local_window_start(at, OVERDUE_WINDOW)
    rows = db.execute(
        select(Conversation.id, Conversation.last_inbound_at)
        .where(
            Conversation.status == "pending",
            Conversation.last_inbound_at <= at - timedelta(minutes=minutes),
            Conversation.last_inbound_at >= window_start,
        )
        .order_by(Conversation.last_inbound_at, Conversation.id)
    ).all()
    wanted = {}
    if rows:
        count = len(rows)
        waited = int((at - _aware(rows[0][1])).total_seconds() // 60)
        wait_text = f"{waited // 60} h" if waited >= 120 else f"{waited} min"
        wanted["inbox.overdue"] = (
            f"{_conversations(count)} pendiente{'s' if count != 1 else ''} de responder",
            f"Llevan más de {minutes} min en estado Pendiente; la más antigua, {wait_text}. "
            "Respóndelas o, si no necesitan respuesta, ciérralas desde la bandeja. "
            f"Recibidas desde el {_since_text(window_start)}; las que más esperan: "
            f"{_id_list([row[0] for row in rows[:5]], count)}.",
            _inbox_link(window_start, status="pending"),
        )
    _sync_set(db, "inbox.overdue", wanted, at)


def _watch_bookings(db: Session, at: datetime) -> None:
    """One notification with the count of open prerreservas from the last days.

    The key carries the newest prerreserva of the window (open or not), so a new one reopens
    the notice even after a dismissal, while closing prerreservas only updates the count.
    """
    since = at - BOOKING_WINDOW
    count = db.scalar(
        select(func.count(BookingTask.id)).where(
            BookingTask.status == "open", BookingTask.created_at >= since
        )
    )
    wanted = {}
    if count:
        newest = db.scalar(select(func.max(BookingTask.id)).where(BookingTask.created_at >= since))
        wanted[f"booking.open:{newest}"] = (
            f"{count} prerreserva{'s' if count != 1 else ''} pendiente"
            f"{'s' if count != 1 else ''} de revisar",
            "Llegaron desde dominios de reservas en los últimos 7 días. Compruébalas en "
            "Avantio y ciérralas en Prerreservas.",
            "/bookings",
        )
    _sync_set(db, "booking.created", wanted, at)


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


def _watch_llm(db: Session, at: datetime) -> None:
    """Several classification failures and no success since: the LLM is down."""
    failures, last_failure = db.execute(
        select(func.count(AuditEvent.id), func.max(AuditEvent.created_at)).where(
            AuditEvent.action == "message.classification_failed",
            AuditEvent.created_at >= at - LLM_WINDOW,
        )
    ).one()
    last_success = db.scalar(
        select(func.max(AuditEvent.created_at)).where(AuditEvent.action == "message.classified")
    )
    failing = failures >= LLM_FAILURES and (
        last_success is None or _aware(last_success) < _aware(last_failure)
    )
    wanted = {}
    if failing:
        wanted["llm.failing"] = (
            "El análisis con IA está fallando",
            f"{failures} mensajes no se pudieron clasificar en los últimos 30 min. Revisa que "
            "Ollama esté disponible; mientras tanto los borradores piden comprobación manual.",
            "/integrations",
        )
    _sync_set(db, "llm.failing", wanted, at)


def _watch_assignments(db: Session, at: datetime) -> None:
    """Tell each person about pending conversations a colleague assigned to them."""
    rows = db.execute(
        select(Conversation.id, Conversation.assigned_user_id).where(
            Conversation.assigned_user_id.is_not(None),
            Conversation.status == "pending",
            Conversation.last_inbound_at >= at - RECENT_WINDOW,
        )
    ).all()
    wanted = {}
    for conversation_id, user_id in rows:
        event = db.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.conversation_id == conversation_id,
                AuditEvent.action == "conversation.assigned",
            )
            .order_by(AuditEvent.id.desc())
        )
        actor_id = event.actor_user_id if event else None
        if actor_id == user_id:
            continue  # Assigned to themselves: nothing to tell.
        actor = db.get(User, actor_id) if actor_id else None
        wanted[f"conversation.assigned:{conversation_id}:{user_id}"] = (
            f"Te han asignado la conversación #{conversation_id}",
            f"Asignada por {actor.username}." if actor else "Está pendiente de respuesta.",
            f"/inbox/{conversation_id}",
            user_id,
        )
    _sync_set(db, "conversation.assigned", wanted, at)


def _watch_classification_review(db: Session, at: datetime) -> None:
    """Conversations whose automatic classification needs a human, as the inbox filter shows."""
    window_start = _local_window_start(at, RECENT_WINDOW)
    ids = db.scalars(
        select(Conversation.id)
        .where(
            Conversation.classification_open.is_(True),
            Conversation.id.in_(
                select(Message.conversation_id).where(classification_review_clause()).distinct()
            ),
            Conversation.last_inbound_at >= window_start,
        )
        .order_by(Conversation.last_inbound_at.desc(), Conversation.id.desc())
    ).all()
    wanted = {}
    if ids:
        count = len(ids)
        wanted["classification.review"] = (
            f"{_conversations(count)} con la clasificación por revisar",
            "La IA no está segura de si piden alojamiento (demanda), son de un huésped con "
            "reserva o no son demanda. Abre cada una y confirma o corrige la clasificación "
            "en sus mensajes. "
            f"Recibidas desde el {_since_text(window_start)}: {_id_list(ids[:5], count)}.",
            _inbox_link(window_start, classification="review"),
        )
    _sync_set(db, "classification.review", wanted, at)


STUDY_TEXT = {
    "completed": ("tariff.study_finished", "Estudio de tarifas «{name}» completado"),
    "partial": (
        "tariff.study_finished",
        "Estudio de tarifas «{name}» terminado con datos incompletos",
    ),
    "failed": ("tariff.study_failed", "Falló el estudio de tarifas «{name}»"),
}


def _watch_tariff_studies(db: Session, at: datetime) -> None:
    finished_at = func.coalesce(
        TariffStudy.completed_at, TariffStudy.started_at, TariffStudy.created_at
    )
    studies = db.scalars(
        select(TariffStudy).where(
            TariffStudy.status.in_(tuple(STUDY_TEXT)), finished_at >= at - IMPORT_WINDOW
        )
    ).all()
    for study in studies:
        kind, title = STUDY_TEXT[study.status]
        if study.status == "failed":
            body = _clip(study.error, 300) or "Sin detalle del error."
        else:
            body = "Ya puedes consultar el resultado y su informe."
        raise_condition(
            db,
            kind,
            f"tariff.study:{study.id}",
            title.format(name=study.name),
            body,
            "/tariff-lab",
            once=True,
            at=at,
        )


def _watch_import_warnings(db: Session, at: datetime) -> None:
    """One notice per dataset while its latest valid import carries warnings."""
    for dataset, label in DATASET_LABELS.items():
        batch = db.scalar(
            select(ImportBatch)
            .where(ImportBatch.dataset == dataset, ImportBatch.status == "valid")
            .order_by(ImportBatch.created_at.desc(), ImportBatch.id.desc())
        )
        key = f"import.warnings:{dataset}"
        count = len(batch.warnings or []) if batch else 0
        if count:
            raise_condition(
                db,
                "import.warnings",
                key,
                f"La última importación de {label} trae {count} advertencia"
                f"{'s' if count != 1 else ''}",
                "Son datos que conviene corregir en el origen (por ejemplo, en Avantio). "
                "El detalle está en Importaciones.",
                "/imports",
                at=at,
            )
        else:
            clear_condition(db, key, at)


MAINTENANCE_TEXT = {
    "whatsapp.session.purged": "Se borraron los datos de WhatsApp",
    "maintenance.payment_data_masked": "Se enmascararon datos de pago guardados",
}


def _watch_maintenance(db: Session, at: datetime) -> None:
    events = db.scalars(
        select(AuditEvent).where(
            AuditEvent.action.in_(tuple(MAINTENANCE_TEXT)),
            AuditEvent.created_at >= at - IMPORT_WINDOW,
        )
    ).all()
    for event in events:
        actor = db.get(User, event.actor_user_id) if event.actor_user_id else None
        detail = event.detail or {}
        if event.action == "maintenance.payment_data_masked":
            body = (
                f"{detail.get('messages', 0)} mensajes y {detail.get('conversations', 0)} "
                "conversaciones."
            )
        else:
            body = "Sesión de WhatsApp y datos derivados eliminados de forma irreversible."
        if actor:
            body += f" Lo hizo {actor.username}."
        raise_condition(
            db,
            "maintenance.done",
            f"maintenance:{event.id}",
            MAINTENANCE_TEXT[event.action],
            body,
            "/integrations",
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
    _watch_llm,
    _watch_assignments,
    _watch_classification_review,
    _watch_tariff_studies,
    _watch_import_warnings,
    _watch_maintenance,
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


async def watch_gowa(db: Session) -> None:
    """Check the WhatsApp device through GOWA (a network call, so it runs less often)."""
    from app.gowa import GowaNotConfigured, get_gowa_status, gowa_is_configured

    key = "whatsapp"
    at = now()
    if not gowa_is_configured():
        clear_condition(db, key, at)
        db.commit()
        return
    try:
        status = await get_gowa_status()
    except GowaNotConfigured:
        clear_condition(db, key, at)
        db.commit()
        return
    except Exception as exc:  # Any failure means the service cannot be reached.
        raise_condition(
            db,
            "whatsapp.unavailable",
            key,
            "No se puede contactar con WhatsApp (GOWA)",
            f"El servicio no responde ({type(exc).__name__}). Los mensajes de WhatsApp no "
            "entran mientras tanto.",
            "/integrations",
            at=at,
        )
        db.commit()
        return
    if not status.get("registered") or not status.get("is_logged_in"):
        raise_condition(
            db,
            "whatsapp.unavailable",
            key,
            "WhatsApp no tiene la sesión iniciada",
            "Hay que vincular de nuevo el dispositivo desde Integraciones.",
            "/integrations",
            at=at,
        )
    elif not status.get("is_connected"):
        raise_condition(
            db,
            "whatsapp.unavailable",
            key,
            "WhatsApp está desconectado",
            "La sesión existe pero no hay conexión. Prueba a reconectar desde Integraciones.",
            "/integrations",
            at=at,
        )
    else:
        clear_condition(db, key, at)
    db.commit()


def notify_auto_reply_failed(
    db: Session, conversation_id: int, error_type: str, *, copy: bool = False
) -> None:
    if copy:
        title = "No se guardó la copia de una autorespuesta en Enviados"
        body = (
            f"Conversación #{conversation_id}: la respuesta salió, pero falló la copia "
            f"({error_type})."
        )
    else:
        title = "Falló el envío de una autorespuesta"
        body = f"Conversación #{conversation_id}: {error_type}. El cliente no recibió el acuse."
    record_event(
        db,
        "auto_reply.failed",
        "auto_reply.copy_failed" if copy else "auto_reply.failed",
        title,
        body,
        f"/inbox/{conversation_id}",
    )


def notify_job_recovered(db: Session, kind: str, job_id: int) -> None:
    label = JOB_LABELS.get(kind, kind)
    follow_up = (
        "Comprueba el envío: puede haber salido o no."
        if kind == "send"
        else "Se ha vuelto a poner en cola."
    )
    record_event(
        db,
        "job.recovered",
        f"job.recovered:{kind}",
        f"Se interrumpió un trabajo de {label}",
        f"Trabajo #{job_id}: el worker se detuvo a mitad. {follow_up}",
        "/integrations",
    )


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
    shared = Notification.audience.in_(("all", "admin") if user.role == "admin" else ("all",))
    return shared | ((Notification.audience == "user") & (Notification.user_id == user.id))


def can_mute(kind: str) -> bool:
    return kind in KINDS and KINDS[kind].severity != "critical"


def muted_kinds(db: Session, user: User) -> set[str]:
    return set(db.scalars(select(NotificationMute.kind).where(NotificationMute.user_id == user.id)))


def _counted(user: User):
    """Unread notifications that count: kinds the user did not mute, and critical ones always."""
    muted = select(NotificationMute.kind).where(NotificationMute.user_id == user.id)
    return (Notification.severity == "critical") | Notification.kind.not_in(muted)


def kinds_for(user: User) -> list[str]:
    """Kinds this user can ever see, for the preferences screen."""
    allowed = {"all", "user", "admin"} if user.role == "admin" else {"all", "user"}
    return [kind for kind, spec in KINDS.items() if spec.audience in allowed]


def preferences(db: Session, user: User) -> list[dict]:
    muted = muted_kinds(db, user)
    return [
        {
            "kind": kind,
            "label": KINDS[kind].label,
            "severity": KINDS[kind].severity,
            "audience": KINDS[kind].audience,
            "muted": kind in muted and can_mute(kind),
            "can_mute": can_mute(kind),
        }
        for kind in kinds_for(user)
    ]


def set_muted(db: Session, user: User, kinds: list[str]) -> None:
    wanted = set(kinds)
    allowed = set(kinds_for(user))
    invalid = sorted(kind for kind in wanted if kind not in allowed or not can_mute(kind))
    if invalid:
        raise ValueError("No se pueden silenciar: " + ", ".join(invalid))
    current = muted_kinds(db, user)
    for kind in current - wanted:
        db.execute(
            delete(NotificationMute).where(
                NotificationMute.user_id == user.id, NotificationMute.kind == kind
            )
        )
    for kind in wanted - current:
        db.add(NotificationMute(user_id=user.id, kind=kind, created_at=now()))
    db.commit()


def _unread_clause(user: User):
    return ~exists().where(
        NotificationRead.notification_id == Notification.id,
        NotificationRead.user_id == user.id,
    )


def view(item: Notification, read: bool, muted: bool = False) -> dict:
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
        "muted": muted,
    }


def counts(db: Session, user: User) -> dict:
    rows = db.execute(
        select(Notification.severity, func.count(Notification.id))
        .where(
            Notification.status == "open",
            _visible(user),
            _unread_clause(user),
            _counted(user),
        )
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
    muted = {kind for kind in muted_kinds(db, user) if can_mute(kind)}
    return [view(item, item.id in read, item.kind in muted) for item in items]


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
    "can_mute",
    "clear_condition",
    "counts",
    "dismiss",
    "get_visible",
    "list_for_user",
    "mark_read",
    "notify_auto_reply_failed",
    "notify_job_failed",
    "notify_job_recovered",
    "preferences",
    "raise_condition",
    "record_event",
    "scan",
    "set_muted",
    "view",
    "watch_gowa",
]
