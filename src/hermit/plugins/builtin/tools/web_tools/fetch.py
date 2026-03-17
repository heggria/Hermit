"""URL content fetcher — extracts readable text from web pages."""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from html.parser import HTMLParser
from typing import Any

from hermit.infra.system.i18n import resolve_locale, tr
from hermit.runtime.control.lifecycle.budgets import get_runtime_budget

_MAX_CONTENT_LENGTH = 50_000


def _t(message_key: str, *, default: str | None = None, **kwargs: object) -> str:
    return tr(message_key, locale=resolve_locale(), default=default, **kwargs)


def handle_fetch(payload: dict[str, Any]) -> str:
    url = str(payload.get("url", "")).strip()
    if not url:
        return _t("tools.web.fetch.error.empty_url")

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    max_length = min(int(payload.get("max_length", 20000)), _MAX_CONTENT_LENGTH)

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            },
        )
        with urllib.request.urlopen(
            req, timeout=get_runtime_budget().provider_read_timeout
        ) as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read(200_000)
    except urllib.error.HTTPError as exc:
        return _t("tools.web.fetch.error.http", code=exc.code, reason=exc.reason)
    except urllib.error.URLError as exc:
        return _t("tools.web.fetch.error.url", reason=exc.reason)
    except Exception as exc:
        return _t("tools.web.fetch.error.fetch", error=exc)

    encoding = _detect_encoding(content_type, raw)
    try:
        html = raw.decode(encoding, errors="replace")
    except (LookupError, UnicodeDecodeError):
        html = raw.decode("utf-8", errors="replace")

    if "json" in content_type or "text/plain" in content_type:
        text = html.strip()
    else:
        text = _html_to_text(html)

    if not text.strip():
        return _t("tools.web.fetch.no_content", url=url)

    if len(text) > max_length:
        text = text[:max_length] + _t(
            "tools.web.fetch.truncated",
            max_length=max_length,
            full_length=len(text),
        )

    return _t("tools.web.fetch.content.title", url=url, text=text)


def _detect_encoding(content_type: str, raw: bytes) -> str:
    ct_match = re.search(r"charset=([^\s;]+)", content_type, re.IGNORECASE)
    if ct_match:
        return ct_match.group(1).strip("'\"")

    meta_match = re.search(
        rb'<meta[^>]+charset=["\']?([^"\'\s;>]+)',
        raw[:4096],
        re.IGNORECASE,
    )
    if meta_match:
        return meta_match.group(1).decode("ascii", errors="ignore")

    return "utf-8"


def _html_to_text(html: str) -> str:
    parser = _ReadableTextExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    return parser.get_text()


class _ReadableTextExtractor(HTMLParser):
    _SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "iframe", "object", "embed"})
    _BLOCK_TAGS = frozenset(
        {
            "p",
            "div",
            "br",
            "hr",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "li",
            "tr",
            "blockquote",
            "pre",
            "section",
            "article",
            "header",
            "footer",
            "main",
            "nav",
            "aside",
            "figure",
            "figcaption",
            "dt",
            "dd",
            "table",
            "thead",
            "tbody",
        }
    )
    _HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0
        self._tag_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        self._tag_stack.append(tag)

        if tag in self._BLOCK_TAGS and not self._skip_depth:
            self._parts.append("\n")
        if tag in self._HEADING_TAGS and not self._skip_depth:
            level = int(tag[1])
            self._parts.append("\n" + "#" * level + " ")
        if tag == "li" and not self._skip_depth:
            self._parts.append("- ")
        if tag == "br" and not self._skip_depth:
            self._parts.append("\n")
        if tag == "a" and not self._skip_depth:
            href = dict(attrs).get("href", "")
            if href and href.startswith(("http://", "https://")):
                self._parts.append("[")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._tag_stack and self._tag_stack[-1] == tag:
            self._tag_stack.pop()

        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

        if tag in self._BLOCK_TAGS and not self._skip_depth:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = data.strip()
        if text:
            self._parts.append(text + " ")

    def get_text(self) -> str:
        raw = "".join(self._parts)
        lines = [line.strip() for line in raw.splitlines()]
        cleaned: list[str] = []
        prev_empty = False
        for line in lines:
            if not line:
                if not prev_empty:
                    cleaned.append("")
                prev_empty = True
            else:
                cleaned.append(line)
                prev_empty = False
        return "\n".join(cleaned).strip()
