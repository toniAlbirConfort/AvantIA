"""Manual annotation of inbound messages.

Operators label real messages with a detailed intent and, when the intent needs an
availability check, with the dates and guests the customer actually asked for. These labels
are the ground truth used to measure the classifier and, later, to give it examples.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MessageAnnotation, now


@dataclass(frozen=True)
class Intent:
    label: str
    needs_availability: bool
    name: str


INTENTS: dict[str, Intent] = {
    "availability_request": Intent("demand", True, "Consulta de disponibilidad"),
    "price_negotiation": Intent("demand", False, "Precio, descuento u oferta"),
    "b2b_agency": Intent("demand", False, "Agencia, grupo o socio"),
    "stay_change": Intent("guest_service", True, "Ampliar o cambiar estancia"),
    "arrival_keys": Intent("guest_service", False, "Llegada, llaves o check-in"),
    "payment": Intent("guest_service", False, "Pagos, depósitos o reembolsos"),
    "cancellation": Intent("guest_service", False, "Cancelación"),
    "incident": Intent("guest_service", False, "Incidencia u otra petición"),
    "security_fraud": Intent("guest_service", False, "Fraude o mensaje sospechoso"),
    "lost_interest": Intent("non_demand", False, "Pierde interés"),
    "courtesy": Intent("non_demand", False, "Agradecimiento o despedida"),
    "platform_notice": Intent("non_demand", False, "Aviso automático de plataforma"),
    "marketing": Intent("non_demand", False, "Publicidad o newsletter"),
    "other": Intent("non_demand", False, "Otro asunto ajeno"),
}

EXPECTED_FIELDS = ("arrival", "departure", "guests", "property_query")
# One message in five is kept aside to measure the model; the rest can serve as examples.
EVAL_SHARE = 5


def split_for(message_id: int) -> str:
    digest = hashlib.sha256(f"avantia-annotation:{message_id}".encode()).digest()
    return "eval" if digest[0] % EVAL_SHARE == 0 else "examples"


def clean_expected(intent: str, expected: dict | None) -> dict | None:
    """Validate the operator's dates and guests. Missing values mean the customer omitted them."""
    if not INTENTS[intent].needs_availability:
        return None
    expected = expected or {}
    arrival = _date_or_none(expected.get("arrival"), "arrival")
    departure = _date_or_none(expected.get("departure"), "departure")
    if arrival and departure and arrival >= departure:
        raise ValueError("La salida debe ser posterior a la llegada")
    guests = expected.get("guests")
    if guests in ("", None):
        guests = None
    elif isinstance(guests, bool) or not isinstance(guests, int) or not 1 <= guests <= 100:
        raise ValueError("El número de personas debe estar entre 1 y 100")
    property_query = expected.get("property_query")
    if not isinstance(property_query, str) or not property_query.strip():
        property_query = None
    return {
        "arrival": arrival.isoformat() if arrival else None,
        "departure": departure.isoformat() if departure else None,
        "guests": guests,
        "property_query": property_query.strip()[:150] if property_query else None,
    }


def _date_or_none(value, field: str) -> date | None:
    if value in ("", None):
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"Fecha no válida en {field}") from None


def is_complete(expected: dict | None) -> bool:
    """An availability request can be checked automatically once dates and guests are known."""
    if not expected:
        return False
    return all(expected.get(field) for field in ("arrival", "departure", "guests"))


def save_annotation(
    db: Session,
    message,
    *,
    intent: str,
    expected: dict | None,
    note: str | None,
    user_id: int | None,
) -> MessageAnnotation:
    if intent not in INTENTS:
        raise ValueError("Intención desconocida")
    cleaned = clean_expected(intent, expected)
    annotation = db.scalar(
        select(MessageAnnotation).where(MessageAnnotation.message_id == message.id)
    )
    if annotation is None:
        # Snapshot what the model said before the human looked, so the comparison is fair.
        annotation = MessageAnnotation(
            message_id=message.id,
            split=split_for(message.id),
            predicted_label=message.classification_label,
            predicted_confidence=message.classification_confidence,
            predicted_data=message.classification_data,
            predicted_model=message.classification_model,
        )
        db.add(annotation)
    annotation.intent = intent
    annotation.label = INTENTS[intent].label
    annotation.expected = cleaned
    annotation.note = note.strip()[:300] if note and note.strip() else None
    annotation.annotated_by_id = user_id
    annotation.updated_at = now()
    db.flush()
    return annotation


def serialize(annotation: MessageAnnotation | None) -> dict | None:
    if annotation is None:
        return None
    return {
        "intent": annotation.intent,
        "label": annotation.label,
        "expected": annotation.expected,
        "complete": is_complete(annotation.expected),
        "note": annotation.note,
        "split": annotation.split,
        "annotated_by_id": annotation.annotated_by_id,
        "updated_at": annotation.updated_at,
    }


def intent_catalog() -> list[dict]:
    return [
        {
            "intent": key,
            "label": intent.label,
            "name": intent.name,
            "needs_availability": intent.needs_availability,
        }
        for key, intent in INTENTS.items()
    ]


@dataclass
class Prediction:
    label: str | None
    data: dict | None


def score(rows: list[tuple[MessageAnnotation, Prediction]]) -> dict:
    """Compare predictions with annotations. Returns only aggregate figures, never message text."""
    labelled = [(a, p) for a, p in rows if p.label is not None]
    correct = sum(a.label == p.label for a, p in labelled)
    confusion = Counter(f"{a.label}->{p.label}" for a, p in labelled if a.label != p.label)
    by_intent: dict[str, dict] = {}
    for a, p in labelled:
        item = by_intent.setdefault(a.intent, {"total": 0, "correct": 0})
        item["total"] += 1
        item["correct"] += int(a.label == p.label)

    fields = {field: {"total": 0, "correct": 0} for field in EXPECTED_FIELDS}
    extraction_rows = 0
    for annotation, prediction in rows:
        if not INTENTS[annotation.intent].needs_availability or not annotation.expected:
            continue
        extraction_rows += 1
        predicted = prediction.data or {}
        for field in EXPECTED_FIELDS:
            expected_value = annotation.expected.get(field)
            if field == "property_query" and expected_value is None:
                continue
            fields[field]["total"] += 1
            fields[field]["correct"] += int(_same(field, expected_value, predicted.get(field)))
    return {
        "annotated": len(rows),
        "with_prediction": len(labelled),
        "label_accuracy": round(correct / len(labelled), 3) if labelled else None,
        "label_correct": correct,
        "confusion": dict(confusion.most_common()),
        "by_intent": by_intent,
        "extraction_messages": extraction_rows,
        "extraction": fields,
    }


def _same(field: str, expected, predicted) -> bool:
    if field == "property_query":
        if not expected or not isinstance(predicted, str):
            return False
        wanted, got = expected.casefold(), predicted.casefold()
        return wanted in got or got in wanted
    return expected == predicted


def summary(db: Session) -> dict:
    annotations = db.scalars(select(MessageAnnotation)).all()
    stored = [
        (item, Prediction(item.predicted_label, item.predicted_data)) for item in annotations
    ]
    return {
        "total": len(annotations),
        "by_intent": dict(Counter(item.intent for item in annotations)),
        "by_split": dict(Counter(item.split for item in annotations)),
        "complete_availability": sum(
            INTENTS[item.intent].needs_availability and is_complete(item.expected)
            for item in annotations
        ),
        "stored_model": score(stored),
    }
