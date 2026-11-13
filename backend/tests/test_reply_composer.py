from __future__ import annotations

import asyncio
from datetime import timedelta

from fastapi.testclient import TestClient

from app.auth import hasher
from app.config import get_settings
from app.db import get_db
from app.delivery import send_approved
from app.html_sanitize import sanitize_html, text_to_html
from app.main import app
from app.models import Conversation, Draft, EmailSignature, Message, User, now

PASSWORD = "very-long-test-password"


def _client(db, monkeypatch, *usernames):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_secret", "unit-test-secret-with-more-than-32-characters")
    monkeypatch.setattr(settings, "smtp_from", "Reservas <reservas@example.test>")
    for username in usernames:
        db.add(User(username=username, password_hash=hasher.hash(PASSWORD), role="operator"))
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _auth(client, username):
    login = client.post("/api/login", json={"username": username, "password": PASSWORD})
    return {"Authorization": f"Bearer {login.json()['token']}"}


def test_sanitizer_keeps_formatting_and_drops_active_content():
    dirty = (
        '<p onclick="steal()" style="color: red; position: fixed">Hola <b>Ana</b></p>'
        "<script>alert(1)</script><style>p{display:none}</style>"
        '<a href="javascript:alert(1)">mal</a><a href="https://example.test">bien</a>'
        '<img src="https://tracker.test/x.png"><img src="data:image/png;base64,AAAA">'
        '<iframe src="https://x.test"></iframe><div data-signature="1">Firma</div>'
    )
    clean = sanitize_html(dirty)
    assert "<b>Ana</b>" in clean
    assert 'style="color: red"' in clean
    assert "onclick" not in clean and "position" not in clean
    assert "script" not in clean and "alert" not in clean and "display:none" not in clean
    assert "javascript" not in clean
    assert 'href="https://example.test"' in clean
    assert "tracker.test" not in clean
    assert 'src="data:image/png;base64,AAAA"' in clean
    assert "iframe" not in clean
    assert 'data-signature="1"' in clean


def test_text_to_html_escapes_and_keeps_paragraphs():
    expected = "<p>Hola &lt;Ana&gt;<br>línea</p><p>Adiós</p>"
    assert text_to_html("Hola <Ana>\nlínea\n\nAdiós") == expected


def test_signatures_are_private_per_operator_and_mailbox(db, monkeypatch):
    try:
        with _client(db, monkeypatch, "ana", "luis") as client:
            ana, luis = _auth(client, "ana"), _auth(client, "luis")
            listing = client.get("/api/signatures", headers=ana).json()
            assert listing["mailboxes"][:2] == ["*", "reservas@example.test"]
            assert listing["signatures"] == []
            saved = client.put(
                "/api/signatures",
                headers=ana,
                json={
                    "mailbox": "reservas@example.test",
                    "html_body": "<p>Ana<script>x</script></p>",
                },
            )
            assert saved.status_code == 200
            assert saved.json()["signatures"][0]["html_body"] == "<p>Ana</p>"
            assert client.get("/api/signatures", headers=luis).json()["signatures"] == []
            unknown = client.put(
                "/api/signatures",
                headers=ana,
                json={"mailbox": "otro@x.test", "html_body": "<p>x</p>"},
            )
            assert unknown.status_code == 422
            cleared = client.put(
                "/api/signatures",
                headers=ana,
                json={"mailbox": "reservas@example.test", "html_body": "<script>x</script>"},
            )
            assert cleared.json()["signatures"] == []
            assert db.query(EmailSignature).count() == 0
    finally:
        app.dependency_overrides.clear()


def test_draft_stores_sanitized_html_subject_and_cc(db, monkeypatch):
    conv = Conversation(channel="email", external_key="<c@example.test>", status="pending")
    db.add(conv)
    db.flush()
    db.add(
        Message(
            conversation_id=conv.id, channel="email", provider_id="<in@example.test>",
            direction="inbound", body="Hola", sender="Cliente <cliente@example.test>",
            subject="Consulta agosto",
        )
    )
    db.commit()
    try:
        with _client(db, monkeypatch, "ana") as client:
            auth = _auth(client, "ana")
            detail = client.get(f"/api/conversations/{conv.id}", headers=auth).json()
            assert detail["reply"] == {
                "mailbox": "Reservas <reservas@example.test>",
                "to": "cliente@example.test",
                "subject": "Re: Consulta agosto",
            }
            bad_cc = client.put(
                f"/api/conversations/{conv.id}/draft",
                headers=auth,
                json={"text": "Hola", "cc": "no-es-correo"},
            )
            assert bad_cc.status_code == 422
            saved = client.put(
                f"/api/conversations/{conv.id}/draft",
                headers=auth,
                json={
                    "text": "Hola",
                    "html_body": '<p>Hola <img src="x" onerror="alert(1)"></p>',
                    "subject": "Re: Agosto",
                    "cc": "Jefa <jefa@example.test>; otro@example.test",
                },
            )
            assert saved.status_code == 200, saved.text
            saved = saved.json()
            assert saved["html_body"] == "<p>Hola </p>"
            assert saved["subject"] == "Re: Agosto"
            assert saved["cc"] == "jefa@example.test, otro@example.test"
    finally:
        app.dependency_overrides.clear()


def test_email_delivery_sends_html_cc_subject_and_quotes_original(db, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_from", "reservas@example.test")
    monkeypatch.setattr("app.delivery.READ_ONLY_MODE", False)
    sent = []
    monkeypatch.setattr("app.delivery.smtp_send", lambda **kwargs: sent.append(kwargs))
    conv = Conversation(
        channel="email", external_key="<c@example.test>", status="queued",
        customer_address="cliente@example.test", last_inbound_at=now() - timedelta(hours=1),
    )
    db.add(conv)
    db.flush()
    db.add(
        Message(
            conversation_id=conv.id, channel="email", provider_id="<in@example.test>",
            direction="inbound", body="¿Hay sitio?", sender="cliente@example.test",
            subject="Consulta", created_at=now() - timedelta(hours=1),
        )
    )
    draft = Draft(
        conversation_id=conv.id, text="Sí, hay sitio.", html_body="<p><b>Sí</b>, hay sitio.</p>",
        subject="Re: Consulta agosto", cc="jefa@example.test", status="queued", approved_at=now(),
    )
    db.add(draft)
    db.commit()
    asyncio.run(send_approved(db, draft.id))
    assert draft.status == "sent"
    [mail] = sent
    assert mail["to"] == "cliente@example.test"
    assert mail["cc"] == ["jefa@example.test"]
    assert mail["subject"] == "Re: Consulta agosto"
    assert mail["html_body"].startswith("<p><b>Sí</b>, hay sitio.</p>")
    assert "<blockquote" in mail["html_body"] and "¿Hay sitio?" in mail["html_body"]
    assert mail["text"].startswith("Sí, hay sitio.") and "> ¿Hay sitio?" in mail["text"]
    outbound = db.query(Message).filter(Message.direction == "outbound").one()
    assert outbound.html_body == "<p><b>Sí</b>, hay sitio.</p>"
