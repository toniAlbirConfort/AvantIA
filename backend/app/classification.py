from __future__ import annotations

import re
import unicodedata
from email.utils import parseaddr


CATEGORIES = {
    "new_booking",
    "active_reservation",
    "booking_event",
    "status_update",
    "platform_notification",
    "internal_partner",
    "marketing",
    "unknown",
}
CLASSIFIER_VERSION = "rules-1"
VALID_QUERY_CATEGORIES = {"new_booking", "active_reservation"}

_QUOTE_STARTS = (
    re.compile(r"^\s*-{2,}\s*(?:original message|mensaje original|message d'origine)\s*-{2,}", re.I),
    re.compile(r"^\s*(?:on .{3,180}wrote:|el .{3,180}escribi[oó]:|le .{3,180}a écrit\s*:)", re.I),
    re.compile(r"^\s*(?:de|from|von|da):\s+.+@", re.I),
)
_BOOKING_WRAPPER = re.compile(
    r"(?:escribe tu respuesta sobre esta línea|write your reply above this line|"
    r"écrivez votre réponse au-dessus de cette ligne)",
    re.I,
)
_FOOTER_START = re.compile(
    r"^\s*(?:responder\s*--?>|reply\s*--?>|sent via booking\.com|"
    r"this email was sent by|unsubscribe|cancelar suscripción)\s*$",
    re.I,
)


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def latest_message_text(body: str | None) -> str:
    """Keep the guest's latest text, excluding common OTA wrapper and reply history."""
    text = (body or "").replace("\r\n", "\n").replace("\r", "\n")
    wrapper = _BOOKING_WRAPPER.search(text)
    if wrapper:
        text = text[wrapper.end() :]
    kept: list[str] = []
    for line in text.splitlines():
        if _FOOTER_START.match(line):
            break
        if any(pattern.match(line) for pattern in _QUOTE_STARTS):
            break
        if line.lstrip().startswith(">"):
            break
        kept.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()[:8000]


def detect_language(text: str) -> str:
    value = _fold(text)
    if re.search(r"\b(?:leilighet|overnatting|ledig|bestille|reservasjon|ankomst|datoer)\b", value):
        return "no"
    if re.search(r"\b(?:accommodatie|beschikbaar|reserveren|verblijf|aankomst)\b", value):
        return "nl"
    if re.search(r"\b(?:unterkunft|wohnung|verfugbar|verfugbarkeit|aufenthalt|konnen|buchung)\b", value):
        return "de"
    if re.search(r"\b(?:soggiorno|alloggio|vorrei|prenotare|disponibilita)\b", value):
        return "it"
    if re.search(r"\b(?:alojamento|estadia|gostaria|hospedes|disponibilidade|voces)\b", value):
        return "pt"
    if re.search(r"\b(?:bonjour|merci|disponibilite|logement|sejour|arrivee|pouvez-vous)\b", value):
        return "fr"
    if re.search(r"\b(?:the|we|you|your|available|booking|apartment|stay|arrival|price|please)\b", value):
        return "en"
    return "es"


def _sender_is_internal(sender: str | None, internal_addresses: list[str]) -> bool:
    sender_address = parseaddr(sender or "")[1].casefold().strip()
    if not sender_address or "@" not in sender_address:
        return False
    for address in internal_addresses:
        parsed = parseaddr(address or "")[1].casefold().strip()
        if parsed and sender_address == parsed:
            return True
    return False


def classify_email(
    *,
    subject: str | None,
    body: str | None,
    sender: str | None,
    internal_addresses: list[str] | None = None,
    prior_context: str = "",
) -> dict:
    """Conservative first-pass rules. Unclear content is held for a person."""
    current = latest_message_text(body)
    folded = _fold(current)
    subject_folded = _fold(subject or "")
    context = _fold(prior_context[:4000])
    searchable = f"{subject_folded}\n{folded}"
    language = detect_language(current or subject or "")
    has_guest_text = bool(
        current
        and len(current.split()) >= 5
        and (
            re.search(r"[?¿]", current)
            or re.search(
                r"\b(?:please|could|can|would|need|want|por favor|quiero|quisiera|podria|"
                r"me gustaria|souhaite|voudrais|pourriez|jeg vil|kan vi|kunnen|kann ich|"
                r"vorrei|potete|posso|poderia|gostaria)\b",
                folded,
            )
        )
    )

    def result(category: str, confidence: float, reason: str, signals: list[str]) -> dict:
        classification = {
            "classifier_version": CLASSIFIER_VERSION,
            "category": category,
            "confidence": confidence,
            "confidence_calibrated": False,
            "valid_query": category in VALID_QUERY_CATEGORIES,
            "reason": reason,
            "signals": signals[:5],
            "language": language,
            "response_mode": "simulation",
            "auto_send_eligible": False,
        }
        if category == "active_reservation":
            classification["reservation_verified"] = False
        return classification

    if not current and not subject_folded:
        return result("unknown", 0.25, "El mensaje no contiene texto clasificable.", [])

    if _sender_is_internal(sender, internal_addresses or []):
        return result("internal_partner", 0.97, "El remitente coincide con una dirección interna configurada.", ["dirección del remitente"])

    marketing_signals = [
        token for token in ("unsubscribe", "newsletter", "view in browser", "ofertas exclusivas", "promocion", "promoción")
        if token in _fold(current)
    ]
    if marketing_signals:
        return result("marketing", 0.94, "Contenido promocional o de suscripción detectado.", marketing_signals)

    booking_event_patterns = (
        (r"\b(?:new|nueva|nouvelle|ny|nieuwe)\s+(?:pre[ -]?)?(?:reservation|booking|reserva|reservasjon|reservering)\b", "aviso de nueva reserva"),
        (r"\b(?:reservation|booking|reserva)\s+(?:created|received|confirmada|confirmed|creada)\b", "evento de reserva"),
        (r"\b(?:a guest has booked|you have a new reservation|ha realizado una reserva)\b", "reserva creada en la plataforma"),
    )
    booking_event_hits = [label for pattern, label in booking_event_patterns if re.search(pattern, searchable)]
    if booking_event_hits and not has_guest_text:
        return result("booking_event", 0.93, "Notificación de una reserva para seguimiento interno; no se redacta una respuesta al remitente.", booking_event_hits)

    platform_patterns = (
        (r"\btienes?\s+\d+\s+mensajes?\s+sin\s+leer\b", "contador de mensajes sin leer"),
        (r"\b(?:informe|reporte)\s+de\s+(?:llegadas|reservas|ocupacion)\b", "informe operativo"),
        (r"\b(?:rating|valoracion|review)\s+(?:notification|notificacion|received)\b", "aviso de valoración"),
        (r"\b(?:reserva|booking)\s+(?:confirmada|confirmed|cancelada|cancelled)\b", "notificación de estado de reserva"),
        (r"\bentra en unified inbox\b", "aviso para abrir Unified Inbox"),
    )
    platform_hits = [label for pattern, label in platform_patterns if re.search(pattern, searchable)]
    if platform_hits and not has_guest_text:
        return result("platform_notification", 0.96, "El contenido coincide con una notificación operativa, sin pregunta del huésped.", platform_hits)

    status_patterns = (
        r"\b(?:thanks|thank you|gracias|merci|takk|bedankt)\b[!. ]*$",
        r"\b(?:not interested|no me interesa|no estamos interesados|pas cette fois|ikke interessert)\b",
        r"\b(?:ok|okay|perfecto|de acuerdo|d'accord)\b[!. ]*$",
        r"\b(?:we have received your message|hemos recibido su mensaje)\b",
    )
    if not has_guest_text and any(re.search(pattern, folded) for pattern in status_patterns):
        return result("status_update", 0.91, "Es un acuse, agradecimiento o cierre sin una nueva petición.", ["respuesta breve sin petición"])

    active_patterns = (
        r"\b(?:my|our|the existing|confirmed)\s+(?:reservation|booking|stay)\b",
        r"\b(?:i have|we have|i already)\s+(?:booked|reserved|a reservation)\b",
        r"\b(?:mi|nuestra|la)\s+(?:reserva|estancia)\b",
        r"\b(?:reserva|estancia)\s+(?:confirmada|existente)\b",
        r"\b(?:reservation|booking)\s+(?:number|reference|ref\.?|locator)\b",
        r"\b(?:num[eé]ro|referencia|localizador)\s+(?:de\s+)?reserva\b",
        r"\b(?:i am|we are)\s+(?:staying|arriving)\b",
        r"\b(?:ma|notre)\s+(?:reservation|sejour)\b",
        r"\b(?:meine|unsere)\s+(?:reservierung|buchung|reise)\b",
        r"\b(?:la mia|la nostra)\s+(?:prenotazione|pernottamento)\b",
        r"\b(?:mijn|onze)\s+(?:reservering|boeking|verblijf)\b",
        r"\b(?:min|vår)\s+(?:reservasjon|bestilling|opphold)\b",
        r"\b(?:minha|nossa)\s+(?:reserva|estadia)\b",
    )
    active_hits = [pattern for pattern in active_patterns if re.search(pattern, searchable)]
    prior_active = bool(re.search(r"\b(?:reservation|booking|reserva|estancia|check-in|check-out)\b", context))
    service_patterns = (
        r"\b(?:check[ -]?in|check[ -]?out|key|keys|keybox|door code|arrival|late arrival|"
        r"payment|paid|invoice|cancel|change dates|wifi|parking|entry|access)\b",
        r"\b(?:llegada|salida|llave|llaves|caja de llaves|codigo de acceso|pago|pagado|factura|"
        r"cancelar|cambiar fechas|wifi|aparcamiento|acceso)\b",
        r"\b(?:arrivee|depart|cle|paiement|facture|annuler|modifier|acces|parking)\b",
        r"\b(?:ankomst|nøkkel|betaling|avbestille|endre datoer|parkering)\b",
        r"\b(?:aankomst|sleutel|betaling|annuleren|data wijzigen|parkeren)\b",
        r"\b(?:ankunft|schlüssel|zahlung|rechnung|stornieren|ändern|zugang|parkplatz)\b",
        r"\b(?:arrivo|partenza|chiave|pagamento|fattura|cancellare|modificare|accesso|parcheggio)\b",
        r"\b(?:chegada|partida|chave|pagamento|fatura|cancelar|alterar|acesso|estacionamento)\b",
    )
    service_hits = [pattern for pattern in service_patterns if re.search(pattern, searchable)]
    has_question_or_request = bool(re.search(r"[?¿]", current) or re.search(
        r"\b(?:please|could|can|would|need|want|por favor|podria|puede|necesito|"
        r"quisiera|est-ce que|pouvez-vous|pourrait|kan|kunnen|jeg vil|kan vi|bitte|"
        r"konnen|kann ich|vorrei|potete|posso|poderia|preciso)\b",
        folded,
    ))
    active_issue = bool(re.search(
        r"\b(?:problem|issue|wrong|doesn.t work|cannot|can.t|no access|help|"
        r"charged twice|double charge|incorrect|refund|problem|no funciona|no puedo|error|falta|"
        r"cobro duplicado|cobrado dos veces|reembolso|no he recibido|pas marche|impossible|"
        r"fungerer ikke|virker ikke|werkt niet|probleem|storing|dubbel betaald|terugbetaling)\b",
        folded,
    ))
    if (active_hits or (prior_active and service_hits)) and (has_question_or_request or active_issue):
        signals = ["referencia a reserva existente"] if active_hits else ["contexto de reserva anterior"]
        if service_hits:
            signals.append("petición sobre la estancia")
        return result("active_reservation", 0.88 if active_hits else 0.78, "El mensaje parece referirse a una reserva previa; el estado activo no se ha verificado con los datos de reservas.", signals)

    new_booking_patterns = (
        r"\b(?:availability|available|vacan(?:cy|t)|rent|rental|book|booking|reserve|reservation|"
        r"price|rate|how much|apartment|accommodation|dates|stay)\b",
        r"\b(?:disponibilidad|disponible|alquilar|reservar|reserva|precio|tarifa|apartamento|"
        r"alojamiento|fechas|estancia|personas|huespedes)\b",
        r"\b(?:disponibilite|disponible|louer|reserver|reservation|prix|tarif|logement|"
        r"appartement|dates|sejour|personnes)\b",
        r"\b(?:ledig|bestille|reservasjon|pris|leilighet|overnatting|datoer|personer)\b",
        r"\b(?:beschikbaar|huren|reserveren|reservering|prijs|appartement|accommodatie|"
        r"datums|verblijf|personen)\b",
        r"\b(?:verfugbarkeit|verfugbar|mieten|buchen|buchung|preis|wohnung|unterkunft|"
        r"termine|aufenthalt|personen)\b",
        r"\b(?:disponibilita|disponibile|affittare|prenotare|prenotazione|prezzo|"
        r"appartamento|alloggio|date|soggiorno|persone)\b",
        r"\b(?:disponibilidade|disponivel|alugar|reservar|reserva|preco|apartamento|"
        r"alojamento|datas|estadia|hospedes|pessoas)\b",
    )
    new_hits = [pattern for pattern in new_booking_patterns if re.search(pattern, searchable)]
    date_signal = bool(re.search(r"\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b|\b(?:january|february|march|april|may|june|july|august|september|october|november|december|enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|diciembre|gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre|januar|februar|marz|april|mai|juni|juli|august|september|oktober|november|dezember|janeiro|fevereiro|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\b", searchable))
    if new_hits and (has_question_or_request or date_signal or len(current.split()) >= 12):
        signals = ["alojamiento, fechas o disponibilidad"]
        if date_signal:
            signals.append("posibles fechas de estancia")
        return result("new_booking", 0.86 if has_question_or_request else 0.74, "Solicitud sobre alojamiento, fechas o disponibilidad.", signals)

    if platform_hits:
        return result("platform_notification", 0.72, "Posible aviso de plataforma; revisar el mensaje original.", platform_hits)
    return result("unknown", 0.40, "No hay señales suficientes para confirmar una consulta; requiere revisión humana.", ["clasificación incierta"])
