"""Allowlist sanitizer for HTML written by operators (reply bodies and signatures).

Operators compose replies and signatures in a rich-text editor. That HTML is shown back
to other operators and sent to customers, so only plain formatting survives: no scripts,
event handlers, styles that can hide content, forms or remote resources.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

ALLOWED_TAGS = {
    "a", "b", "blockquote", "br", "div", "em", "h1", "h2", "h3", "hr", "i", "li", "ol",
    "p", "s", "span", "strong", "sub", "sup", "table", "tbody", "td", "th", "thead", "tr",
    "u", "ul", "img", "font",
}
VOID_TAGS = {"br", "hr", "img"}
DROP_CONTENT = {"script", "style", "head", "title", "iframe", "object", "embed", "template",
                "noscript", "svg", "math", "form", "textarea", "select", "button"}
ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
    "font": {"color", "size"},
}
GLOBAL_ATTRS = {"style", "data-signature"}
STYLE_PROPERTIES = {
    "color", "background-color", "font-weight", "font-style", "text-decoration",
    "font-size", "font-family", "text-align", "margin", "margin-top", "margin-bottom",
    "padding", "line-height",
}
SAFE_URL = re.compile(r"^(https?:|mailto:|tel:)", re.I)
SAFE_IMAGE = re.compile(r"^data:image/(png|jpe?g|gif|webp);base64,[a-z0-9+/=\s]+$", re.I)


def _clean_style(value: str) -> str:
    kept = []
    for declaration in value.split(";"):
        name, sep, raw = declaration.partition(":")
        name, raw = name.strip().lower(), raw.strip()
        if not sep or name not in STYLE_PROPERTIES or not raw:
            continue
        if re.search(r"url\s*\(|expression|javascript:|[<>\\]", raw, re.I):
            continue
        kept.append(f"{name}: {raw}")
    return "; ".join(kept)


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.open: list[str] = []
        self.dropping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in DROP_CONTENT:
            if tag not in VOID_TAGS:
                self.dropping += 1
            return
        if self.dropping or tag not in ALLOWED_TAGS:
            return
        parts = [tag]
        allowed = ALLOWED_ATTRS.get(tag, set()) | GLOBAL_ATTRS
        for name, value in attrs:
            name = name.lower()
            value = (value or "").strip()
            if name not in allowed:
                continue
            if name == "href" and not SAFE_URL.match(value):
                continue
            if name == "src" and not SAFE_IMAGE.match(value):
                continue
            if name == "style":
                value = _clean_style(value)
                if not value:
                    continue
            parts.append(f'{name}="{html.escape(value, quote=True)}"')
        if tag == "a":
            parts.append('rel="noopener noreferrer"')
        if tag == "img" and not any(part.startswith("src=") for part in parts):
            return
        self.out.append("<" + " ".join(parts) + ">")
        if tag not in VOID_TAGS:
            self.open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS and self.open and self.open[-1] == tag:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in DROP_CONTENT:
            self.dropping = max(0, self.dropping - 1)
            return
        if self.dropping or tag not in ALLOWED_TAGS or tag in VOID_TAGS or tag not in self.open:
            return
        while self.open:
            current = self.open.pop()
            self.out.append(f"</{current}>")
            if current == tag:
                break

    def handle_data(self, data: str) -> None:
        if not self.dropping:
            self.out.append(html.escape(data, quote=False))

    def result(self) -> str:
        self.close()
        return "".join(self.out) + "".join(f"</{tag}>" for tag in reversed(self.open))


def sanitize_html(source: str) -> str:
    parser = _Sanitizer()
    parser.feed(source or "")
    return parser.result().strip()


def text_to_html(text: str) -> str:
    paragraphs = [item for item in re.split(r"\n{2,}", (text or "").strip()) if item.strip()]
    return "".join(
        "<p>" + html.escape(item, quote=False).replace("\n", "<br>") + "</p>" for item in paragraphs
    )
