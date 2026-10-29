from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.availability import env_csv_mapping, import_csv
from app.config import get_settings
from app.db import SessionLocal
from app.delivery import send_approved
from app.mail import poll_mail
from app.models import Conversation, Draft, Job
from app.processing import alert_conversation, analyze_message, schedule_overdue

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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
            elif job.kind == "send":
                await send_approved(db, int(job.payload["draft_id"]))
            elif job.kind == "alert":
                await alert_conversation(
                    db, int(job.payload["conversation_id"]), str(job.payload["reason"])
                )
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
            if job.kind == "send" or job.attempts >= 5:
                job.status = "failed"
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
                job.status = "pending"
                job.run_at = datetime.now(UTC)
        db.commit()


async def main() -> None:
    last_mail = 0.0
    last_housekeeping = 0.0
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
            recover_stale_jobs()
            watch_csv()
            last_housekeeping = tick
        if not await run_job():
            await asyncio.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    asyncio.run(main())
