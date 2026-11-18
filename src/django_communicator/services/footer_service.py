# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The HTML mail footer per (channel, language, brand): sanitised on save, resolved with the template language
cascade and the mailbox brand, rendered around the caller's legal text and the unsubscribe link, converted to the
plain-text part. Also the sanitiser of a mailbox signature (the same allowlist, no placeholders).

The legal text stays the caller's (leads builds it from agreements per legal basis); the footer is only the layout
around it — signature, logo, company data, links."""

import re
from html.parser import HTMLParser

import nh3
from django.db.models import QuerySet
from django.utils.html import escape
from django_regional.models import Language

from django_communicator.models import Channel, MailFooter
from django_communicator.models.mailbox import BRAND_PATTERN
from django_communicator.services.exceptions import ClientError
from django_communicator.services.template_service import find_language

LEGAL_PLACEHOLDER = "{{ legal }}"
UNSUBSCRIBE_PLACEHOLDER = "{{ unsubscribe }}"
_PLACEHOLDER = re.compile(r"\{\{\s*legal\s*\}\}")
_UNSUBSCRIBE = re.compile(r"\{\{\s*unsubscribe\s*\}\}")
_BRAND = re.compile(BRAND_PATTERN)
UNSUBSCRIBE_LABELS = {
    "pl": "Nie chcę otrzymywać kolejnych wiadomości",
    "en": "I don't want to receive further messages",
}
UNSUBSCRIBE_LABEL_MAX = 120
UNSUBSCRIBE_LINK_STYLE = "color:#555555"
UNSUBSCRIBE_PARAGRAPH_STYLE = "font-family:Arial,sans-serif;font-size:11px;color:#555555"
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


class FooterError(ClientError, ValueError):
    """Footer HTML refused on save: the `{{ legal }}` placeholder missing, repeated or inside an attribute, or
    `{{ unsubscribe }}` repeated or inside an attribute; or a signature carrying a placeholder."""


class BrandError(ClientError, ValueError):
    """A brand that is not a slug of up to 32 characters (a-z, 0-9, dot, hyphen)."""


def clean_brand(brand: str) -> str:
    """Trimmed and lower-cased; raises `BrandError` for anything but a slug (blank = no brand)."""
    cleaned = (brand or "").strip().lower()
    if not _BRAND.match(cleaned):
        raise BrandError("A brand is up to 32 characters: a-z, 0-9, dot and hyphen.")
    return cleaned


def sanitise(html: str) -> str:
    """Allowlist clean (pasted from other tools); the placeholder is normalised to `{{ legal }}`.

    nh3 filters CSS by property name only, so declarations whose value loads or evaluates something (`url(`,
    `expression(`) are dropped afterwards."""
    cleaned = nh3.clean(
        html, tags=_TAGS, attributes=_ATTRIBUTES, filter_style_properties=_STYLES, url_schemes=_URL_SCHEMES
    )
    cleaned = _TAG.sub(lambda tag: _STYLE_ATTRIBUTE.sub(_safe_style, tag.group()), cleaned)
    cleaned = _UNSUBSCRIBE.sub(UNSUBSCRIBE_PLACEHOLDER, cleaned)
    return _PLACEHOLDER.sub(LEGAL_PLACEHOLDER, cleaned).strip()


def clean_signature(html: str) -> str:
    """A mailbox signature: the footer allowlist; raises `FooterError` for a placeholder (it has no legal text and
    no link of its own)."""
    cleaned = sanitise(html or "")
    if LEGAL_PLACEHOLDER in cleaned or UNSUBSCRIBE_PLACEHOLDER in cleaned:
        raise FooterError(f"A signature cannot contain {LEGAL_PLACEHOLDER} or {UNSUBSCRIBE_PLACEHOLDER}.")
    return cleaned


def _safe_style(match: re.Match) -> str:
    declarations = (part for part in match.group(1).split(";") if part.strip())
    return f'style="{";".join(part for part in declarations if not _UNSAFE_STYLE.search(part))}"'


def list_footers(channel: Channel) -> QuerySet[MailFooter]:
    return MailFooter.objects.select_related("language").filter(channel=channel).order_by("language__iso2", "brand")


def get_footer(channel: Channel, language: Language, brand: str = "") -> MailFooter:
    """Raises `MailFooter.DoesNotExist`; blank `brand` = the channel-wide footer of the language."""
    return list_footers(channel).get(language=language, brand=brand)


def save_footer(
    channel: Channel, language: Language, html: str, brand: str = "", unsubscribe_label: str = ""
) -> MailFooter:
    """Create or replace; raises `FooterError` unless the cleaned HTML has `{{ legal }}` exactly once and
    `{{ unsubscribe }}` at most once, both as text; `BrandError` for a brand that is not a slug."""
    brand = clean_brand(brand)
    cleaned = sanitise(html)
    _check_placeholders(cleaned)
    label = " ".join((unsubscribe_label or "").split())
    if len(label) > UNSUBSCRIBE_LABEL_MAX:
        raise FooterError(f"The unsubscribe link text is at most {UNSUBSCRIBE_LABEL_MAX} characters.")
    defaults = {"html": cleaned, "unsubscribe_label": label}
    footer, _ = MailFooter.objects.update_or_create(channel=channel, language=language, brand=brand, defaults=defaults)
    return footer


def _check_placeholders(html: str) -> None:
    """In an attribute (`href`, `alt`) the text would be rendered where the recipient never sees it."""
    parser = _PlaceholderParser()
    parser.feed(html)
    parser.close()
    for placeholder in (LEGAL_PLACEHOLDER, UNSUBSCRIBE_PLACEHOLDER):
        if parser.in_attributes[placeholder]:
            raise FooterError(f"{placeholder} must be text of the footer, not inside an attribute.")
    if parser.in_text[LEGAL_PLACEHOLDER] != 1 or html.count(LEGAL_PLACEHOLDER) != 1:
        raise FooterError(f"The footer must contain {LEGAL_PLACEHOLDER} exactly once.")
    if parser.in_text[UNSUBSCRIBE_PLACEHOLDER] > 1 or html.count(UNSUBSCRIBE_PLACEHOLDER) > 1:
        raise FooterError(f"The footer may contain {UNSUBSCRIBE_PLACEHOLDER} at most once.")


def delete_footer(footer: MailFooter) -> None:
    footer.delete()


def resolve(channel: Channel, language_code: str, brand: str = "") -> MailFooter | None:
    """(language, brand) → (language, blank) → (channel default language, brand) → (channel default language,
    blank) → None (legal text alone). The language is the body's, the brand the sending mailbox's."""
    language = find_language(language_code)
    languages = [pk for pk in dict.fromkeys((language.pk if language else None, channel.default_language_id)) if pk]
    brands = list(dict.fromkeys((brand, "")))
    rows = MailFooter.objects.filter(channel=channel, language__in=languages, brand__in=brands)
    footers = {(footer.language_id, footer.brand): footer for footer in rows}
    return next((footers[key] for pk in languages for key in ((pk, name) for name in brands) if key in footers), None)


def unsubscribe_label(footer: MailFooter | None, language_code: str) -> str:
    """The footer's own text, else the default of the body language (English for any other)."""
    if footer is not None and footer.unsubscribe_label:
        return footer.unsubscribe_label
    return UNSUBSCRIBE_LABELS.get((language_code or "").lower(), UNSUBSCRIBE_LABELS["en"])


def unsubscribe_link(link: str, label: str) -> str:
    return f'<a href="{escape(link)}" style="{UNSUBSCRIBE_LINK_STYLE}">{escape(label)}</a>'


def unsubscribe_paragraph(link: str, label: str) -> str:
    """The link appended below a footer without `{{ unsubscribe }}` (or below the legal text alone)."""
    return f'<p style="{UNSUBSCRIBE_PARAGRAPH_STYLE}">{unsubscribe_link(link, label)}</p>'


def paragraphs(text: str) -> str:
    """Plain text as escaped HTML paragraphs: blank lines split, single newlines become `<br>`."""
    parts = (part for part in text.split("\n\n") if part.strip())
    return "".join(f"<p>{escape(part).replace(chr(10), '<br>')}</p>" for part in parts)


def render(footer: MailFooter, legal_text: str, link: str = "", label: str = "") -> str:
    """The footer HTML with the legal text in place of `{{ legal }}` (nothing when the call carried none) and the
    unsubscribe link in place of `{{ unsubscribe }}`, else appended below; without a link the placeholder goes."""
    html = footer.html.replace(LEGAL_PLACEHOLDER, paragraphs(legal_text.strip()), 1)
    if UNSUBSCRIBE_PLACEHOLDER in html:
        return html.replace(UNSUBSCRIBE_PLACEHOLDER, unsubscribe_link(link, label) if link else "", 1)
    return html + (unsubscribe_paragraph(link, label) if link else "")


def to_text(html: str) -> str:
    """The plain-text part of a footer: blocks become lines, links `text <url>`, images dropped."""
    parser = _TextParser()
    parser.feed(html)
    parser.close()
    lines = (" ".join(line.split()) for line in "".join(parser.parts).split("\n"))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


class _PlaceholderParser(HTMLParser):
    _NAMES = (LEGAL_PLACEHOLDER, UNSUBSCRIBE_PLACEHOLDER)

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_text = dict.fromkeys(self._NAMES, 0)
        self.in_attributes = dict.fromkeys(self._NAMES, 0)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name in self._NAMES:
            self.in_attributes[name] += sum((value or "").count(name) for _, value in attrs)

    def handle_data(self, data: str) -> None:
        for name in self._NAMES:
            self.in_text[name] += data.count(name)


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
