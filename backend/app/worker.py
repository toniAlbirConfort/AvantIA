from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.auto_reply import run as run_auto_reply
from app.availability import env_csv_mapping, import_csv
from app.config import get_settings
from app.db import SessionLocal
from app.delivery import send_approved
from app.holiday_sync import sync_due, sync_official_holidays
from app.inbox_replies import check_inbox_replies
from app.ingest import enqueue
from app.mail import poll_mail
from app.models import Conversation, Draft, Job, TariffStudy
from app.notifications import notify_job_failed, notify_job_recovered, watch_gowa
from app.notifications import scan as scan_notifications
from app.processing import alert_conversation, analyze_message, schedule_overdue
from app.registration import run_registration_check, watch_sync
from app.registration_replies import check_registration_replies, suggest_registration_reasons
from app.staff_replies import CHECK_SECONDS as STAFF_REPLY_CHECK_SECONDS
from app.staff_replies import check_staff_replies
from app.tariff_lab import run_study

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
# GOWA status is a network call; checking it every 5 minutes is enough for a notice.
GOWA_CHECK_SECONDS = 300


async def run_job() -> bool:
    with SessionLocal() as db:
        job = db.scalar(
            select(Job)
            .where(Job.status == "pending", Job.run_at <= datetime.now(UTC))
            .order_by(Job.id)
            .with_for_update(skip_locked=True)
        )
        if not job:
            return False
        job.status = "running"
        job.attempts += 1
        job.started_at = datetime.now(UTC)
        db.commit()
        try:
            if job.kind == "analyze":
                await analyze_message(db, int(job.payload["message_id"]))
            elif job.kind == "auto_reply":
                await run_auto_reply(db, int(job.payload["message_id"]))
            elif job.kind == "send":
                await send_approved(db, int(job.payload["draft_id"]))
            elif job.kind == "alert":
                await alert_conversation(
                    db, int(job.payload["conversation_id"]), str(job.payload["reason"])
                )
            elif job.kind == "tariff_study":
                await run_study(db, int(job.payload["study_id"]))
            elif job.kind == "registration_check":
                run_registration_check(db)
            elif job.kind == "holiday_sync":
                sync_official_holidays(db)
            else:
                raise ValueError("Unknown job kind")
            job.status = "done"
            job.error = None
            db.commit()
        except Exception as exc:
            logger.exception("Job %s failed", job.id)
            db.rollback()
            job = db.get(Job, job.id)
            job.error = f"{type(exc).__name__}: {str(exc)[:300]}"
            if job.kind == "tariff_study":
                study = db.get(TariffStudy, int(job.payload["study_id"]))
                if study:
                    study.status = "failed" if job.attempts >= 5 else "pending"
                    study.error = job.error
            if job.kind == "send" or job.attempts >= 5:
                job.status = "failed"
                try:
                    notify_job_failed(db, job.kind, job.id, job.error)
                except Exception:
                    logger.exception("Could not record notification for job %s", job.id)
            else:
                job.status = "pending"
                job.run_at = datetime.now(UTC) + timedelta(seconds=min(300, 2**job.attempts * 5))
            db.commit()
        return True


def watch_csv() -> None:
    folder = Path(get_settings().csv_watch_dir)
    if not folder.exists():
        return
    with SessionLocal() as db:
        for path in sorted(folder.glob("*.csv")):
            if not path.is_file():
                continue
            try:
                batch = import_csv(
                    db,
                    raw=path.read_bytes(),
                    filename=path.name,
                    source="folder",
                    mapping=env_csv_mapping(),
                )
                if batch.status == "valid":
                    path.rename(path.with_suffix(".imported"))
                else:
                    path.rename(path.with_suffix(".invalid"))
            except Exception:
                logger.exception("CSV import failed: %s", path.name)


def schedule_holiday_sync() -> None:
    """Queue the daily refresh of official holidays (BOE, DOGV and town hall)."""
    with SessionLocal() as db:
        if sync_due(db):
            enqueue(
                db, key=f"holiday-sync:{date.today().isoformat()}", kind="holiday_sync", payload={}
            )
            db.commit()


def recover_stale_jobs() -> None:
    cutoff = datetime.now(UTC) - timedelta(minutes=5)
    with SessionLocal() as db:
        jobs = db.scalars(select(Job).where(Job.status == "running", Job.started_at < cutoff)).all()
        for job in jobs:
            if job.kind == "send":
                draft = db.get(Draft, int(job.payload["draft_id"]))
                if draft and draft.status in {"sending", "queued"}:
                    draft.status = "delivery_unknown"
                    draft.error = "Worker interrupted during delivery; verify externally"
                    conv = db.get(Conversation, draft.conversation_id)
                    if conv:
                        conv.status = "delivery_unknown"
                job.status = "failed"
            else:
                if job.kind == "tariff_study":
                    study = db.get(TariffStudy, int(job.payload["study_id"]))
                    if study:
                        study.status = "pending"
                        study.error = "El worker se interrumpió; el estudio se reintentará"
                job.status = "pending"
                job.run_at = datetime.now(UTC)
            try:
                with db.begin_nested():
                    notify_job_recovered(db, job.kind, job.id)
            except Exception:
                logger.exception("Could not record notification for job %s", job.id)
        db.commit()


async def main() -> None:
    last_mail = 0.0
    last_housekeeping = 0.0
    last_gowa_check = 0.0
    last_staff_replies = 0.0
    settings = get_settings()
    while True:
        tick = time.monotonic()
        if tick - last_mail >= settings.imap_poll_seconds:
            with SessionLocal() as db:
                try:
                    poll_mail(db)
                except Exception:
                    logger.exception("IMAP connection failed")
            last_mail = tick
        if tick - last_housekeeping >= 30:
            with SessionLocal() as db:
                schedule_overdue(db)
            with SessionLocal() as db:
                try:
                    watch_sync(db)
                except Exception:
                    logger.exception("Avantio sync watchdog failed")
            recover_stale_jobs()
            watch_csv()
            try:
                schedule_holiday_sync()
            except Exception:
                logger.exception("Holiday sync scheduling failed")
            with SessionLocal() as db:
                scan_notifications(db)
            last_housekeeping = tick
        if tick - last_gowa_check >= GOWA_CHECK_SECONDS:
            with SessionLocal() as db:
                try:
                    await watch_gowa(db)
                except Exception:
                    logger.exception("WhatsApp notification check failed")
            last_gowa_check = tick
        if tick - last_staff_replies >= STAFF_REPLY_CHECK_SECONDS:
            with SessionLocal() as db:
                try:
                    check_staff_replies(db)
                except Exception:
                    logger.exception("Sent folder check for auto-replies failed")
            with SessionLocal() as db:
                try:
                    check_inbox_replies(db)
                except Exception:
                    logger.exception("Sent folder check for pending conversations failed")
            with SessionLocal() as db:
                try:
                    check_registration_replies(db)
                except Exception:
                    logger.exception("Sent folder check for registration alerts failed")
            with SessionLocal() as db:
                try:
                    await suggest_registration_reasons(db)
                except Exception:
                    logger.exception("Registration reply suggestions failed")
            last_staff_replies = tick
        if not await run_job():
            await asyncio.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    asyncio.run(main())
