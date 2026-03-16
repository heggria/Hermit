from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from hermit.builtin.feishu import hooks as feishu_hooks
from hermit.builtin.feishu import reaction as feishu_reaction


@pytest.mark.parametrize(
    ("client", "message_id", "emoji_type"),
    [
        (None, "om_1", "OK"),
        (object(), "", "OK"),
        (object(), "om_1", ""),
    ],
)
def test_add_reaction_returns_false_for_missing_inputs(client, message_id: str, emoji_type: str) -> None:
    assert feishu_reaction.add_reaction(client, message_id, emoji_type) is False


def test_add_reaction_returns_false_when_create_raises() -> None:
    class FakeReaction:
        def create(self, _request):
            raise RuntimeError("boom")

    client = SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(message_reaction=FakeReaction())))

    assert feishu_reaction.add_reaction(client, "om_1", "OK") is False


def test_add_reaction_returns_true_when_api_succeeds() -> None:
    class FakeResp:
        def success(self):
            return True

    class FakeReaction:
        def create(self, _request):
            return FakeResp()

    client = SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(message_reaction=FakeReaction())))

    assert feishu_reaction.add_reaction(client, "om_1", "OK") is True


def test_send_ack_and_done_use_settings_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    settings = SimpleNamespace(
        feishu_reaction_enabled=True,
        feishu_reaction_ack="thinking",
        feishu_reaction_done="done",
    )

    monkeypatch.setattr(
        feishu_reaction,
        "add_reaction",
        lambda _client, message_id, emoji: calls.append((message_id, emoji)) or True,
    )

    feishu_reaction.send_ack(object(), "om_1", settings)
    feishu_reaction.send_done(object(), "om_1", settings)

    assert calls == [("om_1", "thinking"), ("om_1", "done")]


def test_send_ack_and_done_skip_when_disabled_or_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        feishu_reaction,
        "add_reaction",
        lambda _client, message_id, emoji: calls.append((message_id, emoji)) or True,
    )

    feishu_reaction.send_ack(
        object(),
        "om_1",
        SimpleNamespace(feishu_reaction_enabled=False, feishu_reaction_ack="eyes"),
    )
    feishu_reaction.send_done(
        object(),
        "om_1",
        SimpleNamespace(feishu_reaction_enabled=True, feishu_reaction_done=""),
    )

    assert calls == []


def test_send_ack_and_done_use_environment_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    monkeypatch.setenv("HERMIT_FEISHU_REACTION_ENABLED", "true")
    monkeypatch.setenv("HERMIT_FEISHU_REACTION_ACK", "FIRE")
    monkeypatch.setenv("HERMIT_FEISHU_REACTION_DONE", "OK")
    monkeypatch.setattr(
        feishu_reaction,
        "add_reaction",
        lambda _client, message_id, emoji: calls.append((message_id, emoji)) or True,
    )

    feishu_reaction.send_ack(object(), "om_1")
    feishu_reaction.send_done(object(), "om_1")

    assert calls == [("om_1", "FIRE"), ("om_1", "OK")]


def test_send_done_returns_early_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        feishu_reaction,
        "add_reaction",
        lambda _client, message_id, emoji: calls.append((message_id, emoji)) or True,
    )

    feishu_reaction.send_done(
        object(),
        "om_1",
        SimpleNamespace(feishu_reaction_enabled=False, feishu_reaction_done="OK"),
    )

    assert calls == []


def test_send_ack_skips_when_emoji_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        feishu_reaction,
        "add_reaction",
        lambda _client, message_id, emoji: calls.append((message_id, emoji)) or True,
    )

    feishu_reaction.send_ack(
        object(),
        "om_1",
        SimpleNamespace(feishu_reaction_enabled=True, feishu_reaction_ack=""),
    )

    assert calls == []


def test_dispatch_result_sends_card_when_card_is_preferred(monkeypatch: pytest.MonkeyPatch) -> None:
    sent_cards: list[tuple[str, dict]] = []

    monkeypatch.setattr(feishu_hooks, "build_lark_client", lambda settings=None: object())
    monkeypatch.setattr("hermit.builtin.feishu.reply._should_use_card", lambda text: True)
    monkeypatch.setattr("hermit.builtin.feishu.reply.build_result_card", lambda text: {"text": text})
    monkeypatch.setattr(
        "hermit.builtin.feishu.reply.send_card",
        lambda _client, chat_id, card: sent_cards.append((chat_id, card)) or "om_card",
    )

    feishu_hooks._on_dispatch_result(
        source="scheduler",
        title="日报",
        result_text="今天完成了 3 个任务",
        notify={"feishu_chat_id": "oc_1"},
    )

    assert sent_cards == [("oc_1", {"text": "# 日报\n\n今天完成了 3 个任务"})]


def test_dispatch_result_sends_text_for_failures_when_card_not_needed(monkeypatch: pytest.MonkeyPatch) -> None:
    sent_texts: list[tuple[str, str]] = []

    monkeypatch.setattr(feishu_hooks, "build_lark_client", lambda settings=None: object())
    monkeypatch.setattr("hermit.builtin.feishu.reply._should_use_card", lambda text: False)
    monkeypatch.setattr(
        "hermit.builtin.feishu.reply.send_text_message",
        lambda _client, chat_id, text: sent_texts.append((chat_id, text)) or "om_text",
    )

    feishu_hooks._on_dispatch_result(
        source="scheduler",
        title="日报",
        result_text="部分任务失败",
        success=False,
        error="network timeout",
        notify={"feishu_chat_id": "oc_1"},
    )

    assert sent_texts == [("oc_1", "# 日报 (failed)\n\n**Error:** network timeout\n\n部分任务失败")]


def test_dispatch_result_ignores_missing_chat_and_swallows_send_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    exception_calls: list[str] = []

    monkeypatch.setattr(feishu_hooks, "build_lark_client", lambda settings=None: object())
    monkeypatch.setattr("hermit.builtin.feishu.reply._should_use_card", lambda text: True)
    monkeypatch.setattr("hermit.builtin.feishu.reply.build_result_card", lambda text: {"text": text})
    monkeypatch.setattr(
        "hermit.builtin.feishu.reply.send_card",
        lambda _client, chat_id, card: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(feishu_hooks._log, "exception", lambda message, chat_id: exception_calls.append(chat_id))

    feishu_hooks._on_dispatch_result(result_text="skip", notify={})
    feishu_hooks._on_dispatch_result(result_text="boom", notify={"feishu_chat_id": "oc_1"})

    assert exception_calls == ["oc_1"]


def test_build_react_tool_validates_required_fields_and_runtime_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = feishu_hooks._build_react_tool()

    assert tool.handler({"emoji_type": "THUMBSUP"}) == {"success": False, "error": "message_id is required"}
    assert tool.handler({"message_id": "om_1"}) == {"success": False, "error": "emoji_type is required"}

    monkeypatch.setattr(
        feishu_hooks,
        "build_lark_client",
        lambda settings=None: (_ for _ in ()).throw(RuntimeError("missing credentials")),
    )
    assert tool.handler({"message_id": "om_1", "emoji_type": "THUMBSUP"}) == {
        "success": False,
        "error": "missing credentials",
    }


def test_build_react_tool_passes_emoji_type_through_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(feishu_hooks, "build_lark_client", lambda settings=None: object())
    monkeypatch.setattr(
        feishu_hooks,
        "add_reaction",
        lambda _client, message_id, emoji_type: calls.append((message_id, emoji_type)) or True,
    )

    result = feishu_hooks._build_react_tool().handler({"message_id": "om_1", "emoji_type": "THUMBSUP"})

    assert result == {"success": True, "emoji_type": "THUMBSUP", "message_id": "om_1"}
    assert calls == [("om_1", "THUMBSUP")]


def test_resolve_emoji_type_returns_trimmed_value_without_alias_mapping() -> None:
    assert feishu_reaction.resolve_emoji_type("  party  ") == "party"
    assert feishu_reaction.resolve_emoji_type("THUMBSUP") == "THUMBSUP"


def test_resolve_emoji_type_randomly_selects_from_candidate_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake_choice(options: list[str]) -> str:
        seen.append(options)
        return options[-1]

    monkeypatch.setattr(feishu_reaction.random, "choice", fake_choice)

    assert feishu_reaction.resolve_emoji_type(" PARTY | APPLAUSE | WOW ") == "WOW"
    assert seen == [["PARTY", "APPLAUSE", "WOW"]]


def test_build_react_tool_selects_random_candidate_from_emoji_type_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(feishu_hooks, "build_lark_client", lambda settings=None: object())
    monkeypatch.setattr(
        feishu_hooks,
        "add_reaction",
        lambda _client, message_id, emoji_type: calls.append((message_id, emoji_type)) or True,
    )
    monkeypatch.setattr(feishu_reaction.random, "choice", lambda options: options[1])

    result = feishu_hooks._build_react_tool().handler(
        {"message_id": "om_1", "emoji_type": "PARTY | APPLAUSE | WOW"}
    )

    assert result == {"success": True, "emoji_type": "APPLAUSE", "message_id": "om_1"}
    assert calls == [("om_1", "APPLAUSE")]


def test_feishu_emoji_reaction_skill_includes_varied_choices_and_new_scenarios() -> None:
    skill_path = (
        Path(__file__).resolve().parents[1]
        / "hermit"
        / "builtin"
        / "feishu"
        / "skills"
        / "feishu-emoji-reaction"
        / "SKILL.md"
    )
    content = skill_path.read_text(encoding="utf-8")

    assert "pass the whole candidate list with ` | ` separators" in content
    assert "The user is excited or looking forward to something" in content
    assert "The user says they are stuck, nervous, or unsure" in content
    assert "The user is brainstorming or sharing a fresh idea" in content
    assert "The user says the answer is wrong, calls the assistant dumb, or sounds annoyed" in content
    assert "`EMBARRASSED | HAMMER`" in content
