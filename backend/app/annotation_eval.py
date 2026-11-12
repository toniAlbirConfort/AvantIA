"""Measure the classifier against the operators' annotations.

Read-only: it never writes to the database and prints only aggregate figures, no message text.

    python -m app.annotation_eval            # what the model said in production (stored)
    python -m app.annotation_eval --live     # rerun the current prompts on the eval split
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.annotations import INTENTS, Prediction, score
from app.config import get_settings
from app.integrations import ollama_json
from app.models import Message, MessageAnnotation
from app.processing import (
    CLASSIFY_SYSTEM,
    EXTRACT_SYSTEM,
    _classification_context,
    _safe_analysis,
    _safe_classification,
)


def _annotations(db: Session, split: str) -> list[MessageAnnotation]:
    query = select(MessageAnnotation).order_by(MessageAnnotation.id)
    if split != "all":
        query = query.where(MessageAnnotation.split == split)
    return list(db.scalars(query).all())


def stored_report(db: Session, split: str) -> dict:
    rows = [
        (item, Prediction(item.predicted_label, item.predicted_data))
        for item in _annotations(db, split)
    ]
    return {"mode": "stored", "split": split, **score(rows)}


async def live_report(db: Session, split: str, model_name: str | None) -> dict:
    rows = []
    errors = 0
    for annotation in _annotations(db, split):
        message = db.get(Message, annotation.message_id)
        if message is None:
            continue
        context = _classification_context(db, message)
        try:
            label, _, _ = _safe_classification(
                await ollama_json(CLASSIFY_SYSTEM, context, model_name=model_name)
            )
            data = None
            if INTENTS[annotation.intent].needs_availability:
                data = _safe_analysis(
                    await ollama_json(EXTRACT_SYSTEM, context, model_name=model_name)
                )
        except Exception:
            errors += 1
            label, data = None, None
        rows.append((annotation, Prediction(label, data)))
    return {
        "mode": "live",
        "split": split,
        "model": model_name or get_settings().ollama_model,
        "errors": errors,
        **score(rows),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="vuelve a consultar el modelo")
    parser.add_argument("--split", choices=["eval", "examples", "all"], default=None)
    parser.add_argument("--model", default=None, help="modelo de Ollama a evaluar")
    args = parser.parse_args(argv)

    from app.db import SessionLocal

    with SessionLocal() as db:
        try:
            if args.live:
                report = asyncio.run(live_report(db, args.split or "eval", args.model))
            else:
                report = stored_report(db, args.split or "all")
        finally:
            db.rollback()
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
