from __future__ import annotations

import json
import smtplib
import ssl
from email.message import EmailMessage

import httpx

from app.config import get_settings


async def ollama_json(system: str, user: str) -> dict:
    settings = get_settings()
    if not settings.ollama_url or not settings.ollama_model:
        raise RuntimeError("Ollama no configurado")
    async with httpx.AsyncClient(timeout=settings.ollama_timeout_seconds) as client:
        response = await client.post(
            settings.ollama_url.rstrip("/") + "/api/chat",
            json={
                "model": settings.ollama_model,
                "stream": False,
                "format": "json",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "options": {"temperature": 0.1},
            },
        )
        response.raise_for_status()
    content = response.json()["message"]["content"]
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError("Ollama devolvió un formato no válido")
    return value


async def gowa_send(chat_id: str, text: str) -> str | None:
    settings = get_settings()
    username, sep, password = settings.gowa_basic_auth.partition(":")
    if not sep or not username or not settings.gowa_device_id:
        raise RuntimeError("GOWA no configurado")
    async with httpx.AsyncClient(timeout=20, auth=(username, password)) as client:
        response = await client.post(
            settings.gowa_url.rstrip("/") + "/send/message",
            headers={"X-Device-Id": settings.gowa_device_id},
            json={"phone": chat_id, "message": text},
        )
        response.raise_for_status()
    data = response.json()
    return str(data.get("id") or data.get("message_id") or "") or None


def smtp_send(
    *,
    to: str,
    subject: str,
    text: str,
    message_id: str,
    in_reply_to: str | None,
    references: str | None,
) -> None:
    settings = get_settings()
    if not settings.smtp_host or not settings.smtp_from:
        raise RuntimeError("SMTP no configurado")
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    message["Message-ID"] = message_id
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if references:
        message["References"] = references
    message.set_content(text)
    if settings.smtp_starttls:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP_SSL(
            settings.smtp_host, settings.smtp_port, timeout=30, context=ssl.create_default_context()
        ) as smtp:
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)
