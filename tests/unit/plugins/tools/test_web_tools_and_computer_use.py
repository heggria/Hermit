from __future__ import annotations

import base64
import json
import subprocess
import urllib.error
from pathlib import Path

import pytest

from hermit.plugins.builtin.tools.computer_use import actions
from hermit.plugins.builtin.tools.web_tools import fetch, search
from hermit.plugins.builtin.tools.web_tools.cache import get_cache


class _FakeResponse:
    def __init__(self, body: bytes, *, content_type: str = "text/html; charset=utf-8") -> None:
        self._body = body
        self.headers = {"Content-Type": content_type}

    def read(self, size: int | None = None) -> bytes:
        return self._body if size is None else self._body[:size]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_fetch_helpers_cover_errors_and_text_extraction(monkeypatch) -> None:
    assert fetch.handle_fetch({}) == "Error: empty URL"

    monkeypatch.setattr(
        fetch.urllib.request,
        "urlopen",
        lambda req, timeout=15: (_ for _ in ()).throw(
            urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
        ),
    )
    assert fetch.handle_fetch({"url": "example.com"}) == "HTTP error 404: Not Found"

    monkeypatch.setattr(
        fetch.urllib.request,
        "urlopen",
        lambda req, timeout=15: (_ for _ in ()).throw(urllib.error.URLError("boom")),
    )
    assert fetch.handle_fetch({"url": "https://example.com"}) == "URL error: boom"

    monkeypatch.setattr(
        fetch.urllib.request,
        "urlopen",
        lambda req, timeout=15: (_ for _ in ()).throw(RuntimeError("bad")),
    )
    assert fetch.handle_fetch({"url": "https://example.com"}) == "Fetch error: bad"

    html = b"""
    <html><body><h1>Title</h1><p>Hello</p><script>ignored</script><ul><li>Item</li></ul></body></html>
    """
    monkeypatch.setattr(
        fetch.urllib.request, "urlopen", lambda req, timeout=15: _FakeResponse(html)
    )
    result = fetch.handle_fetch({"url": "example.com", "max_length": 100})
    assert result.startswith("# Content from https://example.com")
    assert "Title" in result and "Item" in result
    assert "[truncated" not in result
    assert "[truncated at 20 chars" in fetch.handle_fetch({"url": "example.com", "max_length": 20})

    monkeypatch.setattr(
        fetch.urllib.request,
        "urlopen",
        lambda req, timeout=15: _FakeResponse(b'{"ok": true}', content_type="application/json"),
    )
    get_cache().clear()
    assert '{"ok": true}' in fetch.handle_fetch({"url": "https://example.com"})

    monkeypatch.setattr(
        fetch.urllib.request,
        "urlopen",
        lambda req, timeout=15: _FakeResponse(b"plain text", content_type="text/plain"),
    )
    get_cache().clear()
    assert "plain text" in fetch.handle_fetch({"url": "https://example.com"})

    monkeypatch.setattr(
        fetch.urllib.request,
        "urlopen",
        lambda req, timeout=15: _FakeResponse(b"<html><script>only script</script></html>"),
    )
    get_cache().clear()
    assert "could not extract text content" in fetch.handle_fetch({"url": "https://example.com"})

    assert fetch._detect_encoding("text/html; charset=gbk", b"") == "gbk"
    assert fetch._detect_encoding("", b'<meta charset="shift_jis">') == "shift_jis"
    assert (
        fetch._html_to_text("<div>hello<br>world</div><style>ignored</style>") == "hello\n\nworld"
    )
    # Markdown link rendering: absolute href → [text](url), others → plain text
    assert "](" in fetch._html_to_text('<a href="https://example.com">click</a>')
    assert "](https://example.com)" in fetch._html_to_text(
        '<a href="https://example.com">click</a>'
    )
    assert "](" not in fetch._html_to_text('<a href="/relative">link</a>')
    assert "[" not in fetch._html_to_text('<a href="/relative">link</a>')


def test_search_helpers_cover_results_parsing_and_errors(monkeypatch) -> None:
    original_instant_answer = search._ddg_instant_answer
    original_lite_search = search._ddg_lite_search

    assert search.handle_search({}) == "Error: empty query"

    monkeypatch.setattr(search, "_ddg_instant_answer", lambda query: "## Answer\n42")
    monkeypatch.setattr(
        search, "_ddg_lite_search", lambda *args, **kwargs: "## Search Results\n### 1. Example"
    )
    result = search.handle_search({"query": "life"})
    assert "## Answer" in result and "## Search Results" in result

    monkeypatch.setattr(search, "_ddg_instant_answer", lambda query: "")
    monkeypatch.setattr(
        search,
        "_ddg_lite_search",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("network")),
    )
    from hermit.plugins.builtin.tools.web_tools.cache import get_cache

    get_cache().clear()
    assert "Search error: network" in search.handle_search({"query": "life"})

    monkeypatch.setattr(search, "_ddg_lite_search", lambda *args, **kwargs: "")
    assert (
        search.handle_search({"query": "life", "search_type": "news"})
        == "No results found for: life"
    )

    class FakeParser:
        def __init__(self) -> None:
            self.results = [
                {
                    "title": "Example",
                    "snippet": "Snippet text",
                    "href": "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com",
                }
            ]

        def feed(self, html: str) -> None:
            self.html = html

    monkeypatch.setattr(search, "_DDGLiteParser", FakeParser)
    monkeypatch.setattr(search, "_ddg_instant_answer", original_instant_answer)
    monkeypatch.setattr(search, "_ddg_lite_search", original_lite_search)
    monkeypatch.setattr(
        search.urllib.request, "urlopen", lambda req, timeout=10: _FakeResponse(b"<html></html>")
    )
    lite = search._ddg_lite_search(
        "example", max_results=1, region="us-en", time_filter="day", search_type="news"
    )
    assert "## Search Results" in lite
    assert "https://example.com" in lite
    assert "Snippet text" in lite

    assert (
        search._extract_real_url("https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com")
        == "https://example.com"
    )
    assert search._extract_real_url("https://example.com") == "https://example.com"

    payload = {
        "AbstractText": "Summary",
        "AbstractSource": "Source",
        "AbstractURL": "https://source.example.com",
        "Answer": "42",
        "Definition": "Meaning",
    }
    monkeypatch.setattr(
        search.urllib.request,
        "urlopen",
        lambda req, timeout=5: _FakeResponse(
            json.dumps(payload).encode("utf-8"), content_type="application/json"
        ),
    )
    instant = search._ddg_instant_answer("life")
    assert "## Summary (Source)" in instant
    assert "## Answer" in instant
    assert "## Definition" in instant

    monkeypatch.setattr(
        search.urllib.request,
        "urlopen",
        lambda req, timeout=5: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert search._ddg_instant_answer("life") == ""


def test_computer_use_low_level_helpers() -> None:
    assert actions._applescript_string('a"b\\c') == '"a\\"b\\\\c"'
    assert actions._ok("done") == {"ok": True, "info": "done"}
    with pytest.raises(RuntimeError, match="x is required"):
        actions._require_int({}, "x")
    with pytest.raises(RuntimeError, match="x must be an integer"):
        actions._require_int({"x": "bad"}, "x")
    assert actions._require_int({"x": "3"}, "x") == 3


def test_computer_use_run_command_and_osascript(monkeypatch) -> None:
    monkeypatch.setattr(
        actions.subprocess,
        "run",
        lambda args, capture_output=True, text=True: subprocess.CompletedProcess(
            args, 0, stdout="ok", stderr=""
        ),
    )
    assert actions._run_command(["echo", "ok"]).stdout == "ok"

    monkeypatch.setattr(
        actions.subprocess,
        "run",
        lambda args, capture_output=True, text=True: subprocess.CompletedProcess(
            args, 1, stdout="", stderr="bad"
        ),
    )
    with pytest.raises(RuntimeError, match="bad"):
        actions._run_command(["echo", "bad"])

    captured: list[list[str]] = []
    monkeypatch.setattr(
        actions,
        "_run_command",
        lambda args: (
            captured.append(args) or subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        ),
    )
    actions._run_osascript("display dialog", ["hello"])
    assert captured == [["osascript", "-e", "display dialog", "hello"]]


def test_screenshot_and_pointer_actions(monkeypatch, tmp_path) -> None:
    written_paths: list[Path] = []

    def fake_run_command(args: list[str]):
        path = Path(args[-1])
        path.write_bytes(b"png")
        written_paths.append(path)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(actions, "_tool_exists", lambda name: True)
    monkeypatch.setattr(actions, "_run_command", fake_run_command)
    shot = actions.screenshot({})
    assert shot["type"] == "image"
    assert base64.b64decode(shot["source"]["data"]) == b"png"
    assert all(not path.exists() for path in written_paths)

    clicks: list[list[str]] = []
    monkeypatch.setattr(
        actions,
        "_run_command",
        lambda args: (
            clicks.append(args) or subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        ),
    )
    assert actions.click({"x": 1, "y": 2, "button": "right"}) == {
        "ok": True,
        "info": "clicked at 1,2",
    }
    assert actions.click({"x": 1, "y": 2, "double": True}) == {"ok": True, "info": "clicked at 1,2"}
    with pytest.raises(RuntimeError, match="unsupported button"):
        actions.click({"x": 1, "y": 2, "button": "weird"})

    monkeypatch.setattr(actions, "_tool_exists", lambda name: False)
    scripts: list[tuple[str, list[str] | None]] = []
    monkeypatch.setattr(
        actions,
        "_run_osascript",
        lambda script, argv=None: (
            scripts.append((script, argv))
            or subprocess.CompletedProcess([], 0, stdout="", stderr="")
        ),
    )
    assert actions.click({"x": 3, "y": 4}) == {"ok": True, "info": "click at 3,4"}
    assert actions.move({"x": 5, "y": 6}) == {"ok": True, "info": "moved to 5,6"}
    assert actions.scroll({"x": 7, "y": 8, "direction": "down", "amount": 2}) == {
        "ok": True,
        "info": "scrolled down at 7,8",
    }
    assert any("move mouse" in script for script, _ in scripts)


def test_typing_keys_screen_size_and_open_app(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(actions, "_tool_exists", lambda name: name in {actions._CLICLICK, "open"})
    monkeypatch.setattr(
        actions,
        "_run_command",
        lambda args: (
            calls.append(args) or subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        ),
    )

    assert actions.type_text({"text": "hello"}) == {"ok": True, "info": "text typed"}
    with pytest.raises(RuntimeError, match="text is required"):
        actions.type_text({})
    assert actions.press_key({"key": "cmd+shift+p"}) == {"ok": True, "info": "pressed cmd+shift+p"}
    assert actions.move({"x": 1, "y": 2}) == {"ok": True, "info": "moved to 1,2"}
    assert actions.scroll({"x": 1, "y": 2, "direction": "up", "amount": 3}) == {
        "ok": True,
        "info": "scrolled up at 1,2",
    }
    assert actions.open_app({"app_name": "Finder"}) == {"ok": True, "info": "opened Finder"}

    with pytest.raises(RuntimeError, match="unsupported direction"):
        actions.scroll({"x": 1, "y": 2, "direction": "diagonal", "amount": 3})

    monkeypatch.setattr(actions, "_tool_exists", lambda name: False)
    scripts: list[tuple[str, list[str] | None]] = []
    monkeypatch.setattr(
        actions,
        "_run_osascript",
        lambda script, argv=None: (
            scripts.append((script, argv))
            or subprocess.CompletedProcess([], 0, stdout="0, 0, 1440, 900", stderr="")
        ),
    )

    assert actions.type_text({"text": "hello"}) == {"ok": True, "info": "text typed"}
    assert actions.press_key({"key": "enter"}) == {"ok": True, "info": "pressed enter"}
    assert actions.press_key({"key": "a"}) == {"ok": True, "info": "pressed a"}
    with pytest.raises(RuntimeError, match="unsupported modifier"):
        actions.press_key({"key": "weird+a"})
    with pytest.raises(RuntimeError, match="unsupported key"):
        actions.press_key({"key": "unknownkey"})
    assert actions.get_screen_size({}) == {"width": 1440, "height": 900}

    monkeypatch.setattr(
        actions,
        "_run_osascript",
        lambda script, argv=None: subprocess.CompletedProcess([], 0, stdout="bad", stderr=""),
    )
    with pytest.raises(RuntimeError, match="unexpected screen bounds"):
        actions.get_screen_size({})

    monkeypatch.setattr(actions, "_tool_exists", lambda name: False)
    with pytest.raises(RuntimeError, match="open is not available"):
        actions.open_app({"app_name": "Finder"})

    monkeypatch.setattr(actions, "_tool_exists", lambda name: True)
    with pytest.raises(RuntimeError, match="screencapture is not available"):
        monkeypatch.setattr(actions, "_tool_exists", lambda name: False)
        actions.screenshot({})


def test_computer_use_errors_explain_accessibility_and_cliclick(monkeypatch) -> None:
    monkeypatch.setattr(actions, "_tool_exists", lambda name: False)
    permission_error = (
        "System Events got an error: osascript is not permitted to send keystrokes. (1002)"
    )
    monkeypatch.setattr(
        actions,
        "_run_osascript",
        lambda script, argv=None: (_ for _ in ()).throw(RuntimeError(permission_error)),
    )

    with pytest.raises(RuntimeError, match="Grant Accessibility access") as type_exc:
        actions.type_text({"text": "hello"})
    assert "cliclick" in str(type_exc.value)

    with pytest.raises(RuntimeError, match="Grant Accessibility access") as key_exc:
        actions.press_key({"key": "enter"})
    assert "cliclick" not in str(key_exc.value)


def test_computer_use_errors_explain_osascript_fallback(monkeypatch) -> None:
    monkeypatch.setattr(actions, "_tool_exists", lambda name: False)
    monkeypatch.setattr(
        actions,
        "_run_osascript",
        lambda script, argv=None: (_ for _ in ()).throw(RuntimeError("Application is not running")),
    )

    with pytest.raises(RuntimeError, match="fell back to osascript/System Events"):
        actions.type_text({"text": "hello"})
