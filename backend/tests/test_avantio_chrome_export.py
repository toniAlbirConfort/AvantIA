from __future__ import annotations

import asyncio

import pytest

from app import avantio_chrome_export
from app.avantio_chrome_export import ExportError, _open_menu


class Element:
    def __init__(self, page, name: str, visible):
        self.page = page
        self.name = name
        self.visible = visible

    async def count(self):
        return 1 if self.visible() else 0

    def nth(self, index: int):
        assert index == 0
        return self

    async def is_visible(self):
        return self.visible()

    async def click(self):
        self.page.clicks.append(self.name)
        if self.name == "toggle":
            self.page.menu_open = True


class Page:
    """Avantio dashboard: narrow windows hide the menu behind ☰, wide ones show it always."""

    def __init__(self, *, wide: bool, render_after_waits: int = 0, menu: bool = True):
        self.wide = wide
        self.menu = menu
        self.render_after_waits = render_after_waits
        self.menu_open = False
        self.waits = 0
        self.clicks: list[str] = []

    def rendered(self) -> bool:
        return self.menu and self.waits >= self.render_after_waits

    def locator(self, selector: str):
        if selector == "button:has(.fa-bars)":
            return Element(self, "toggle", lambda: self.rendered() and not self.wide)
        return Element(self, selector, lambda: False)

    def get_by_role(self, role, name, exact):
        if role == "link" and name == "Reservas":
            return Element(
                self, name, lambda: self.rendered() and (self.wide or self.menu_open)
            )
        return Element(self, name, lambda: False)

    def get_by_text(self, label, exact):
        return Element(self, label, lambda: False)

    async def wait_for_timeout(self, milliseconds):
        self.waits += 1


def test_narrow_window_opens_the_menu_with_the_toggle():
    page = Page(wide=False)
    asyncio.run(_open_menu(page))
    assert page.clicks == ["toggle"]


def test_wide_window_uses_the_permanent_side_menu():
    page = Page(wide=True)
    asyncio.run(_open_menu(page))
    assert page.clicks == []
    assert page.waits == 0


def test_waits_for_the_dashboard_to_render():
    page = Page(wide=True, render_after_waits=3)
    asyncio.run(_open_menu(page))
    assert page.waits == 3


def test_reports_missing_navigation(monkeypatch):
    monkeypatch.setattr(avantio_chrome_export, "MENU_WAIT_STEPS", 4)
    page = Page(wide=True, menu=False)
    with pytest.raises(ExportError, match="menú de navegación"):
        asyncio.run(_open_menu(page))
    assert page.waits == 4


class Candidate:
    def __init__(self, clicks: list[str], name: str, y: float, receives_clicks):
        self.clicks = clicks
        self.name = name
        self.y = y
        self.receives_clicks = receives_clicks

    async def is_visible(self):
        return True

    covered_for = 0  # hit tests that still land on a loading layer

    async def evaluate(self, script):
        if isinstance(self.receives_clicks, Exception):
            raise self.receives_clicks
        if self.covered_for:
            self.covered_for -= 1
            return False
        return self.receives_clicks

    async def bounding_box(self):
        return {"x": 18, "y": self.y, "width": 255, "height": 44}

    async def click(self):
        self.clicks.append(self.name)


class Candidates:
    def __init__(self, items: list[Candidate]):
        self.items = items

    async def count(self):
        return len(self.items)

    def nth(self, index: int):
        return self.items[index]


class MenuPage:
    """Side menu with «Reservas» twice: the top-level entry and one in the folded Reportes
    submenu, which Playwright reports as visible although the entries below cover it."""

    def __init__(self, folded_receives_clicks=False):
        self.clicks: list[str] = []
        self.folded = Candidate(self.clicks, "folded submenu", 344, folded_receives_clicks)
        self.top_level = Candidate(self.clicks, "top-level", 246, True)

    def get_by_role(self, role, name, exact):
        # Only the submenu entry exposes exactly «Reservas» as its accessible name.
        return Candidates([self.folded] if role == "button" else [])

    def get_by_text(self, label, exact):
        return Candidates([self.top_level, self.folded])


def test_skips_the_covered_reservas_entry_of_a_folded_submenu():
    page = MenuPage()
    asyncio.run(
        avantio_chrome_export._click_visible_label(
            page, "Reservas", ("menuitem", "link", "button")
        )
    )
    assert page.clicks == ["top-level"]


def test_keeps_candidates_when_the_hit_test_cannot_run():
    page = MenuPage(folded_receives_clicks=avantio_chrome_export.PlaywrightError("detached"))
    asyncio.run(
        avantio_chrome_export._click_visible_label(
            page, "Reservas", ("menuitem", "link", "button")
        )
    )
    # Previous behaviour: the role match is clicked when nothing says it is covered.
    assert page.clicks == ["folded submenu"]


class DrawerPage:
    """Bookings page reached through «Reservas»: the side menu is a drawer, closed or open."""

    def __init__(self, *, open_: bool):
        self.open = open_
        self.clicks: list[str] = []
        self.waits = 0
        page = self

        class Entry(Candidate):
            async def evaluate(self, script):
                return page.open

        class Toggle(Candidate):
            async def click(self):
                page.clicks.append("toggle")
                page.open = not page.open

        self.entry = Entry(self.clicks, "Listado de reservas", 238, None)
        self.toggle = Toggle(self.clicks, "toggle", 0, True)

    def locator(self, selector: str):
        toggle = selector == "div.tabBarraSuperior.flexBarraSuperior"
        return Candidates([self.toggle] if toggle else [])

    def get_by_role(self, role, name, exact):
        return Candidates([])

    def get_by_text(self, label, exact):
        return Candidates([self.entry] if label == "Listado de reservas" else [])

    async def wait_for_timeout(self, milliseconds):
        self.waits += 1


def test_reopens_the_closed_drawer_before_clicking_listado_de_reservas():
    page = DrawerPage(open_=False)
    asyncio.run(avantio_chrome_export._reveal_menu_entry(page, "Listado de reservas"))
    asyncio.run(
        avantio_chrome_export._click_visible_label(
            page, "Listado de reservas", ("menuitem", "link", "button")
        )
    )
    assert page.clicks == ["toggle", "Listado de reservas"]


def test_leaves_an_open_drawer_alone():
    page = DrawerPage(open_=True)
    asyncio.run(avantio_chrome_export._reveal_menu_entry(page, "Listado de reservas"))
    assert page.clicks == []
    assert page.open


class BookingsListPage:
    """Toolbar «Más acciones» plus one icon-only ⋮ button per row with the same aria-label."""

    def __init__(self, covered_for: int = 0):
        self.clicks: list[str] = []
        self.waits = 0
        self.toolbar = Candidate(self.clicks, "toolbar", 131, True)
        self.toolbar.covered_for = covered_for
        self.rows = [Candidate(self.clicks, f"row {i}", 394 + 90 * i, True) for i in range(3)]

    def get_by_role(self, role, name, exact):
        return Candidates([self.toolbar, *self.rows] if role == "button" else [])

    def get_by_text(self, label, exact):
        return Candidates([self.toolbar])

    async def wait_for_timeout(self, milliseconds):
        self.waits += 1


def test_mas_acciones_waits_for_the_loading_layer_and_clicks_the_toolbar_button():
    page = BookingsListPage(covered_for=3)
    asyncio.run(
        avantio_chrome_export._click_visible_label(page, "Más acciones", ("button", "link"))
    )
    assert page.clicks == ["toolbar"]
    assert page.waits == 3


def test_mas_acciones_ignores_the_row_menus():
    page = BookingsListPage()
    page.toolbar.receives_clicks = False  # covered: there is no toolbar button to click
    with pytest.raises(ExportError, match="Más acciones"):
        asyncio.run(
            avantio_chrome_export._click_visible_label(page, "Más acciones", ("button", "link"))
        )
    assert page.clicks == []


FOLDED_SUBMENU_HTML = """
<avantio-menu></avantio-menu>
<script>
  const root = document.querySelector('avantio-menu').attachShadow({mode: 'open'});
  root.innerHTML = `
    <style>
      a { display: flex; height: 44px; background: white; }
      ul { margin: 0; padding: 0; list-style: none; }
      .folded { height: 0; overflow: hidden; }
    </style>
    <nav><ul>
      <li><a id="top" href="#"><span>Reservas</span></a></li>
      <li><a href="#"><span>Reportes</span></a>
        <ul class="folded">
          <li><a id="sub" role="button" href="#"><span>Reservas</span></a></li>
        </ul>
      </li>
      <li><a id="next" href="#"><span>Alojamientos</span></a></li>
    </ul></nav>
    <nav style="position: fixed; top: 0; left: 0; width: 300px; transform: translateX(-1900px)">
      <a id="drawer" href="#"><span>Listado de reservas</span></a>
    </nav>`;
</script>
"""


def test_hit_test_detects_folded_submenu_entries_in_a_shadow_root():
    async def run():
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            try:
                browser = await playwright.chromium.launch()
            except avantio_chrome_export.PlaywrightError:
                pytest.skip("Chromium de Playwright no está instalado")
            try:
                page = await browser.new_page()
                await page.set_content(FOLDED_SUBMENU_HTML)
                top = page.locator("#top")
                sub = page.locator("#sub")
                assert await sub.is_visible()  # Playwright alone cannot tell them apart.
                assert await avantio_chrome_export._receives_clicks(top)
                assert not await avantio_chrome_export._receives_clicks(sub)
                drawer = page.locator("#drawer")
                assert await drawer.is_visible()
                assert not await avantio_chrome_export._receives_clicks(drawer)
            finally:
                await browser.close()

    asyncio.run(run())
