"""Win-back email campaigns built on the "Clientes a recuperar" list.

A campaign keeps the retention filters the operator chose, freezes the audience those
filters give (who is included and why the rest is left out) and holds the message in
each language. Emails are laid out here, so the operator sees exactly what each guest
would receive. Nothing is sent from this module: delivery comes in a later phase.

Only guests linked to one customer card, with their own email address and consent to
receive offers (``email_opt_in``) are included. Addresses on the suppression list never
are, whatever Avantio says later.
"""

from __future__ import annotations

import html
import re
import secrets
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.customers import fold
from app.models import Customer, MailingCampaign, MailingRecipient, MailingSuppression
from app.retention import retention_view

LANGUAGES = ("es", "en", "fr", "de", "nl", "nb")
LANGUAGE_NAMES = {
    "es": "Español", "en": "Inglés", "fr": "Francés", "de": "Alemán", "nl": "Neerlandés",
    "nb": "Noruego",
}
FALLBACK_LANGUAGE = "en"
# Avantio exports the customer language as a name or a code; both are accepted.
LANGUAGE_ALIASES = {
    "es": ("es", "spa", "espanol", "castellano", "spanish", "espagnol", "spanisch", "spaans",
           "catalan", "catala", "valenciano", "valencia", "ca", "gl", "eu"),
    "en": ("en", "eng", "ingles", "english", "anglais", "englisch", "engels", "engelsk"),
    "fr": ("fr", "fra", "fre", "frances", "french", "francais", "franzosisch", "frans"),
    "de": ("de", "deu", "ger", "aleman", "german", "deutsch", "allemand", "duits", "tysk"),
    "nl": ("nl", "nld", "dut", "neerlandes", "holandes", "dutch", "nederlands", "flamenco",
           "vlaams", "flemish", "neerlandais"),
    "nb": ("nb", "no", "nn", "nor", "noruego", "norwegian", "norsk", "bokmal", "norvegien"),
}
_ALIAS = {alias: code for code, aliases in LANGUAGE_ALIASES.items() for alias in aliases}

# Booking, Airbnb, Expedia... give the guest a relay address: it is not a marketing contact.
OTA_DOMAINS = (
    "booking.com", "airbnb.com", "airbnb.es", "expedia.com", "expediapartnercentral.com",
    "hotelbeds.com", "vrbo.com", "homeaway.com", "holidu.com", "tripadvisor.com",
)
EXCLUSION_REASONS = {
    "no_match": "Sin ficha de cliente",
    "ambiguous": "Varias fichas con ese nombre",
    "no_email": "Sin email",
    "ota_email": "Email de portal (Booking, Airbnb…)",
    "no_opt_in": "No acepta campañas",
    "suppressed": "Se dio de baja",
    "duplicate_email": "Email repetido en la campaña",
    "manual": "Excluido por el operador",
}
VARIABLES = {
    "nombre": "Nombre del huésped (vacío si no consta)",
    "alojamiento": "Alojamiento habitual",
    "ultima_estancia": "Mes y año de la última estancia",
    "estancias": "Número de estancias",
    "codigo": "Código de la oferta",
    "valida_hasta": "Fecha límite de la oferta",
}
FILTER_KEYS = (
    "target_year", "base_years", "min_stays", "channel", "contact", "q", "month_from",
    "month_to",
)
CONTENT_FIELDS = ("subject", "preheader", "headline", "body", "offer", "cta_label")
CONTENT_LIMITS = {
    "subject": 150, "preheader": 200, "headline": 120, "body": 5000, "offer": 200,
    "cta_label": 60,
}

MONTHS = {
    "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
           "septiembre", "octubre", "noviembre", "diciembre"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"],
    "fr": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
           "septembre", "octobre", "novembre", "décembre"],
    "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
           "September", "Oktober", "November", "Dezember"],
    "nl": ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus",
           "september", "oktober", "november", "december"],
    "nb": ["januar", "februar", "mars", "april", "mai", "juni", "juli", "august",
           "september", "oktober", "november", "desember"],
}
MONTH_YEAR = {
    "es": "{month} de {year}", "en": "{month} {year}", "fr": "{month} {year}",
    "de": "{month} {year}", "nl": "{month} {year}", "nb": "{month} {year}",
}
FULL_DATE = {
    "es": "{day} de {month} de {year}", "en": "{day} {month} {year}",
    "fr": "{day} {month} {year}", "de": "{day}. {month} {year}", "nl": "{day} {month} {year}",
    "nb": "{day}. {month} {year}",
}
# Fixed pieces of the layout; the operator edits the message, not these.
CHROME = {
    "es": {
        "greeting": ("Hola, {name}:", "Hola:"),
        "property": "nuestros apartamentos",
        "offer_title": "Tu ventaja exclusiva",
        "code": "Código",
        "valid_until": "Válido para reservas hechas hasta el {date}.",
        "signature": "Un saludo,\nEl equipo de {company}",
        "reason": "Recibes este correo porque te alojaste con nosotros y aceptaste recibir "
                  "nuestras ofertas.",
        "unsubscribe": "Darme de baja",
        "unsubscribe_question": "¿Quieres dejar de recibir nuestras ofertas por email?",
        "unsubscribe_button": "Confirmar la baja",
        "unsubscribe_done": "Listo. No volverás a recibir ofertas por email de {company}.",
        "invalid": "Este enlace no es válido o ha caducado.",
    },
    "en": {
        "greeting": ("Dear {name},", "Hello,"),
        "property": "our apartments",
        "offer_title": "Your exclusive offer",
        "code": "Code",
        "valid_until": "Valid for bookings made until {date}.",
        "signature": "Kind regards,\nThe {company} team",
        "reason": "You are receiving this email because you stayed with us and agreed to "
                  "receive our offers.",
        "unsubscribe": "Unsubscribe",
        "unsubscribe_question": "Would you like to stop receiving our offers by email?",
        "unsubscribe_button": "Confirm unsubscribe",
        "unsubscribe_done": "Done. You will no longer receive offers by email from {company}.",
        "invalid": "This link is not valid or has expired.",
    },
    "fr": {
        "greeting": ("Bonjour {name},", "Bonjour,"),
        "property": "nos appartements",
        "offer_title": "Votre avantage exclusif",
        "code": "Code",
        "valid_until": "Valable pour les réservations effectuées jusqu’au {date}.",
        "signature": "Bien cordialement,\nL’équipe {company}",
        "reason": "Vous recevez cet e-mail car vous avez séjourné chez nous et accepté de "
                  "recevoir nos offres.",
        "unsubscribe": "Se désabonner",
        "unsubscribe_question": "Vous ne souhaitez plus recevoir nos offres par e-mail ?",
        "unsubscribe_button": "Confirmer le désabonnement",
        "unsubscribe_done": "C’est fait. Vous ne recevrez plus nos offres par e-mail "
                            "({company}).",
        "invalid": "Ce lien n’est pas valide ou a expiré.",
    },
    "de": {
        "greeting": ("Hallo {name},", "Guten Tag,"),
        "property": "unseren Apartments",
        "offer_title": "Ihr exklusiver Vorteil",
        "code": "Code",
        "valid_until": "Gültig für Buchungen bis zum {date}.",
        "signature": "Herzliche Grüße\nIhr Team von {company}",
        "reason": "Sie erhalten diese E-Mail, weil Sie bei uns zu Gast waren und dem Erhalt "
                  "unserer Angebote zugestimmt haben.",
        "unsubscribe": "Abmelden",
        "unsubscribe_question": "Möchten Sie unsere Angebote nicht mehr per E-Mail erhalten?",
        "unsubscribe_button": "Abmeldung bestätigen",
        "unsubscribe_done": "Erledigt. Sie erhalten von {company} keine Angebote mehr per "
                            "E-Mail.",
        "invalid": "Dieser Link ist ungültig oder abgelaufen.",
    },
    "nl": {
        "greeting": ("Beste {name},", "Beste gast,"),
        "property": "onze appartementen",
        "offer_title": "Uw exclusieve voordeel",
        "code": "Code",
        "valid_until": "Geldig voor boekingen tot en met {date}.",
        "signature": "Met vriendelijke groet,\nHet team van {company}",
        "reason": "U ontvangt deze e-mail omdat u bij ons heeft verbleven en heeft ingestemd "
                  "met het ontvangen van onze aanbiedingen.",
        "unsubscribe": "Afmelden",
        "unsubscribe_question": "Wilt u onze aanbiedingen niet meer per e-mail ontvangen?",
        "unsubscribe_button": "Afmelding bevestigen",
        "unsubscribe_done": "Gelukt. U ontvangt geen aanbiedingen meer per e-mail van "
                            "{company}.",
        "invalid": "Deze link is ongeldig of verlopen.",
    },
    "nb": {
        "greeting": ("Hei {name},", "Hei,"),
        "property": "leilighetene våre",
        "offer_title": "Din eksklusive fordel",
        "code": "Kode",
        "valid_until": "Gjelder for bestillinger gjort innen {date}.",
        "signature": "Vennlig hilsen\n{company}",
        "reason": "Du får denne e-posten fordi du har bodd hos oss og har samtykket til å "
                  "motta tilbudene våre.",
        "unsubscribe": "Meld deg av",
        "unsubscribe_question": "Vil du slutte å motta tilbudene våre på e-post?",
        "unsubscribe_button": "Bekreft avmelding",
        "unsubscribe_done": "Ferdig. Du får ikke lenger tilbud på e-post fra {company}.",
        "invalid": "Denne lenken er ugyldig eller utløpt.",
    },
}
DEFAULT_CONTENT = {
    "es": {
        "subject": "Albir te espera de nuevo, con una ventaja solo para ti",
        "preheader": "Reserva antes del {valida_hasta} con tu código {codigo}.",
        "headline": "Vuelve a Albir con ventaja",
        "body": "Nos encantó recibirte en {alojamiento} en {ultima_estancia} y nos haría "
                "mucha ilusión volver a verte esta temporada.\n\n"
                "Por eso te hemos preparado una ventaja exclusiva para tu próxima estancia, "
                "solo para quienes ya nos conocen y reservan directamente con nosotros.\n\n"
                "Si quieres que te ayudemos a encontrar fechas o apartamento, responde a este "
                "correo y te atenderemos personalmente.",
        "offer": "10 % de descuento en tu próxima estancia",
        "cta_label": "Ver disponibilidad",
    },
    "en": {
        "subject": "Albir is waiting for you, with a special offer just for you",
        "preheader": "Book before {valida_hasta} with your code {codigo}.",
        "headline": "Come back to Albir",
        "body": "We loved having you at {alojamiento} in {ultima_estancia}, and it would be "
                "wonderful to welcome you back this season.\n\n"
                "That is why we have prepared an exclusive benefit for your next stay, only "
                "for guests who already know us and book directly with us.\n\n"
                "If you would like help finding dates or an apartment, simply reply to this "
                "email and we will look after you personally.",
        "offer": "10% off your next stay",
        "cta_label": "Check availability",
    },
    "fr": {
        "subject": "Albir vous attend, avec un avantage rien que pour vous",
        "preheader": "Réservez avant le {valida_hasta} avec votre code {codigo}.",
        "headline": "Revenez à Albir",
        "body": "Nous avons été ravis de vous accueillir à {alojamiento} en {ultima_estancia} "
                "et serions heureux de vous recevoir à nouveau cette saison.\n\n"
                "C’est pourquoi nous vous avons réservé un avantage exclusif pour votre "
                "prochain séjour, destiné aux clients qui nous connaissent déjà et réservent "
                "directement auprès de nous.\n\n"
                "Si vous souhaitez de l’aide pour choisir vos dates ou votre appartement, "
                "répondez simplement à cet e-mail : nous nous occuperons de vous "
                "personnellement.",
        "offer": "10 % de réduction sur votre prochain séjour",
        "cta_label": "Voir les disponibilités",
    },
    "de": {
        "subject": "Albir wartet auf Sie – mit einem Vorteil nur für Sie",
        "preheader": "Buchen Sie bis zum {valida_hasta} mit Ihrem Code {codigo}.",
        "headline": "Kommen Sie zurück nach Albir",
        "body": "Es war uns eine Freude, Sie im {ultima_estancia} in {alojamiento} zu Gast "
                "zu haben, und wir würden Sie gerne in dieser Saison wieder begrüßen.\n\n"
                "Deshalb haben wir für Ihren nächsten Aufenthalt einen exklusiven Vorteil "
                "vorbereitet – nur für Gäste, die uns bereits kennen und direkt bei uns "
                "buchen.\n\n"
                "Wenn Sie Hilfe bei der Wahl von Reisedaten oder Apartment wünschen, "
                "antworten Sie einfach auf diese E-Mail. Wir kümmern uns persönlich um Sie.",
        "offer": "10 % Rabatt auf Ihren nächsten Aufenthalt",
        "cta_label": "Verfügbarkeit prüfen",
    },
    "nl": {
        "subject": "Albir wacht op u, met een voordeel speciaal voor u",
        "preheader": "Boek vóór {valida_hasta} met uw code {codigo}.",
        "headline": "Kom terug naar Albir",
        "body": "Wat fijn dat u in {ultima_estancia} bij ons verbleef in {alojamiento}. We "
                "zouden u graag dit seizoen opnieuw verwelkomen.\n\n"
                "Daarom hebben we een exclusief voordeel voor uw volgende verblijf "
                "klaargezet, alleen voor gasten die ons al kennen en rechtstreeks bij ons "
                "boeken.\n\n"
                "Wilt u hulp bij het kiezen van data of een appartement? Beantwoord dan "
                "gewoon deze e-mail; we helpen u graag persoonlijk.",
        "offer": "10% korting op uw volgende verblijf",
        "cta_label": "Beschikbaarheid bekijken",
    },
    "nb": {
        "subject": "Albir venter på deg – med en fordel bare for deg",
        "preheader": "Bestill innen {valida_hasta} med koden {codigo}.",
        "headline": "Kom tilbake til Albir",
        "body": "Det var en glede å ha deg hos oss i {alojamiento} i {ultima_estancia}, og vi "
                "vil gjerne ønske deg velkommen tilbake denne sesongen.\n\n"
                "Derfor har vi en eksklusiv fordel til ditt neste opphold, bare for gjester "
                "som allerede kjenner oss og bestiller direkte hos oss.\n\n"
                "Vil du ha hjelp til å finne datoer eller leilighet? Svar på denne e-posten, "
                "så hjelper vi deg personlig.",
        "offer": "10 % rabatt på ditt neste opphold",
        "cta_label": "Se ledighet",
    },
}
# A plausible guest so the operator can preview a language nobody in the audience speaks.
SAMPLE_GUEST = {
    "first_name": "Ana", "property_name": "Apartamento de ejemplo",
    "last_stay_start": "2025-08-01", "stays": 2,
}
VARIABLE_RE = re.compile(r"\{([a-z_]+)\}")
BANNER = Path(__file__).parent / "assets" / "auto_reply_banner.png"


class CampaignError(ValueError):
    pass


def normalize_language(value: str | None) -> str | None:
    """The template language for an Avantio language value, or None when it is unknown."""
    if not value:
        return None
    folded = fold(value).replace("å", "a")
    folded = re.split(r"[-_ (/]", folded, maxsplit=1)[0].strip(".")
    return _ALIAS.get(folded)


def company_name() -> str:
    return get_settings().smtp_from_name.strip() or "Albir Confort Holidays"


def sender() -> str:
    address = get_settings().smtp_from.strip()
    return f"{company_name()} <{address}>" if address else company_name()


def site_url() -> str:
    return get_settings().auto_reply_banner_url.strip()


def public_base() -> str:
    settings = get_settings()
    return (settings.public_base_url.strip() or settings.cors_origin.strip()).rstrip("/")


def unsubscribe_url(token: str) -> str:
    return f"{public_base()}/api/public/campaigns/unsubscribe/{token}"


def default_offer(target_year: int) -> dict:
    return {
        "code": f"VUELVE{target_year}",
        "valid_until": (date.today() + timedelta(days=45)).isoformat(),
        "cta_url": site_url(),
    }


def default_name(filters: dict) -> str:
    target = filters.get("target_year") or date.today().year
    return f"Recuperación {target}"


def is_ota_email(email: str) -> bool:
    domain = email.rsplit("@", 1)[-1].lower()
    return any(domain == item or domain.endswith(f".{item}") for item in OTA_DOMAINS)


def view_filters(filters: dict) -> dict:
    return {key: filters[key] for key in FILTER_KEYS if filters.get(key) not in (None, "", [])}


def build_audience(db: Session, campaign: MailingCampaign) -> None:
    """Freeze who the campaign goes to, keeping the operator's own exclusions and tokens."""
    result = retention_view(db, **view_filters(campaign.filters), offset=0, limit=1_000_000)
    previous = {
        row.guest_key: row
        for row in db.scalars(
            select(MailingRecipient).where(MailingRecipient.campaign_id == campaign.id)
        )
    }
    suppressed = set(db.scalars(select(MailingSuppression.email)))
    linked = [item["avantio_id"] for item in result["items"] if item.get("avantio_id")]
    first_names = dict(
        db.execute(
            select(Customer.avantio_id, Customer.first_name).where(
                Customer.avantio_id.in_(linked)
            )
        ).all()
    ) if linked else {}
    db.execute(delete(MailingRecipient).where(MailingRecipient.campaign_id == campaign.id))
    db.flush()
    seen: set[str] = set()
    for item in result["items"]:
        email = (item.get("email") or "").strip().lower() or None
        old = previous.get(item["key"])
        if item["match"] == "none":
            reason = "no_match"
        elif item["match"] == "ambiguous":
            reason = "ambiguous"
        elif not email:
            reason = "no_email"
        elif is_ota_email(email):
            reason = "ota_email"
        elif not item.get("email_opt_in"):
            reason = "no_opt_in"
        elif email in suppressed:
            reason = "suppressed"
        elif email in seen:
            reason = "duplicate_email"
        elif old is not None and old.exclusion_reason == "manual":
            reason = "manual"
        else:
            reason = None
        if reason is None:
            seen.add(email)
        language = normalize_language(item.get("language"))
        first = first_names.get(item.get("avantio_id")) if item["match"] == "exact" else None
        db.add(MailingRecipient(
            campaign_id=campaign.id,
            guest_key=item["key"],
            display_name=item["display_name"],
            first_name=(first or "").strip() or None,
            avantio_id=item.get("avantio_id") if item["match"] == "exact" else None,
            email=email,
            language=language or FALLBACK_LANGUAGE,
            language_raw=(item.get("language") or None),
            data={
                "years": item["years"],
                "stays": item["stays"],
                "nights": item["nights"],
                "total": str(item["total"]),
                "last_stay_start": item["last_stay_start"].isoformat(),
                "last_stay_end": item["last_stay_end"].isoformat(),
                "property_name": item.get("property_name"),
                "channel": item.get("channel"),
                "country": item.get("country"),
                "language_known": language is not None,
            },
            status="included" if reason is None else "excluded",
            exclusion_reason=reason,
            token=old.token if old is not None else secrets.token_urlsafe(24),
        ))
    campaign.audience_built_at = datetime.now(UTC)
    campaign.updated_at = campaign.audience_built_at


def audience_summary(db: Session, campaign: MailingCampaign) -> dict:
    rows = db.execute(
        select(
            MailingRecipient.status, MailingRecipient.exclusion_reason,
            MailingRecipient.language, MailingRecipient.data,
        ).where(MailingRecipient.campaign_id == campaign.id)
    ).all()
    content = campaign.content or {}
    reasons = {key: 0 for key in EXCLUSION_REASONS}
    languages: dict[str, int] = {}
    included = 0
    # Guests whose own language has no text (or is unknown) and get the English version.
    fallback = 0
    for status, reason, language, data in rows:
        if status == "included":
            included += 1
            languages[language] = languages.get(language, 0) + 1
            if language not in content or not (data or {}).get("language_known", True):
                fallback += 1
        elif reason in reasons:
            reasons[reason] += 1
    return {
        "candidates": len(rows),
        "included": included,
        "excluded": len(rows) - included,
        "fallback": fallback,
        "reasons": [
            {"reason": key, "label": label, "count": reasons[key]}
            for key, label in EXCLUSION_REASONS.items() if reasons[key]
        ],
        "languages": [
            {
                "language": code, "label": LANGUAGE_NAMES.get(code, code), "count": count,
                # Without a text of its own, the guest gets the English version.
                "sent_as": code if code in content else FALLBACK_LANGUAGE,
            }
            for code, count in sorted(languages.items(), key=lambda item: -item[1])
        ],
    }


def validate_content(content: dict) -> dict:
    clean: dict[str, dict] = {}
    for language, fields in content.items():
        if language not in LANGUAGES:
            raise CampaignError(f"Idioma no admitido: {language}")
        if not isinstance(fields, dict):
            raise CampaignError("Contenido no válido")
        entry = {}
        for field in CONTENT_FIELDS:
            value = str(fields.get(field) or "").replace("\r\n", "\n").strip()
            if len(value) > CONTENT_LIMITS[field]:
                raise CampaignError(
                    f"{LANGUAGE_NAMES[language]}: el campo {field} supera "
                    f"{CONTENT_LIMITS[field]} caracteres"
                )
            entry[field] = value
        clean[language] = entry
    if FALLBACK_LANGUAGE not in clean:
        raise CampaignError("La versión en inglés es obligatoria: es la que reciben los demás")
    return clean


def validate_offer(offer: dict) -> dict:
    code = str(offer.get("code") or "").strip().upper()
    if len(code) > 40 or not re.fullmatch(r"[A-Z0-9_-]*", code):
        raise CampaignError("El código solo admite letras, números y guiones (40 como máximo)")
    valid_until = str(offer.get("valid_until") or "").strip()
    if valid_until:
        try:
            date.fromisoformat(valid_until)
        except ValueError as exc:
            raise CampaignError("Fecha límite no válida") from exc
    url = str(offer.get("cta_url") or "").strip()
    if url:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.netloc:
            raise CampaignError("El enlace del botón debe empezar por https://")
    return {"code": code, "valid_until": valid_until, "cta_url": url}


def _month_year(language: str, value: str | None) -> str:
    if not value:
        return ""
    day = date.fromisoformat(value)
    return MONTH_YEAR[language].format(month=MONTHS[language][day.month - 1], year=day.year)


def _full_date(language: str, value: str | None) -> str:
    if not value:
        return ""
    day = date.fromisoformat(value)
    return FULL_DATE[language].format(
        day=day.day, month=MONTHS[language][day.month - 1], year=day.year
    )


def _values(campaign: MailingCampaign, language: str, guest: dict) -> dict[str, str]:
    offer = campaign.offer or {}
    return {
        "nombre": guest.get("first_name") or "",
        "alojamiento": guest.get("property_name") or CHROME[language]["property"],
        "ultima_estancia": _month_year(language, guest.get("last_stay_start")),
        "estancias": str(guest.get("stays") or ""),
        "codigo": offer.get("code") or "",
        "valida_hasta": _full_date(language, offer.get("valid_until")),
    }


def _fill(template: str, values: dict[str, str], unknown: set[str]) -> str:
    def replace(match: re.Match) -> str:
        name = match.group(1)
        if name not in values:
            unknown.add(name)
            return match.group(0)
        return values[name]

    return VARIABLE_RE.sub(replace, template)


@lru_cache
def _banner_data_uri() -> str | None:
    import base64

    try:
        return "data:image/png;base64," + base64.b64encode(BANNER.read_bytes()).decode()
    except OSError:
        return None


def _paragraphs(text: str, style: str) -> str:
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    return "".join(
        f'<p style="{style}">{html.escape(block).replace(chr(10), "<br>")}</p>' for block in blocks
    )


FONT = "font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;"
INK = "#1C1E1F"
MUTED = "#5B6066"
ACCENT = "#B8391F"  # Brand orange darkened so white text on it stays readable (AA).


def _layout(language: str, parts: dict, unsubscribe: str) -> str:
    chrome = CHROME[language]
    company = html.escape(company_name())
    site = site_url()
    banner = _banner_data_uri()
    header = ""
    if banner:
        image = (
            f'<img src="{banner}" width="560" alt="{company}" '
            'style="display:block;width:100%;max-width:560px;height:auto;border:0">'
        )
        if site:
            image = f'<a href="{html.escape(site)}" target="_blank">{image}</a>'
        header = f'<tr><td style="padding:20px 20px 4px">{image}</td></tr>'
    else:
        header = (
            f'<tr><td style="padding:24px 32px 0;{FONT}font-size:18px;font-weight:bold;'
            f'color:{INK}">{company}</td></tr>'
        )
    paragraph = f"margin:0 0 16px;{FONT}font-size:16px;line-height:1.6;color:{INK}"
    offer_rows = ""
    if parts["offer"] or parts["code"]:
        code = ""
        if parts["code"]:
            code = (
                f'<p style="margin:14px 0 0;{FONT}font-size:13px;color:{MUTED}">'
                f'{html.escape(chrome["code"])}</p>'
                f'<p style="margin:4px 0 0"><span style="display:inline-block;padding:8px 14px;'
                f'border:2px dashed {ACCENT};border-radius:6px;font-family:Consolas,Menlo,'
                f'monospace;font-size:20px;font-weight:bold;letter-spacing:2px;color:{INK};'
                f'background:#FFFFFF">{html.escape(parts["code"])}</span></p>'
            )
        deadline = ""
        if parts["valid_until"]:
            deadline = (
                f'<p style="margin:12px 0 0;{FONT}font-size:13px;line-height:1.5;color:{MUTED}">'
                f'{html.escape(chrome["valid_until"].format(date=parts["valid_until"]))}</p>'
            )
        offer_rows = (
            '<tr><td style="padding:8px 32px 8px">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="background:#FFF4EF;border-left:4px solid {ACCENT};border-radius:6px">'
            '<tr><td style="padding:18px 20px">'
            f'<p style="margin:0;{FONT}font-size:12px;font-weight:bold;letter-spacing:1px;'
            f'text-transform:uppercase;color:{ACCENT}">{html.escape(chrome["offer_title"])}</p>'
            f'<p style="margin:6px 0 0;{FONT}font-size:20px;font-weight:bold;line-height:1.35;'
            f'color:{INK}">{html.escape(parts["offer"])}</p>'
            f"{code}{deadline}</td></tr></table></td></tr>"
        )
    button = ""
    if parts["cta_label"] and parts["cta_url"]:
        button = (
            '<tr><td align="center" style="padding:20px 32px 8px">'
            f'<a href="{html.escape(parts["cta_url"])}" target="_blank" '
            f'style="display:inline-block;padding:14px 28px;background:{ACCENT};'
            f'border-radius:6px;{FONT}font-size:16px;font-weight:bold;color:#FFFFFF;'
            f'text-decoration:none">{html.escape(parts["cta_label"])}</a></td></tr>'
        )
    signature = html.escape(chrome["signature"].format(company=company_name()))
    site_line = ""
    if site:
        shown = re.sub(r"^https?://", "", site).strip("/")
        site_line = (
            f' · <a href="{html.escape(site)}" target="_blank" style="color:{MUTED}">'
            f"{html.escape(shown)}</a>"
        )
    return (
        "<!doctype html>"
        f'<html lang="{language}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(parts['subject'])}</title></head>"
        '<body style="margin:0;padding:0;background:#F3EFEA">'
        '<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent">'
        f"{html.escape(parts['preheader'])}</div>"
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#F3EFEA"><tr><td align="center" style="padding:24px 12px">'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
        'style="width:100%;max-width:600px;background:#FFFFFF;border-radius:10px;'
        'overflow:hidden">'
        f"{header}"
        f'<tr><td style="padding:24px 32px 4px"><h1 style="margin:0;{FONT}font-size:26px;'
        f'line-height:1.25;font-weight:bold;color:{INK}">{html.escape(parts["headline"])}'
        "</h1></td></tr>"
        f'<tr><td style="padding:18px 32px 0">'
        f'<p style="{paragraph}">{html.escape(parts["greeting"])}</p>'
        f'{_paragraphs(parts["body"], paragraph)}</td></tr>'
        f"{offer_rows}{button}"
        f'<tr><td style="padding:24px 32px 32px"><p style="margin:0;{FONT}font-size:16px;'
        f'line-height:1.6;color:{INK}">{signature.replace(chr(10), "<br>")}</p></td></tr>'
        "</table>"
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
        'style="width:100%;max-width:600px"><tr><td style="padding:18px 32px;'
        f'{FONT}font-size:12px;line-height:1.6;color:{MUTED};text-align:center">'
        f'{html.escape(chrome["reason"])}<br>'
        f'<a href="{html.escape(unsubscribe)}" target="_blank" style="color:{MUTED}">'
        f'{html.escape(chrome["unsubscribe"])}</a>{site_line}<br>{company}'
        "</td></tr></table></td></tr></table></body></html>"
    )


def _text(language: str, parts: dict, unsubscribe: str) -> str:
    chrome = CHROME[language]
    lines = [parts["greeting"], "", parts["body"], ""]
    if parts["offer"]:
        lines += [f"{chrome['offer_title']}: {parts['offer']}"]
    if parts["code"]:
        lines += [f"{chrome['code']}: {parts['code']}"]
    if parts["valid_until"]:
        lines += [chrome["valid_until"].format(date=parts["valid_until"])]
    if parts["cta_label"] and parts["cta_url"]:
        lines += ["", f"{parts['cta_label']}: {parts['cta_url']}"]
    lines += ["", chrome["signature"].format(company=company_name()), "", "--",
              chrome["reason"], f"{chrome['unsubscribe']}: {unsubscribe}"]
    return "\n".join(lines).strip() + "\n"


def render(
    campaign: MailingCampaign, *, language: str, recipient: MailingRecipient | None = None
) -> dict:
    """The email as the guest would get it: subject, preheader, HTML and plain text."""
    content = campaign.content or {}
    if recipient is not None:
        wanted = recipient.language
        guest = {"first_name": recipient.first_name, **(recipient.data or {})}
        token = recipient.token
    else:
        wanted = language
        guest = dict(SAMPLE_GUEST)
        token = "ejemplo"
    used = wanted if wanted in content else FALLBACK_LANGUAGE
    if used not in content:
        raise CampaignError("La campaña no tiene texto en inglés")
    fields = content[used]
    values = _values(campaign, used, guest)
    unknown: set[str] = set()
    with_name, without_name = CHROME[used]["greeting"]
    parts = {
        field: _fill(fields.get(field, ""), values, unknown) for field in CONTENT_FIELDS
    }
    parts.update(
        greeting=with_name.format(name=values["nombre"]) if values["nombre"] else without_name,
        code=values["codigo"],
        valid_until=values["valida_hasta"],
        cta_url=(campaign.offer or {}).get("cta_url") or "",
    )
    unsubscribe = unsubscribe_url(token)
    warnings = []
    if unknown:
        warnings.append(
            "Variables desconocidas: " + ", ".join(f"{{{name}}}" for name in sorted(unknown))
        )
    if not parts["subject"]:
        warnings.append("Falta el asunto")
    if not parts["body"]:
        warnings.append("Falta el texto del mensaje")
    if parts["cta_label"] and not parts["cta_url"]:
        warnings.append("El botón no se muestra: falta el enlace")
    if "{codigo}" in "".join(fields.values()) and not values["codigo"]:
        warnings.append("El texto usa {codigo} pero la oferta no tiene código")
    if "{valida_hasta}" in "".join(fields.values()) and not values["valida_hasta"]:
        warnings.append("El texto usa {valida_hasta} pero la oferta no tiene fecha límite")
    if recipient is not None and wanted != used:
        warnings.append(
            f"Sin texto en {LANGUAGE_NAMES.get(wanted, wanted).lower()}: recibe la versión "
            "en inglés"
        )
    return {
        "language": used,
        "requested_language": wanted,
        "sample": recipient is None,
        "from": sender(),
        "to": recipient.email if recipient is not None else None,
        "subject": parts["subject"],
        "preheader": parts["preheader"],
        "html": _layout(used, parts, unsubscribe),
        "text": _text(used, parts, unsubscribe),
        "unsubscribe_url": unsubscribe,
        "warnings": warnings,
    }


def unsubscribe_page(language: str, *, state: str, token: str = "") -> str:
    """A small standalone page in the guest's language: confirm, done or invalid link."""
    chrome = CHROME.get(language, CHROME[FALLBACK_LANGUAGE])
    company = html.escape(company_name())
    if state == "confirm":
        body = (
            f'<p style="margin:0 0 20px">{html.escape(chrome["unsubscribe_question"])}</p>'
            f'<form method="post" action="{html.escape(unsubscribe_url(token))}">'
            f'<button type="submit" style="padding:12px 22px;border:0;border-radius:6px;'
            f'background:{ACCENT};color:#FFFFFF;font-size:16px;font-weight:bold;cursor:pointer">'
            f'{html.escape(chrome["unsubscribe_button"])}</button></form>'
        )
    elif state == "done":
        done = chrome["unsubscribe_done"].format(company=company_name())
        body = f'<p style="margin:0">{html.escape(done)}</p>'
    else:
        body = f'<p style="margin:0">{html.escape(chrome["invalid"])}</p>'
    return (
        f'<!doctype html><html lang="{language}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex">'
        f"<title>{company}</title></head>"
        f'<body style="margin:0;background:#F3EFEA;{FONT}color:{INK}">'
        '<main style="max-width:520px;margin:48px auto;padding:32px;background:#FFFFFF;'
        'border-radius:10px;font-size:16px;line-height:1.6">'
        f'<p style="margin:0 0 16px;font-size:18px;font-weight:bold">{company}</p>'
        f"{body}</main></body></html>"
    )


def unsubscribe(db: Session, token: str) -> MailingRecipient | None:
    recipient = db.scalar(select(MailingRecipient).where(MailingRecipient.token == token))
    if recipient is None or not recipient.email:
        return None
    if db.scalar(
        select(MailingSuppression.id).where(MailingSuppression.email == recipient.email)
    ) is None:
        db.add(MailingSuppression(
            email=recipient.email, reason="unsubscribe", campaign_id=recipient.campaign_id
        ))
    return recipient
