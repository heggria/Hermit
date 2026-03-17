"""Tests for FlattenDict — nested payload template rendering."""

from __future__ import annotations

from hermit.plugins.builtin.hooks.webhook.server import FlattenDict


class TestFlattenDict:
    def _render(self, template: str, payload: dict) -> str:
        return FlattenDict(payload).render(template)

    def test_top_level_key(self) -> None:
        assert self._render("{action}", {"action": "opened"}) == "opened"

    def test_nested_dot_access(self) -> None:
        result = self._render(
            "{pull_request.title}",
            {"pull_request": {"title": "Fix bug", "number": 42}},
        )
        assert result == "Fix bug"

    def test_deeply_nested(self) -> None:
        result = self._render(
            "{repository.owner.login}",
            {"repository": {"owner": {"login": "octocat"}}},
        )
        assert result == "octocat"

    def test_missing_top_key_returns_placeholder(self) -> None:
        result = self._render("{missing_key}", {"action": "opened"})
        assert result == "{missing_key}"

    def test_missing_nested_key_returns_placeholder(self) -> None:
        result = self._render(
            "{pull_request.body}",
            {"pull_request": {"title": "Hello"}},
        )
        assert result == "{pull_request.body}"

    def test_mixed_template(self) -> None:
        payload = {
            "action": "opened",
            "repository": {"full_name": "org/repo"},
            "pull_request": {"title": "My PR"},
        }
        tpl = "Event: {action} on {repository.full_name} — PR: {pull_request.title}"
        result = self._render(tpl, payload)
        assert result == "Event: opened on org/repo — PR: My PR"

    def test_non_dict_intermediate_returns_placeholder(self) -> None:
        result = self._render(
            "{pull_request.title}",
            {"pull_request": "not-a-dict"},
        )
        assert result == "{pull_request.title}"

    def test_none_value_at_leaf_returns_placeholder(self) -> None:
        # None values are omitted from flat dict, so placeholder is preserved
        result = self._render("{pr.body}", {"pr": {"body": None}})
        assert result == "{pr.body}"

    def test_int_value_stringified(self) -> None:
        result = self._render("{pr.number}", {"pr": {"number": 123}})
        assert result == "123"

    def test_empty_payload_returns_all_placeholders(self) -> None:
        result = self._render("{action} on {repo.name}", {})
        assert result == "{action} on {repo.name}"
