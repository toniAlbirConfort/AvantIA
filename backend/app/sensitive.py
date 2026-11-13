"""Mask payment data that customers sometimes paste into emails and chats.

Inbound text is stored, shown in the inbox and sent to the language model, so card numbers,
expiry dates, security codes and IBANs are replaced before the message is saved. The original
email stays untouched in the mailbox for whoever really needs the full number.
"""

from __future__ import annotations

import re

# 13-19 digits, optionally grouped with spaces or hyphens (same shape as customers.CARD).
CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
# Four groups of four digits is a card even when the customer mistyped a digit (fails Luhn).
GROUPED_CARD = re.compile(r"(?<![\d-])\d{4}([ -])\d{4}\1\d{4}\1\d{4}(?![\d-])")
EXPIRY_AFTER_CARD = re.compile(
    r"(\[tarjeta ••••\d{4}\])"
    r"([\s,;:.-]*(?:(?:exp\w*|valid\w*|caduc\w*|vto|venc\w*|utl\w*|gyldig\w*)"
    r"(?:\s+(?:thru|date|til|hasta))?\s*[:.]?\s*)?)"
    r"(\d{1,2}\s*/\s*\d{2,4})(?!\d)",
    re.IGNORECASE,
)
SECURITY_CODE = re.compile(
    r"\b(cvv2?|cvc2?|cv2|c[oó]digo\s+de\s+seguridad|security\s+code|sikkerhetskode|kontrollsiffer)"
    r"(\s*(?:n[ºo°.]*|nr\.?|is)?\s*[:#=]?\s*)\d{3,4}\b",
    re.IGNORECASE,
)
# Liberal IBAN candidate; the real length is found by validating the checksum.
IBAN_CANDIDATE = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){10,30}")


def luhn(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        number = int(char)
        if index % 2:
            number = number * 2 - 9 if number > 4 else number * 2
        total += number
    return total % 10 == 0


def iban_valid(compact: str) -> bool:
    if not 15 <= len(compact) <= 34 or not compact[:2].isalpha() or not compact[2:4].isdigit():
        return False
    rearranged = compact[4:] + compact[:4]
    try:
        number = int("".join(str(int(char, 36)) for char in rearranged))
    except ValueError:
        return False
    return number % 97 == 1


def _mask_cards(text: str) -> str:
    def luhn_card(match: re.Match) -> str:
        digits = re.sub(r"\D", "", match.group())
        if 13 <= len(digits) <= 19 and luhn(digits):
            return f"[tarjeta ••••{digits[-4:]}]"
        return match.group()

    text = CARD.sub(luhn_card, text)
    return GROUPED_CARD.sub(lambda m: f"[tarjeta ••••{m.group()[-4:]}]", text)


def _mask_ibans(text: str) -> str:
    out, position = [], 0
    for match in IBAN_CANDIDATE.finditer(text):
        if match.start() < position:
            continue
        candidate = match.group()
        # A trailing all-letters word ("CODIGO", "SWIFT") is text after the IBAN, not part of it.
        tokens = candidate.split(" ")
        while len(tokens) > 1 and tokens[-1].isalpha():
            tokens.pop()
        candidate = " ".join(tokens)
        # Shrink from the right until the checksum holds, ending on a word boundary.
        found = None
        for end in range(len(candidate), 14, -1):
            piece = candidate[:end].rstrip()
            following = text[match.start() + len(piece) : match.start() + len(piece) + 1]
            if following.isalnum():
                continue
            compact = piece.replace(" ", "").upper()
            if iban_valid(compact):
                found = (piece, compact)
                break
        if found:
            piece, compact = found
            out.append(text[position : match.start()])
            out.append(f"[IBAN ••••{compact[-4:]}]")
            position = match.start() + len(piece)
    out.append(text[position:])
    return "".join(out)


def redact_payment_data(text: str | None) -> str | None:
    """Return text with payment cards, expiry, security codes and IBANs masked."""
    if not text:
        return text
    text = _mask_ibans(text)
    text = _mask_cards(text)
    text = EXPIRY_AFTER_CARD.sub(lambda m: f"{m.group(1)}{m.group(2)}[caducidad]", text)
    return SECURITY_CODE.sub(lambda m: f"{m.group(1)}{m.group(2)}[oculto]", text)
