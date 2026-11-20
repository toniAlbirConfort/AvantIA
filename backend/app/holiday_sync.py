"""Synchronize public holidays for the business scope from official sources.

Scope: Spain, Comunitat Valenciana, l'Alfàs del Pi (Albir). See ``app.business_scope``.

Sources, all official and read-only:

* BOE: the annual resolution of the Dirección General de Trabajo with the national and
  regional holidays of the following year. It is found through the public BOE open
  data API (daily summaries) and read from its XML, using the Comunitat Valenciana
  column of the annex table.
* Ayuntamiento de l'Alfàs del Pi: the municipal calendar of non-working days for the
  current year, which labels each day as national, regional or local.
* DOGV: the annual resolution with the local holidays of every Valencian municipality.
  DOGV has no public search API, so the PDFs of the resolutions are registered in
  ``DOGV_LOCAL_RESOLUTIONS`` and read automatically.

Each source that fails or is not yet published leaves the stored rows of that year
untouched and is reported in the synchronization state.
"""

from __future__ import annotations

import io
import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from html import unescape

import httpx
from sqlalchemy import delete, extract, select
from sqlalchemy.orm import Session

from app.business_scope import AUTONOMOUS_COMMUNITY, MUNICIPALITY
from app.models import AppSetting, TariffHoliday

logger = logging.getLogger(__name__)

SYNC_STATE_KEY = "tariff_holidays_sync"
SYNC_INTERVAL = timedelta(hours=24)
HISTORY_YEARS = 4
USER_AGENT = "AvantIA-festivos/1.0"
LOCAL_HOLIDAY_NAME = f"Fiestas locales de {MUNICIPALITY}"

BOE_SUMMARY_URL = "https://www.boe.es/datosabiertos/api/boe/sumario/{day:%Y%m%d}"
BOE_XML_URL = "https://www.boe.es/diario_boe/xml.php?id={identifier}"
BOE_HTML_URL = "https://www.boe.es/diario_boe/txt.php?id={identifier}"
# Verified identifiers; any other year is discovered through the BOE summaries.
BOE_KNOWN_RESOLUTIONS = {
    2022: "BOE-A-2021-17113",
    2023: "BOE-A-2022-16755",
    2024: "BOE-A-2023-22014",
    2025: "BOE-A-2024-21316",
    2026: "BOE-A-2025-21667",
    2027: "BOE-A-2026-21254",
}

TOWN_HALL_URL = (
    "https://ciudadano.lalfas.es/PortalCiudadania/info/inicio.do"
    "?formAction=btDiasInhabiles&target=print"
)

# Annual DOGV resolutions approving the local holidays of the Comunitat Valenciana.
# Add the new PDF here when the DOGV publishes the following year's resolution.
DOGV_LOCAL_RESOLUTIONS = {
    2025: ("DOGV-C-2024-11987", "https://dogv.gva.es/datos/2024/11/18/pdf/2024_11987_es.pdf"),
    2026: ("DOGV-C-2025-46326", "https://dogv.gva.es/datos/2025/11/14/pdf/2025_46326_es.pdf"),
}

MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}
SCOPE_LABELS = {"national": "nacional", "regional": "autonómico", "municipal": "local"}


class HolidaySourceError(RuntimeError):
    """An official source could not be read or did not have the expected format."""


@dataclass(frozen=True)
class OfficialHoliday:
    holiday_date: date
    name: str
    scope: str
    source: str
    source_reference: str | None
    source_url: str


Fetch = Callable[[str, dict | None], httpx.Response]


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return "".join(character for character in normalized if not unicodedata.combining(character))


def _municipality_key(value: str) -> str:
    return re.sub(r"[^a-z]", "", _fold(value))


def _http_fetch(client: httpx.Client) -> Fetch:
    def fetch(url: str, headers: dict | None = None) -> httpx.Response:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        return response

    return fetch


# --- BOE -------------------------------------------------------------------------


def _summary_items(payload) -> list[dict]:
    found: list[dict] = []
    stack = [payload]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if "identificador" in item and "titulo" in item:
                found.append(item)
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return found


def _resolution_title(year: int) -> str:
    return f"fiestas laborales para el ano {year}"


def find_boe_resolution(fetch: Fetch, year: int, today: date) -> str | None:
    """Scan BOE daily summaries for the resolution with the holidays of ``year``."""
    start = date(year - 1, 9, 15)
    end = min(date(year - 1, 12, 31), today)
    wanted = _resolution_title(year)
    day = start
    while day <= end:
        try:
            response = fetch(BOE_SUMMARY_URL.format(day=day), {"Accept": "application/json"})
        except httpx.HTTPStatusError as exc:
            # Days without a BOE issue answer 404.
            if exc.response.status_code != 404:
                raise
            response = None
        if response is not None:
            for item in _summary_items(response.json()):
                if wanted in _fold(str(item["titulo"])):
                    return str(item["identificador"])
        day += timedelta(days=1)
    return None


def _clean_holiday_name(text: str) -> str:
    text = re.sub(r"\s+", " ", unescape(text)).strip()
    text = re.sub(r"^\d{1,2}\s+", "", text).rstrip(". ")
    return text[:1].upper() + text[1:] if text else text


def _text(element) -> str:
    return " ".join("".join(element.itertext()).split())


def parse_boe_resolution(xml_text: str, year: int, identifier: str) -> list[OfficialHoliday]:
    """National and Comunitat Valenciana holidays from the annex table.

    The annex has one row per month followed by one row per holiday, and one column
    per autonomous community. ``***`` marks a regional holiday and ``*``/``**`` a
    national one. The table is read by position because older and newer resolutions
    use different markup.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise HolidaySourceError(f"XML del BOE no válido ({identifier})") from exc
    title = " ".join((root.findtext(".//titulo") or "").split())
    if _resolution_title(year) not in _fold(title):
        raise HolidaySourceError(f"{identifier} no es la relación de fiestas de {year}")
    table = next(
        (
            item
            for item in root.iter("table")
            if any("valenciana" in _fold(_text(th)) for th in item.iter("th"))
        ),
        None,
    )
    if table is None:
        raise HolidaySourceError(f"{identifier} no tiene la tabla de fiestas autonómicas")
    header_rows = [row for row in table.iter("tr") if row.find("th") is not None]
    communities = [_text(th) for th in header_rows[-1].findall("th")]
    column = next(
        index for index, name in enumerate(communities) if "valenciana" in _fold(name)
    )
    from_end = len(communities) - column
    holidays: dict[date, OfficialHoliday] = {}
    month = None
    for row in table.iter("tr"):
        cells = row.findall("td")
        if not cells:
            continue
        label = _text(cells[0])
        month = MONTHS.get(_fold(label).rstrip(". "), month)
        day_match = re.match(r"(\d{1,2})\b", label)
        if not day_match or month is None or len(cells) < from_end + 1:
            continue
        mark = _text(cells[-from_end]).replace(" ", "")
        if not mark.startswith("*"):
            continue
        holiday_date = date(year, month, int(day_match.group(1)))
        holidays[holiday_date] = OfficialHoliday(
            holiday_date=holiday_date,
            name=_clean_holiday_name(label) or "Festivo",
            scope="regional" if mark == "***" else "national",
            source="boe",
            source_reference=identifier,
            source_url=BOE_HTML_URL.format(identifier=identifier),
        )
    if not holidays:
        raise HolidaySourceError(
            f"{identifier} no contiene festivos para la {AUTONOMOUS_COMMUNITY}"
        )
    return sorted(holidays.values(), key=lambda item: item.holiday_date)


# --- Ayuntamiento de l'Alfàs del Pi ---------------------------------------------------


def parse_town_hall_calendar(html: str) -> tuple[int, list[OfficialHoliday], list[tuple]]:
    """Return the year, the local holidays and every labelled day of the calendar."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", unescape(text))
    year_match = re.search(r"festivos\s+(\d{4})", text, re.IGNORECASE)
    if not year_match:
        raise HolidaySourceError("El calendario municipal no indica el año")
    year = int(year_match.group(1))
    entries = re.findall(
        r"D[ií]a\s+(\d{1,2})\s+de\s+([a-záéíóú]+)\s*\([^)]*\)\s*:\s*(.+?)\s*"
        r"\(Fiesta\s+(Nacional|Auton[oó]mica|Local)\)",
        text,
        re.IGNORECASE,
    )
    days: list[tuple] = []
    local: list[OfficialHoliday] = []
    for day, month_name, name, kind in entries:
        month = MONTHS.get(_fold(month_name))
        if month is None:
            continue
        holiday_date = date(year, month, int(day))
        scope = {"nacional": "national", "autonomica": "regional", "local": "municipal"}[
            _fold(kind)
        ]
        days.append((holiday_date, scope, name.strip()))
        if scope == "municipal":
            local.append(
                OfficialHoliday(
                    holiday_date=holiday_date,
                    name=LOCAL_HOLIDAY_NAME,
                    scope="municipal",
                    source="ayuntamiento",
                    source_reference=f"Calendario oficial {year}",
                    source_url=TOWN_HALL_URL,
                )
            )
    if not days:
        raise HolidaySourceError("El calendario municipal no contiene festivos reconocibles")
    return year, local, days


# --- DOGV ----------------------------------------------------------------------------


def _pdf_text(content: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def parse_dogv_local_resolution(
    text: str, year: int, reference: str, url: str
) -> list[OfficialHoliday]:
    """Local holidays of the municipality from the annual DOGV resolution."""
    if not re.search(rf"para\s+el\s+a[nñ]o\s+{year}", text, re.IGNORECASE):
        raise HolidaySourceError(f"{reference} no es el calendario de fiestas locales de {year}")
    wanted = _municipality_key(MUNICIPALITY)
    lines = [line.strip() for line in text.splitlines()]
    entry = None
    for index, line in enumerate(lines):
        name, separator, rest = line.partition(":")
        if separator and _municipality_key(name) == wanted:
            entry = rest
            # The entry may continue on the next line when it is long.
            following = lines[index + 1] if index + 1 < len(lines) else ""
            if not entry.rstrip().endswith(".") and ":" not in following:
                entry += " " + following
            break
    if entry is None:
        raise HolidaySourceError(f"{reference} no incluye {MUNICIPALITY}")
    month_names = "|".join(MONTHS)
    dates: list[date] = []
    for days, month_name in re.findall(
        rf"((?:\d{{1,2}}\s*(?:,|y|e)\s*)*\d{{1,2}})\s+de\s+({month_names})",
        _fold(entry),
    ):
        for day in re.findall(r"\d{1,2}", days):
            dates.append(date(year, MONTHS[month_name], int(day)))
    if not dates:
        raise HolidaySourceError(f"{reference} no tiene fechas reconocibles para {MUNICIPALITY}")
    return [
        OfficialHoliday(
            holiday_date=holiday_date,
            name=LOCAL_HOLIDAY_NAME,
            scope="municipal",
            source="dogv",
            source_reference=reference,
            source_url=url,
        )
        for holiday_date in sorted(set(dates))
    ]


# --- Storage ---------------------------------------------------------------------------


def _replace_year(db: Session, year: int, scopes: set[str], items: list[OfficialHoliday]) -> None:
    db.execute(
        delete(TariffHoliday).where(
            extract("year", TariffHoliday.holiday_date) == year,
            TariffHoliday.scope.in_(scopes),
        )
    )
    for item in items:
        db.add(
            TariffHoliday(
                holiday_date=item.holiday_date,
                name=item.name[:180],
                scope=item.scope,
                region=AUTONOMOUS_COMMUNITY if item.scope == "regional" else None,
                municipality=MUNICIPALITY if item.scope == "municipal" else None,
                source=item.source,
                source_reference=item.source_reference,
                source_url=item.source_url,
            )
        )


def sync_state(db: Session) -> dict:
    setting = db.get(AppSetting, SYNC_STATE_KEY)
    return dict(setting.value) if setting and setting.value else {}


def _save_state(db: Session, state: dict) -> None:
    setting = db.get(AppSetting, SYNC_STATE_KEY)
    if setting is None:
        setting = AppSetting(key=SYNC_STATE_KEY, value={})
        db.add(setting)
    setting.value = state
    setting.updated_at = datetime.now(UTC)


def sync_due(db: Session, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    last = sync_state(db).get("last_run_at")
    return last is None or now - datetime.fromisoformat(last) >= SYNC_INTERVAL


def sync_official_holidays(
    db: Session, fetch: Fetch | None = None, today: date | None = None
) -> dict:
    """Refresh national, regional and local holidays from official sources."""
    today = today or date.today()
    client = None
    if fetch is None:
        client = httpx.Client(
            timeout=30, follow_redirects=False, headers={"User-Agent": USER_AGENT}
        )
        fetch = _http_fetch(client)
    previous = sync_state(db)
    boe_ids: dict[str, str] = dict(previous.get("boe_resolutions") or {})
    warnings: list[str] = []
    years: dict[str, dict] = {}
    try:
        town_hall_year = None
        town_hall_local: list[OfficialHoliday] = []
        town_hall_days: list[tuple] = []
        try:
            response = fetch(TOWN_HALL_URL, None)
            town_hall_year, town_hall_local, town_hall_days = parse_town_hall_calendar(
                response.text
            )
        except (httpx.HTTPError, HolidaySourceError) as exc:
            warnings.append(f"Calendario del Ayuntamiento no disponible: {_describe(exc)}")

        for year in range(today.year - HISTORY_YEARS, today.year + 2):
            status = {"national_regional": None, "local": None}
            boe_holidays: list[OfficialHoliday] = []
            identifier = boe_ids.get(str(year)) or BOE_KNOWN_RESOLUTIONS.get(year)
            try:
                if identifier is None and today >= date(year - 1, 9, 15):
                    identifier = find_boe_resolution(fetch, year, today)
                if identifier:
                    response = fetch(BOE_XML_URL.format(identifier=identifier), None)
                    boe_holidays = parse_boe_resolution(response.text, year, identifier)
                    _replace_year(db, year, {"national", "regional"}, boe_holidays)
                    boe_ids[str(year)] = identifier
                    status["national_regional"] = identifier
            except (httpx.HTTPError, HolidaySourceError, ValueError) as exc:
                warnings.append(f"BOE {year}: {_describe(exc)}")

            local: list[OfficialHoliday] = []
            dogv_local: list[OfficialHoliday] = []
            resolution = DOGV_LOCAL_RESOLUTIONS.get(year)
            if resolution:
                reference, url = resolution
                try:
                    response = fetch(url, None)
                    dogv_local = parse_dogv_local_resolution(
                        _pdf_text(response.content), year, reference, url
                    )
                except Exception as exc:  # pypdf raises several error types
                    warnings.append(f"DOGV {year}: {_describe(exc)}")
            if year == town_hall_year and town_hall_local:
                # The town hall reflects later amendments of the DOGV resolution.
                local = town_hall_local
                dogv_dates = {item.holiday_date for item in dogv_local}
                if dogv_local and dogv_dates != {item.holiday_date for item in local}:
                    warnings.append(
                        f"Festivos locales {year}: el Ayuntamiento y el DOGV no coinciden; "
                        "se usa el calendario municipal."
                    )
            else:
                local = dogv_local
            if local:
                _replace_year(db, year, {"municipal"}, local)
                status["local"] = local[0].source_reference

            if year == town_hall_year and boe_holidays:
                # Scopes may differ (for example San José): compare the dates only.
                boe_dates = {item.holiday_date for item in boe_holidays}
                town_dates = {
                    holiday_date
                    for holiday_date, scope, _ in town_hall_days
                    if scope != "municipal"
                }
                if boe_dates != town_dates:
                    warnings.append(
                        f"Festivos nacionales y autonómicos {year}: el BOE y el Ayuntamiento "
                        "no coinciden; se usa el BOE."
                    )
            years[str(year)] = status
        state = {
            "last_run_at": datetime.now(UTC).isoformat(),
            "last_success_at": datetime.now(UTC).isoformat(),
            "boe_resolutions": boe_ids,
            "years": years,
            "warnings": warnings,
            "error": None,
        }
        _save_state(db, state)
        db.commit()
        return state
    except Exception as exc:
        db.rollback()
        state = {
            **previous,
            "last_run_at": datetime.now(UTC).isoformat(),
            "error": _describe(exc),
        }
        _save_state(db, state)
        db.commit()
        raise
    finally:
        if client is not None:
            client.close()


def _describe(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"respuesta HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError):
        return f"error de conexión ({type(exc).__name__})"
    return str(exc)[:300] or type(exc).__name__


def holiday_sync_view(db: Session, today: date | None = None) -> dict:
    """Synchronization state and yearly coverage for the interface."""
    today = today or date.today()
    state = sync_state(db)
    rows = db.scalars(select(TariffHoliday)).all()
    coverage = []
    for year in range(today.year - HISTORY_YEARS, today.year + 2):
        year_rows = [row for row in rows if row.holiday_date.year == year]
        coverage.append(
            {
                "year": year,
                "national": sum(1 for row in year_rows if row.scope == "national"),
                "regional": sum(1 for row in year_rows if row.scope == "regional"),
                "local": sum(1 for row in year_rows if row.scope == "municipal"),
            }
        )
    return {
        "last_run_at": state.get("last_run_at"),
        "last_success_at": state.get("last_success_at"),
        "error": state.get("error"),
        "warnings": state.get("warnings") or [],
        "coverage": coverage,
    }
