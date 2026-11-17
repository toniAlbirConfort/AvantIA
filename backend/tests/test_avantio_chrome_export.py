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
