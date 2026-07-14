from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any

ALLOWED_SCIENTIFIC_TAGS = frozenset({"sub", "sup", "i", "em", "strong", "b", "br"})
_PAIRED_TAG_RE = re.compile(
    r"<(sub|sup|i|em|strong|b)>(.*?)</\1>",
    flags=re.IGNORECASE | re.DOTALL,
)
_NUMBER_RE = re.compile(r"(?<![\w])(?:\d+(?:[.,]\d+)*%?)(?![\w])")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")


class _ScientificHTMLSanitizer(HTMLParser):
    """Escape all content except a tiny, attribute-free scientific markup allowlist."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.casefold()
        if lowered in ALLOWED_SCIENTIFIC_TAGS:
            self.parts.append(f"<{lowered}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.casefold()
        if lowered == "br":
            self.parts.append("<br>")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered in ALLOWED_SCIENTIFIC_TAGS and lowered != "br":
            self.parts.append(f"</{lowered}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        return

    def unknown_decl(self, data: str) -> None:
        return


def safe_scientific_html(value: Any) -> str:
    """Render safe scientific text while preserving sub/sup/italic tags only."""
    raw = html.unescape(str(value or ""))
    parser = _ScientificHTMLSanitizer()
    parser.feed(raw)
    parser.close()
    return "".join(parser.parts)


class _PlainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def strip_scientific_markup(value: Any) -> str:
    parser = _PlainTextParser()
    parser.feed(html.unescape(str(value or "")))
    parser.close()
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip()


def protect_scientific_markup(value: Any) -> tuple[str, dict[str, str]]:
    """Replace supported scientific markup fragments with immutable placeholders."""
    text = html.unescape(str(value or ""))
    mapping: dict[str, str] = {}
    counter = 0

    # Repeat to support simple nested fragments without exposing arbitrary HTML.
    while True:
        match = _PAIRED_TAG_RE.search(text)
        if not match:
            break
        token = f"[[PDI_SCI_{counter:03d}]]"
        mapping[token] = match.group(0)
        text = text[: match.start()] + token + text[match.end() :]
        counter += 1

    text = re.sub(r"<br\s*/?>", "[[PDI_BR]]", text, flags=re.IGNORECASE)
    if "[[PDI_BR]]" in text:
        mapping["[[PDI_BR]]"] = "<br>"
    return text, mapping


def restore_scientific_markup(value: Any, mapping: dict[str, str]) -> str:
    text = str(value or "")
    for token, fragment in mapping.items():
        text = text.replace(token, fragment)
    return text


def placeholders_preserved(value: Any, mapping: dict[str, str]) -> bool:
    text = str(value or "")
    return all(token in text for token in mapping)


def number_tokens(value: Any) -> list[str]:
    return _NUMBER_RE.findall(strip_scientific_markup(value))


def contains_cjk(value: Any) -> bool:
    return bool(_CJK_RE.search(str(value or "")))
