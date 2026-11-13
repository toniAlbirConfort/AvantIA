from __future__ import annotations

import pytest
from sqlalchemy import select

from app.ingest import ingest
from app.sensitive import iban_valid, redact_payment_data


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("visa 4111 1111 1111 1111 ok", "visa [tarjeta ••••1111] ok"),
        ("card 4111-1111-1111-1111", "card [tarjeta ••••1111]"),
        ("pegada 4111111111111111.", "pegada [tarjeta ••••1111]."),
        # Mistyped by the customer (fails Luhn) but unmistakably a card.
        (
            "this will go thru: 1234 5678 9012 3456 08/29",
            "this will go thru: [tarjeta ••••3456] [caducidad]",
        ),
        (
            "Card number: 4111 1111 1111 1111 Valid thru: 06/31",
            "Card number: [tarjeta ••••1111] Valid thru: [caducidad]",
        ),
        ("4111 1111 1111 1111 exp 09/2028", "[tarjeta ••••1111] exp [caducidad]"),
        ("CVV 123", "CVV [oculto]"),
        ("cvc: 4321 gracias", "cvc: [oculto] gracias"),
        ("código de seguridad 987", "código de seguridad [oculto]"),
        ("IBAN.GB82 WEST 1234 5698 7654 32.SWIFT", "IBAN.[IBAN ••••5432].SWIFT"),
        ("cuenta ES9121000418450200051332 gracias", "cuenta [IBAN ••••1332] gracias"),
        ("ES91 2100 0418 4502 0005 1332 CODIGO switch", "[IBAN ••••1332] CODIGO switch"),
    ],
)
def test_masks_payment_data(text, expected):
    assert redact_payment_data(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Reserva 31076138-130046 del 01/02/2027 al 01/03/2027",
        "Booking cancellation 31611701-130648",
        "teléfono 0047 1234 5678 o +4790000000",
        "Llegamos el 15.05.26 a las 17:50",
        "Total 3.382,89 € pagado",
        "ES12 no es un IBAN y AB12CDEF tampoco",
        "pedido 1234 5678 9012",
        "",
    ],
)
def test_leaves_other_numbers_alone(text):
    assert redact_payment_data(text) == text


def test_none_passes_through():
    assert redact_payment_data(None) is None


def test_iban_checksum():
    assert iban_valid("GB82WEST12345698765432")
    assert not iban_valid("GB82WEST12345698765433")


def test_ingest_stores_masked_text(db):
    message, duplicate = ingest(
        db,
        channel="email",
        external_key="<pay@example.test>",
        provider_id="<pay@example.test>",
        body="Charge 4111 1111 1111 1111 08/29 CVV 123, refund to GB82 WEST 1234 5698 7654 32",
        html_body="<p>Charge 4111 1111 1111 1111</p>",
        sender="Guest <guest@example.test>",
        subject="Card 4111111111111111",
    )
    db.commit()
    assert not duplicate
    assert "4111 1111" not in message.body and "123," not in message.body
    assert "[tarjeta ••••1111] [caducidad] CVV [oculto]" in message.body
    assert "[IBAN ••••5432]" in message.body
    assert message.html_body == "<p>Charge [tarjeta ••••1111]</p>"
    assert message.subject == "Card [tarjeta ••••1111]"
    assert message.conversation.subject == "Card [tarjeta ••••1111]"


def test_redact_stored_reports_then_applies(db):
    from app.models import Conversation, Message
    from app.redact_stored import redact_stored

    conversation = Conversation(
        channel="email", external_key="<old@example.test>", subject="Pago 4111111111111111"
    )
    db.add(conversation)
    db.flush()
    db.add_all(
        [
            Message(
                conversation_id=conversation.id,
                channel="email",
                provider_id="<old-1@example.test>",
                direction="inbound",
                body="Tarjeta 4111 1111 1111 1111 CVV 123",
            ),
            Message(
                conversation_id=conversation.id,
                channel="email",
                provider_id="<old-2@example.test>",
                direction="inbound",
                body="Reserva 31076138-130046, gracias",
            ),
            Message(
                conversation_id=conversation.id,
                channel="email",
                provider_id="<old-3@example.test>",
                direction="outbound",
                body="Nuestro IBAN GB82 WEST 1234 5698 7654 32",
            ),
        ]
    )
    db.commit()

    report = redact_stored(db)
    assert (report["messages"], report["conversations"]) == (1, 1)
    assert (
        "4111 1111"
        in db.scalars(
            select(Message.body).where(Message.provider_id == "<old-1@example.test>")
        ).one()
    )

    applied = redact_stored(db, apply=True, batch_size=1)
    assert applied["messages"] == 1
    bodies = dict(db.execute(select(Message.provider_id, Message.body)).all())
    assert bodies["<old-1@example.test>"] == "Tarjeta [tarjeta ••••1111] CVV [oculto]"
    assert bodies["<old-2@example.test>"] == "Reserva 31076138-130046, gracias"
    assert "GB82 WEST" in bodies["<old-3@example.test>"]  # our own outbound text is left alone
    assert db.get(Conversation, conversation.id).subject == "Pago [tarjeta ••••1111]"
    assert redact_stored(db)["messages"] == 0
