from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from app.config import get_settings
from app.db import SessionLocal
from app.models import ReservationSyncState, now
from app.planning import MAX_BYTES, commit_import
from app.reservation_sync import SYNC_STATE_KEY


def _state(db) -> ReservationSyncState:
    state = db.get(ReservationSyncState, SYNC_STATE_KEY)
    if state is None:
        state = ReservationSyncState(key=SYNC_STATE_KEY)
        db.add(state)
        db.flush()
    return state


def mark_running() -> None:
    with SessionLocal() as db:
        state = _state(db)
        state.status = "running"
        state.last_attempt_at = now()
        state.last_error = None
        db.commit()


def mark_failed(message: str) -> None:
    with SessionLocal() as db:
        state = _state(db)
        if state.last_attempt_at is None:
            state.last_attempt_at = now()
        state.status = "failed"
        state.last_error = message[:500]
        db.commit()


def set_interval(hours: int) -> None:
    if hours < 1 or hours > 168:
        raise ValueError("El intervalo debe estar entre 1 y 168 horas.")
    with SessionLocal() as db:
        state = _state(db)
        state.interval_hours = hours
        db.commit()


def import_reservations(filename: str) -> int:
    settings = get_settings()
    import_root = Path(settings.avantio_import_dir).resolve()
    path = Path(filename).resolve(strict=True)
    try:
        path.relative_to(import_root)
    except ValueError as exc:
        raise ValueError("El CSV está fuera de la carpeta temporal de importación.") from exc
    if not path.is_file() or path.suffix.lower() != ".csv":
        raise ValueError("El archivo descargado no es un CSV.")
    if path.stat().st_size == 0 or path.stat().st_size > MAX_BYTES:
        raise ValueError("El CSV está vacío o supera el límite de 10 MB.")

    raw = path.read_bytes()
    checksum = hashlib.sha256(raw).hexdigest()
    with SessionLocal() as db:
        try:
            batch = commit_import(
                db,
                dataset="reservations",
                raw=raw,
                filename=path.name,
                actor_user_id=None,
                expected_checksum=checksum,
                source="avantio_chrome",
            )
        except Exception:
            db.rollback()
            try:
                mark_failed("Falló la importación del CSV de Avantio.")
            except Exception:
                pass
            raise
        state = _state(db)
        if batch.status != "valid":
            message = (batch.error or "El CSV no pasó la validación.")[:500]
            state.status = "failed"
            state.last_error = message
            db.commit()
            raise ValueError(message)
        state.status = "succeeded"
        state.last_success_at = now()
        state.last_batch_id = batch.id
        state.row_count = batch.row_count
        state.last_error = None
        db.commit()
        return batch.row_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Operaciones de sincronización de reservas")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("start", help="Registrar un intento de sincronización")
    failed = commands.add_parser("fail", help="Registrar un error de sincronización")
    failed.add_argument("--message", required=True)
    interval = commands.add_parser("set-interval", help="Guardar el intervalo activo")
    interval.add_argument("hours", type=int)
    imported = commands.add_parser("import", help="Validar e importar un CSV descargado")
    imported.add_argument("filename")
    args = parser.parse_args()

    try:
        if args.command == "start":
            mark_running()
            return 0
        if args.command == "fail":
            mark_failed(args.message)
            return 0
        if args.command == "set-interval":
            set_interval(args.hours)
            return 0
        rows = import_reservations(args.filename)
        print(f"CSV de Avantio importado correctamente ({rows} filas).")
        return 0
    except ValueError as exc:
        message = str(exc).strip() or "Error de importación."
        print(message[:500], file=sys.stderr)
        return 1
    except Exception as exc:
        message = f"Error interno de sincronización ({type(exc).__name__})."
        print(message[:500], file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
