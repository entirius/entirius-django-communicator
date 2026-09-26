# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The HTML mail footer per (channel, language): sanitised on save, resolved with the template language cascade,
rendered around the caller's legal text, converted to the plain-text part.

The legal text stays the caller's (leads builds it from agreements per legal basis); the footer is only the layout
around it — signature, logo, company data, links."""

import re
from html.parser import HTMLParser

import nh3
from django.db.models import QuerySet
from django.utils.html import escape
from django_regional.models import Language

from django_communicator.models import Channel, MailFooter
from django_communicator.services.template_service import find_language

LEGAL_PLACEHOLDER = "{{ legal }}"
_PLACEHOLDER = re.compile(r"\{\{\s*legal\s*\}\}")
_TAGS = {"a", "img", "p", "br", "strong", "em", "span", "div", "table", "thead", "tbody", "tfoot", "tr", "td", "th"}
_CELL = {"style", "align", "valign", "width", "colspan", "rowspan"}
_ATTRIBUTES = {
    "*": {"style"},
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "width", "height"},
    "table": {"width", "align", "border", "cellpadding", "cellspacing"},
    "td": _CELL,
    "th": _CELL,
}
_STYLES = {
    "color", "background-color", "font-family", "font-size", "font-weight", "font-style", "text-align",
    "text-decoration", "line-height", "vertical-align", "width", "height", "max-width",
    "padding", "padding-top", "padding-right", "padding-bottom", "padding-left", "margin", "margin-top", "margin-bottom",
}  # fmt: skip
_URL_SCHEMES = {"http", "https", "mailto", "tel"}
_TAG = re.compile(r'<[a-z0-9]+(?:\s+[^\s="<>]+(?:="[^"]*")?)*\s*/?>')  # nh3 output: text `<` is `&lt;`
_STYLE_ATTRIBUTE = re.compile(r'(?<=\s)style="([^"]*)"')
_UNSAFE_STYLE = re.compile(r"expression\s*\(|url\s*\(|\\|/\*", re.IGNORECASE)  # CSS escapes/comments hide both


class FooterError(ValueError):
    """Footer HTML refused on save: the `{{ legal }}` placeholder missing, repeated or inside an attribute."""


def sanitise(html: str) -> str:
    """Allowlist clean (pasted from other tools); the placeholder is normalised to `{{ legal }}`.

    nh3 filters CSS by property name only, so declarations whose value loads or evaluates something (`url(`,
    `expression(`) are dropped afterwards."""
    cleaned = nh3.clean(
        html, tags=_TAGS, attributes=_ATTRIBUTES, filter_style_properties=_STYLES, url_schemes=_URL_SCHEMES
    )
    cleaned = _TAG.sub(lambda tag: _STYLE_ATTRIBUTE.sub(_safe_style, tag.group()), cleaned)
    return _PLACEHOLDER.sub(LEGAL_PLACEHOLDER, cleaned).strip()


def _safe_style(match: re.Match) -> str:
    declarations = (part for part in match.group(1).split(";") if part.strip())
    return f'style="{";".join(part for part in declarations if not _UNSAFE_STYLE.search(part))}"'


def list_footers(channel: Channel) -> QuerySet[MailFooter]:
    return MailFooter.objects.select_related("language").filter(channel=channel).order_by("language__iso2")


def get_footer(channel: Channel, language: Language) -> MailFooter:
    """Raises `MailFooter.DoesNotExist`."""
    return list_footers(channel).get(language=language)


def save_footer(channel: Channel, language: Language, html: str) -> MailFooter:
    """Create or replace; raises `FooterError` unless the cleaned HTML has the placeholder exactly once, as text."""
    cleaned = sanitise(html)
    _check_placeholder(cleaned)
    footer, _ = MailFooter.objects.update_or_create(channel=channel, language=language, defaults={"html": cleaned})
    return footer


def _check_placeholder(html: str) -> None:
    """In an attribute (`href`, `alt`) the legal text would be rendered where the recipient never sees it."""
    parser = _PlaceholderParser()
    parser.feed(html)
    parser.close()
    if parser.in_attributes:
        raise FooterError(f"{LEGAL_PLACEHOLDER} must be text of the footer, not inside an attribute.")
    if parser.in_text != 1 or html.count(LEGAL_PLACEHOLDER) != 1:
        raise FooterError(f"The footer must contain {LEGAL_PLACEHOLDER} exactly once.")


def delete_footer(footer: MailFooter) -> None:
    footer.delete()


def resolve(channel: Channel, language_code: str) -> MailFooter | None:
    """The recipient language's footer, else the channel default language's, else None (legal text alone)."""
    language = find_language(language_code)
    languages = [pk for pk in dict.fromkeys((language.pk if language else None, channel.default_language_id)) if pk]
    footers = {
        footer.language_id: footer for footer in MailFooter.objects.filter(channel=channel, language__in=languages)
    }
    return next((footers[pk] for pk in languages if pk in footers), None)


def paragraphs(text: str) -> str:
    """Plain text as escaped HTML paragraphs: blank lines split, single newlines become `<br>`."""
    parts = (part for part in text.split("\n\n") if part.strip())
    return "".join(f"<p>{escape(part).replace(chr(10), '<br>')}</p>" for part in parts)


def render(footer: MailFooter, legal_text: str) -> str:
    """The footer HTML with the legal text in place of the placeholder (nothing when the call carried none)."""
    return footer.html.replace(LEGAL_PLACEHOLDER, paragraphs(legal_text.strip()), 1)


def to_text(html: str) -> str:
    """The plain-text part of a footer: blocks become lines, links `text <url>`, images dropped."""
    parser = _TextParser()
    parser.feed(html)
    parser.close()
    lines = (" ".join(line.split()) for line in "".join(parser.parts).split("\n"))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


class _PlaceholderParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_text = 0
        self.in_attributes = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.in_attributes += sum((value or "").count(LEGAL_PLACEHOLDER) for _, value in attrs)

    def handle_data(self, data: str) -> None:
        self.in_text += data.count(LEGAL_PLACEHOLDER)


class _TextParser(HTMLParser):
    _BLOCKS = frozenset({"p", "div", "table", "tr", "thead", "tbody", "tfoot"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._links: list[tuple[str, int]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self.parts.append("\n")
        elif tag in self._BLOCKS:
            self.parts.append("\n\n" if tag == "p" else "\n")
        elif tag == "a":
            self._links.append((dict(attrs).get("href") or "", len(self.parts)))

    def handle_endtag(self, tag: str) -> None:
        if tag in self._BLOCKS:
            self.parts.append("\n\n" if tag == "p" else "\n")
        elif tag in ("td", "th"):
            self.parts.append(" ")
        elif tag == "a" and self._links:
            self._close_link(*self._links.pop())

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def _close_link(self, href: str, start: int) -> None:
        text = " ".join("".join(self.parts[start:]).split())
        target = href.removeprefix("mailto:").removeprefix("tel:")
        if href and target != text:
            self.parts.append(f" <{target}>" if text else target)
