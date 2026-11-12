"""Detect who the customer really is and what they want, from the message itself.

Colleagues who take a phone call write the lead to this mailbox: the customer's email or
phone and the dates are in the body, and the sender is the colleague. Forwards ("RV:",
"Fwd:") carry the customer in the quoted header. Detection is deterministic (regular
expressions, no model), so it can be recomputed at any time and never invents a contact.
"""

from __future__ import annotations

import re
from email.utils import parseaddr

from app.config import get_settings

EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-zA-Z]{2,24}(?![\w-])")
INTERNATIONAL_PHONE_RE = re.compile(r"(?<![\w+])(?:\+|00)\d(?:[\s().-]?\d){6,14}(?!\d)")
SPANISH_PHONE_RE = re.compile(r"(?<![\w+/.-])[6789]\d{2}[\s.-]?\d{3}[\s.-]?\d{3}(?![\d/])")
# A quoted header introduces someone else's message inside this one.
FORWARD_HEADER_RE = re.compile(
    r"(?:^|(?<=[\s>*]))(?:De|From|Von|Van|Fra|Från|Da)\s*:\s*"
    r"(?P<from>[^<>\r\n:]{0,80}(?:<|\[mailto:)[^\s<>\]]+@[^\s<>\]]+(?:>|\]))",
    re.I | re.M,
)
QUOTE_START_RE = re.compile(
    r"^[ \t]*(?:"
    r"(?:De|From|Von|Van|Fra|Från|Da)\s*:\s*\S"
    r"|-{2,}\s*(?:Original Message|Mensaje original|Forwarded message|Mensaje reenviado)"
    r"|(?:El|On|Am|Op|Le|Den)\b.{0,160}(?:escribió|wrote|schrieb|schreef|écrit|skrev)\s*:?"
    r"|.{0,160}\bskrev\s*<[^>]+>:"
    r"|>"
    r")",
    re.I | re.M,
)
PRICE_RE = re.compile(
    r"\b(?:price|prices|pricing|precio|precios|oferta|offerta|offer|quote|quotation|"
    r"presupuesto|cotizaci[oó]n|tarifa|tarifas|coste|costes|cost|costs|how much|cu[aá]nto|"
    r"prijs|prijzen|preis|preise|pris|prisen|kosten|kost)\b",
    re.I,
)
ROLE_LOCALS = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon", "postmaster",
    "dpd", "dpo", "privacy", "privacidad", "unsubscribe", "bounce", "bounces",
}
GLUED_WORD_RE = re.compile(
    r"(?:asunto|subject|enviado|sent|para|fecha|date|betreff|onderwerp|emne|ämne)$"
)
FORWARD_SUBJECT_RE =re.compile(r"^\s*(?:RV|RE|FW|FWD|TR|WG|SV|VS|AW|Antw)\s*:", re.I)
NAME_SUBJECT_RE = re.compile(r"^[A-ZÀ-ÖØ-Þ][\w'’.-]+(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ'’.-]+){1,4}$")


def _domains(value: str) -> list[str]:
    return [item.strip().lower() for item in value.split(",") if item.strip()]


def _is_internal(address: str, domains: list[str]) -> bool:
    domain = address.rsplit("@", 1)[-1].lower()
    return any(domain == item or domain.endswith("." + item) for item in domains)


def _digits(phone: str) -> str:
    return re.sub(r"\D", "", phone)


def normalize_phone(phone: str) -> str | None:
    raw = phone.strip()
    digits = _digits(raw)
    if raw.startswith("00"):
        digits = digits[2:]
        raw = "+" + digits
    if raw.startswith("+"):
        return "+" + digits if 8 <= len(digits) <= 15 else None
    if len(digits) == 9 and digits[0] in "6789":
        return "+34" + digits
    return None


def same_phone(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    a, b = _digits(left), _digits(right)
    return len(a) >= 8 and len(b) >= 8 and a[-9:] == b[-9:]


def new_part(body: str) -> str:
    """The text the sender wrote now, without the quoted history below it."""
    match = QUOTE_START_RE.search(body or "")
    return (body or "")[: match.start()] if match else (body or "")


def _clean_address(match: str) -> str:
    address = match.strip(".").lower()
    local, _, domain = address.partition("@")
    # Text extracted from HTML can glue the next header word to the address.
    glued = GLUED_WORD_RE.sub("", domain)
    if glued != domain and "." in glued and len(glued.rsplit(".", 1)[-1]) >= 2:
        domain = glued
    return f"{local}@{domain}"


def _usable(address: str, internal: list[str]) -> bool:
    return bool(address) and not _is_internal(address, internal) and (
        address.split("@", 1)[0] not in ROLE_LOCALS
    )


def find_emails(text: str, *, internal: list[str]) -> list[str]:
    found: list[str] = []
    for match in EMAIL_RE.findall(text or ""):
        address = _clean_address(match)
        if not _usable(address, internal):
            continue
        if address not in found:
            found.append(address)
    return found


def find_phones(text: str, *, exclude: list[str]) -> list[str]:
    found: list[str] = []
    for regex in (INTERNATIONAL_PHONE_RE, SPANISH_PHONE_RE):
        for match in regex.findall(text or ""):
            phone = normalize_phone(match)
            if not phone or any(same_phone(phone, item) for item in exclude + found):
                continue
            found.append(phone)
    return found


def wants_price(text: str) -> bool:
    return bool(PRICE_RE.search(text or ""))


def _forwarded_sender(body: str, internal: list[str]) -> tuple[str | None, str | None]:
    for match in FORWARD_HEADER_RE.finditer(body or ""):
        value = match.group("from").strip()
        name, address = parseaddr(value.replace("[mailto:", "<").replace("]", ">"))
        if not address:
            emails = EMAIL_RE.findall(value)
            address = emails[0] if emails else ""
            name = value.split("<", 1)[0].strip(" '\"") if address else name
        address = _clean_address(address) if address else ""
        if _usable(address, internal):
            return (name.strip(" '\"") or None), address
    return None, None


def detect_contact(
    *, channel: str, sender: str | None, subject: str | None, body: str | None
) -> dict:
    """Return the customer's contact and request, separating colleagues' relayed leads."""
    settings = get_settings()
    internal = _domains(settings.internal_sender_domains)
    own_phones = [
        phone
        for item in settings.internal_phone_numbers.split(",")
        if (phone := normalize_phone(item))
    ]
    body = body or ""
    sender_name, sender_address = parseaddr(sender or "")
    sender_address = sender_address.lower()
    relayed = channel == "email" and bool(sender_address) and _is_internal(sender_address, internal)

    if relayed:
        # The colleague's note and any forwarded customer message are both useful here.
        forward_name, forward_address = _forwarded_sender(body, internal)
        emails = find_emails(body, internal=internal)
        if forward_address:
            emails = [forward_address] + [item for item in emails if item != forward_address]
        phones = find_phones(body, exclude=own_phones)
        name = forward_name
        clean_subject = (subject or "").strip()
        if not name and clean_subject and not FORWARD_SUBJECT_RE.match(clean_subject):
            if NAME_SUBJECT_RE.match(clean_subject) and not EMAIL_RE.search(clean_subject):
                name = clean_subject
        if not emails and not phones:
            return {"source": "internal", "relayed_by": sender_address, "name": None,
                    "emails": [], "phones": [], "wants_price": False}
        return {
            "source": "relayed",
            "relayed_by": sender_address,
            "name": name,
            "emails": emails,
            "phones": phones,
            "wants_price": wants_price(body),
        }

    text = new_part(body)
    emails = (
        [sender_address] if channel == "email" and _usable(sender_address, internal) else []
    )
    emails += [item for item in find_emails(text, internal=internal) if item not in emails]
    phones = find_phones(text, exclude=own_phones)
    if channel == "whatsapp" and sender:
        phone = normalize_phone("+" + _digits(sender.split("@", 1)[0]))
        if phone and not any(same_phone(phone, item) for item in phones):
            phones.insert(0, phone)
    return {
        "source": "direct",
        "relayed_by": None,
        "name": sender_name or None,
        "emails": emails,
        "phones": phones,
        "wants_price": wants_price(text),
    }


def message_contact(message, conversation_channel: str | None = None) -> dict | None:
    if message is None or message.direction != "inbound":
        return None
    return detect_contact(
        channel=conversation_channel or message.channel,
        sender=message.sender,
        subject=message.subject,
        body=message.body,
    )


def conversation_contact(messages: list, annotations: dict | None = None) -> dict | None:
    """The customer to answer: a relayed lead wins, then the latest direct message.

    Contact data an operator confirmed while annotating takes precedence over detection.
    """
    inbound = [item for item in messages if item.direction == "inbound"]
    if not inbound:
        return None
    detected = [(item, message_contact(item)) for item in reversed(inbound)]
    chosen = next(
        ((item, contact) for item, contact in detected if contact["source"] == "relayed"),
        detected[0],
    )
    message, contact = chosen
    contact = {**contact, "message_id": message.id, "verified": False}
    annotation = (annotations or {}).get(message.id)
    expected = getattr(annotation, "expected", None) or {}
    if expected.get("contact_email") or expected.get("contact_phone"):
        if expected.get("contact_email"):
            contact["emails"] = [expected["contact_email"]] + [
                item for item in contact["emails"] if item != expected["contact_email"]
            ]
        if expected.get("contact_phone"):
            contact["phones"] = [expected["contact_phone"]] + [
                item
                for item in contact["phones"]
                if not same_phone(item, expected["contact_phone"])
            ]
        contact["verified"] = True
    if expected.get("wants_price") is not None:
        contact["wants_price"] = bool(expected["wants_price"])
    return contact
