from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from app import holiday_sync
from app.business_scope import AUTONOMOUS_COMMUNITY, MUNICIPALITY
from app.holiday_sync import (
    BOE_SUMMARY_URL,
    BOE_XML_URL,
    DOGV_LOCAL_RESOLUTIONS,
    LOCAL_HOLIDAY_NAME,
    TOWN_HALL_URL,
    HolidaySourceError,
    find_boe_resolution,
    holiday_sync_view,
    parse_boe_resolution,
    parse_dogv_local_resolution,
    parse_town_hall_calendar,
    sync_due,
    sync_official_holidays,
)
from app.models import TariffHoliday, TariffStudy
from app.tariff_lab import calculate_study, register_market_provider

COMMUNITIES = ["Andalucía", "Illes Balears", "Comunitat Valenciana", "Ciudad de Melilla"]


def boe_xml(year: int, rows: list[tuple[str, list[str]]], *, with_ids: bool = True) -> str:
    """A BOE resolution annex. ``rows`` are (label, marks per community)."""
    header = "".join(
        f'<th axis="comunidad" id="header{index}">{name}</th>'
        if with_ids
        else f'<th class="cabeza_tabla">{name}</th>'
        for index, name in enumerate(COMMUNITIES)
    )
    body = []
    for label, marks in rows:
        if not marks:
            body.append(
                f'<tr><td axis="mes">{label}</td>' + "<td> </td>" * len(COMMUNITIES) + "</tr>"
            )
            continue
        cells = "".join(
            f'<td><abbr title="Fiesta">{mark}</abbr></td>' if mark else "<td> </td>"
            for mark in marks
        )
        body.append(f'<tr><td class="cuerpo_tabla_izq">{label}</td>{cells}</tr>')
    return (
        "<?xml version='1.0' encoding='UTF-8'?><documento><metadatos>"
        f"<titulo>Resolución de 8 de octubre, de la Dirección General de Trabajo, por la que se "
        f"publica relación de fiestas laborales para el año {year}.</titulo></metadatos><texto>"
        '<table><thead><tr><th rowspan="2">Fecha de las fiestas</th>'
        f'<th colspan="{len(COMMUNITIES)}">Comunidades autónomas</th></tr>'
        f"<tr>{header}</tr></thead><tbody>{''.join(body)}</tbody></table></texto></documento>"
    )


BOE_2027 = boe_xml(
    2027,
    [
        ("Enero", []),
        ("1 Año Nuevo.", ["*", "*", "*", "*"]),
        ("6 Epifanía del Señor.", ["**", "**", "**", ""]),
        ("Marzo", []),
        ("1 Día de las Illes Balears.", ["", "***", "", ""]),
        ("29 Lunes de Pascua.", ["", "***", "***", ""]),
        ("Octubre", []),
        ("9 Día de la Comunitat Valenciana.", ["", "", "***", ""]),
    ],
)

TOWN_HALL_2027 = """<html><body><h2>Calendario oficial laboral y de días inhábiles y festivos 2027</h2>
<p>Día 1 de enero (viernes):&nbsp;AÑO NUEVO&nbsp;(Fiesta Nacional)</p>
<p>Día 6 de enero (miércoles):&nbsp;EPIFANIA DEL SEÑOR&nbsp;(Fiesta Nacional)</p>
<p>Día 29 de marzo (lunes):&nbsp;LUNES DE PASCUA&nbsp;(Fiesta Autonómica)</p>
<p>Día 9 de octubre (sábado):&nbsp;DIA DE LA COMUNITAT VALENCIANA&nbsp;(Fiesta Autonómica)</p>
<p>Día 8 de noviembre (lunes):&nbsp;FESTIVO LOCAL&nbsp;(Fiesta Local)</p>
<p>Día 9 de noviembre (martes):&nbsp;FESTIVO LOCAL&nbsp;(Fiesta Local)</p>
</body></html>"""

DOGV_TEXT_2026 = """RESOLUCIÓN de 12 de noviembre de 2025, por la que se aprueba el calendario de fiestas
locales, retribuidas y no recuperables, en el ámbito de la Comunitat Valenciana para el año 2026.
RELACIÓN DE FIESTAS LOCALES EN LA PROVINCIA DE ALICANTE 2026
ALFAFARA: 6 de agosto; 7 de diciembre.
L´ ALFÀS DEL PÍ: 9 de noviembre y 10 de noviembre.
BENIDORM: 9 y 10 de noviembre.
"""


def test_boe_annex_reads_the_valencian_column_with_either_markup():
    for with_ids in (True, False):
        xml = boe_xml(
            2027,
            [
                ("Enero", []),
                ("1 Año Nuevo.", ["*", "*", "*", "*"]),
                ("6 Epifanía del Señor.", ["**", "**", "**", ""]),
                ("Marzo", []),
                ("1 Día de las Illes Balears.", ["", "***", "", ""]),
                ("29 Lunes de Pascua.", ["", "***", "***", ""]),
            ],
            with_ids=with_ids,
        )
        holidays = parse_boe_resolution(xml, 2027, "BOE-A-2026-1")
        assert [(item.holiday_date, item.scope, item.name) for item in holidays] == [
            (date(2027, 1, 1), "national", "Año Nuevo"),
            (date(2027, 1, 6), "national", "Epifanía del Señor"),
            (date(2027, 3, 29), "regional", "Lunes de Pascua"),
        ]
        assert holidays[0].source_url.endswith("id=BOE-A-2026-1")


def test_boe_resolution_for_another_year_is_rejected():
    with pytest.raises(HolidaySourceError):
        parse_boe_resolution(BOE_2027, 2026, "BOE-A-2026-1")


def test_town_hall_calendar_separates_local_holidays():
    year, local, days = parse_town_hall_calendar(TOWN_HALL_2027)
    assert year == 2027
    assert [item.holiday_date for item in local] == [date(2027, 11, 8), date(2027, 11, 9)]
    assert {item.name for item in local} == {LOCAL_HOLIDAY_NAME}
    assert (date(2027, 3, 29), "regional", "LUNES DE PASCUA") in days


def test_dogv_resolution_finds_the_municipality_despite_spelling():
    holidays = parse_dogv_local_resolution(DOGV_TEXT_2026, 2026, "DOGV-C-1", "https://dogv.test/x")
    assert [item.holiday_date for item in holidays] == [date(2026, 11, 9), date(2026, 11, 10)]
    assert holidays[0].source == "dogv"
    with pytest.raises(HolidaySourceError):
        parse_dogv_local_resolution(DOGV_TEXT_2026, 2027, "DOGV-C-1", "https://dogv.test/x")
    with pytest.raises(HolidaySourceError):
        parse_dogv_local_resolution(
            DOGV_TEXT_2026.replace("ALFÀS DEL PÍ", "OTRO"), 2026, "DOGV-C-1", "https://x"
        )


class FakeSources:
    def __init__(self, pages: dict[str, httpx.Response]):
        self.pages = pages
        self.requested: list[str] = []

    def __call__(self, url: str, headers: dict | None = None) -> httpx.Response:
        self.requested.append(url)
        response = self.pages.get(url) or httpx.Response(404)
        response.request = httpx.Request("GET", url)
        response.raise_for_status()
        return response


def test_boe_resolution_is_discovered_from_daily_summaries():
    day = date(2026, 10, 13)
    summary = {
        "data": {
            "sumario": {
                "diario": [
                    {
                        "identificador": "BOE-A-2026-21254",
                        "titulo": "Resolución ... relación de fiestas laborales para el año 2027.",
                    }
                ]
            }
        }
    }
    fetch = FakeSources({BOE_SUMMARY_URL.format(day=day): httpx.Response(200, json=summary)})
    assert find_boe_resolution(fetch, 2027, date(2026, 11, 20)) == "BOE-A-2026-21254"
    assert find_boe_resolution(fetch, 2027, date(2026, 10, 12)) is None


def test_sync_stores_official_holidays_and_keeps_rows_when_a_source_fails(db, monkeypatch):
    monkeypatch.setattr(holiday_sync, "BOE_KNOWN_RESOLUTIONS", {2027: "BOE-A-2026-21254"})
    monkeypatch.setattr(holiday_sync, "DOGV_LOCAL_RESOLUTIONS", {})
    db.add(
        TariffHoliday(
            holiday_date=date(2026, 11, 10),
            name="Fiestas locales",
            scope="municipal",
            municipality=MUNICIPALITY,
            source="dogv",
        )
    )
    db.commit()
    fetch = FakeSources(
        {
            BOE_XML_URL.format(identifier="BOE-A-2026-21254"): httpx.Response(200, text=BOE_2027),
            TOWN_HALL_URL: httpx.Response(200, text=TOWN_HALL_2027),
        }
    )

    state = sync_official_holidays(db, fetch=fetch, today=date(2026, 11, 20))

    rows = {(row.holiday_date, row.scope): row for row in db.query(TariffHoliday).all()}
    assert rows[(date(2027, 1, 1), "national")].source_reference == "BOE-A-2026-21254"
    assert rows[(date(2027, 10, 9), "regional")].region == AUTONOMOUS_COMMUNITY
    assert rows[(date(2027, 11, 8), "municipal")].source == "ayuntamiento"
    assert rows[(date(2027, 11, 8), "municipal")].municipality == MUNICIPALITY
    # Earlier local holidays stay when no source answers for that year.
    assert (date(2026, 11, 10), "municipal") in rows
    assert state["years"]["2027"] == {
        "national_regional": "BOE-A-2026-21254",
        "local": "Calendario oficial 2027",
    }
    assert state["boe_resolutions"]["2027"] == "BOE-A-2026-21254"
    assert state["error"] is None
    assert not sync_due(db)
    assert sync_due(db, datetime.now(UTC) + timedelta(days=2))
    view = holiday_sync_view(db, today=date(2026, 11, 20))
    coverage = {item["year"]: item for item in view["coverage"]}
    assert coverage[2027] == {"year": 2027, "national": 2, "regional": 2, "local": 2}

    # A second run replaces rows instead of duplicating them.
    sync_official_holidays(db, fetch=fetch, today=date(2026, 11, 20))
    assert db.query(TariffHoliday).filter(TariffHoliday.scope == "municipal").count() == 3


def test_sync_warns_when_sources_disagree(db, monkeypatch):
    monkeypatch.setattr(holiday_sync, "BOE_KNOWN_RESOLUTIONS", {2027: "BOE-A-2026-21254"})
    monkeypatch.setattr(
        holiday_sync, "DOGV_LOCAL_RESOLUTIONS", {2027: ("DOGV-C-2026-1", "https://dogv.test/27")}
    )
    monkeypatch.setattr(
        holiday_sync,
        "_pdf_text",
        lambda content: DOGV_TEXT_2026.replace("2026", "2027"),
    )
    fetch = FakeSources(
        {
            BOE_XML_URL.format(identifier="BOE-A-2026-21254"): httpx.Response(200, text=BOE_2027),
            TOWN_HALL_URL: httpx.Response(200, text=TOWN_HALL_2027),
            "https://dogv.test/27": httpx.Response(200, content=b"%PDF"),
        }
    )
    state = sync_official_holidays(db, fetch=fetch, today=date(2026, 11, 20))
    assert any("Ayuntamiento y el DOGV no coinciden" in item for item in state["warnings"])
    # The town hall calendar wins for its own year.
    local = sorted(
        row.holiday_date
        for row in db.query(TariffHoliday).filter(TariffHoliday.scope == "municipal")
    )
    assert local == [date(2027, 11, 8), date(2027, 11, 9)]


def test_study_warns_when_local_holidays_are_not_published(db):
    from tests.test_tariff_lab import StubMarketProvider, _make_study_fixture

    study = _make_study_fixture(db)
    db.query(TariffHoliday).filter(TariffHoliday.scope == "municipal").delete()
    db.commit()
    study = db.get(TariffStudy, study.id)
    register_market_provider(StubMarketProvider())
    try:
        result = asyncio.run(calculate_study(db, study))
    finally:
        register_market_provider(None)
    assert result["status"] == "partial"
    assert any("festivos locales" in item and "2027" in item for item in result["warnings"])


def test_registered_dogv_resolutions_point_to_official_pdfs():
    for year, (reference, url) in DOGV_LOCAL_RESOLUTIONS.items():
        assert reference.startswith("DOGV-C-")
        assert url.startswith("https://dogv.gva.es/datos/") and url.endswith(".pdf")
        assert str(year - 1) in url
