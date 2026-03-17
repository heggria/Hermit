from __future__ import annotations

import json

from hermit.plugins.builtin.tools.web_tools import search


def test_search_helpers_cover_parser_extract_and_instant_answer(monkeypatch) -> None:
    assert (
        search._extract_real_url("/l/?kh=-1&uddg=https%3A%2F%2Fexample.com")
        == "https://example.com"
    )
    assert search._extract_real_url("https://plain.example") == "https://plain.example"
    assert search._extract_real_url("/relative") == "/relative"

    parser = search._DDGLiteParser()
    parser.feed(
        """
<a class="result-link" href="/l/?uddg=https%3A%2F%2Fexample.com">Example Title</a>
<td class="result-snippet">Snippet text</td>
""".strip()
    )
    assert parser.results == [
        {
            "href": "/l/?uddg=https%3A%2F%2Fexample.com",
            "title": "Example Title",
            "snippet": "Snippet text",
        }
    ]

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "AbstractText": "A concise summary.",
                    "AbstractSource": "Example Source",
                    "AbstractURL": "https://example.com/source",
                    "Answer": "42",
                    "Definition": "The answer.",
                }
            ).encode("utf-8")

    monkeypatch.setattr(search.urllib.request, "urlopen", lambda req, timeout=0: _FakeResponse())
    instant = search._ddg_instant_answer("life meaning")
    assert "Example Source" in instant
    assert "42" in instant
    assert "The answer." in instant
