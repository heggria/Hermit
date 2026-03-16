from __future__ import annotations

from pathlib import Path

from hermit.runtime.capability.contracts.base import HookEvent, SubagentSpec
from hermit.runtime.capability.contracts.hooks import HooksEngine
from hermit.runtime.capability.contracts.skills import SkillDefinition, load_skills
from hermit.runtime.capability.registry.manager import PluginManager
from hermit.runtime.capability.registry.tools import ToolRegistry


def test_plugin_manager_loads_declarative_plugin(tmp_path: Path) -> None:
    """A plugin with only skills/ and rules/ directories (no Python entry)."""
    plugin_dir = tmp_path / "plugins" / "code-review"
    (plugin_dir / "skills" / "code-review").mkdir(parents=True)
    (plugin_dir / "skills" / "code-review" / "SKILL.md").write_text(
        "# Code Review\nReview code.", encoding="utf-8"
    )
    (plugin_dir / "rules").mkdir()
    (plugin_dir / "rules" / "standards.md").write_text("Always write tests.", encoding="utf-8")
    (plugin_dir / "plugin.toml").write_text(
        '[plugin]\nname = "code-review"\nversion = "1.0.0"\ndescription = "Review"\n',
        encoding="utf-8",
    )

    pm = PluginManager()
    pm.discover_and_load(tmp_path / "plugins")

    assert len(pm.manifests) == 1
    assert pm.manifests[0].name == "code-review"
    assert len(pm._all_skills) == 1
    assert "Always write tests." in pm._all_rules_parts[0]


def test_plugin_manager_setup_tools_registers_subagent_delegation(tmp_path: Path) -> None:
    pm = PluginManager()
    pm._all_subagents.append(
        SubagentSpec(
            name="researcher",
            description="Research things",
            system_prompt="You are a researcher.",
        )
    )

    registry = ToolRegistry()
    pm.setup_tools(registry)

    tool = registry.get("delegate_researcher")
    assert tool is not None
    assert "researcher" in tool.description


def test_plugin_manager_build_system_prompt_includes_all_parts(tmp_path: Path) -> None:
    pm = PluginManager()
    pm._all_rules_parts.append('<rule path="test.md">\nAlways be nice.\n</rule>')
    pm._all_skills.append(
        SkillDefinition(
            name="search",
            description="Search the web",
            path=tmp_path,
            content="Search instructions here",
        )
    )
    pm.hooks.register(HookEvent.SYSTEM_PROMPT, lambda: "<memory>test</memory>")

    prompt = pm.build_system_prompt("base prompt here")

    assert "base prompt here" in prompt
    assert "Always be nice." in prompt
    assert '<skill name="search">Search the web</skill>' in prompt
    assert "read_skill" in prompt
    assert "<memory>test</memory>" in prompt


def test_plugin_manager_build_system_prompt_preloads_skills(tmp_path: Path) -> None:
    pm = PluginManager()
    pm._all_skills.append(
        SkillDefinition(
            name="format-rules",
            description="Formatting rules",
            path=tmp_path,
            content="Use bold for headings.",
        )
    )
    pm._all_skills.append(
        SkillDefinition(
            name="other",
            description="Other skill",
            path=tmp_path,
            content="Other instructions.",
        )
    )

    prompt = pm.build_system_prompt("base", preloaded_skills=["format-rules"])

    assert '<skill_content name="format-rules">' in prompt
    assert "Use bold for headings." in prompt
    assert '<skill name="other">Other skill</skill>' in prompt
    assert (
        "format-rules" not in prompt.split("</available_skills>")[0].split("</skill_content>")[-1]
    )


def test_plugin_manager_setup_tools_registers_read_skill(tmp_path: Path) -> None:
    pm = PluginManager()
    pm._all_skills.append(
        SkillDefinition(
            name="my-skill",
            description="A skill",
            path=tmp_path,
            content="Full instructions.",
        )
    )

    registry = ToolRegistry()
    pm.setup_tools(registry)

    tool = registry.get("read_skill")
    assert tool is not None
    assert "read_skill" == tool.name


def test_read_skill_handler_returns_content(tmp_path: Path) -> None:
    pm = PluginManager()
    pm._all_skills.append(
        SkillDefinition(
            name="test-skill",
            description="Test",
            path=tmp_path,
            content="Detailed instructions for the agent.",
        )
    )

    result = pm._read_skill_handler({"name": "test-skill"})
    assert '<skill_content name="test-skill">' in result
    assert "Detailed instructions for the agent." in result


def test_read_skill_handler_returns_error_for_unknown(tmp_path: Path) -> None:
    pm = PluginManager()
    pm._all_skills.append(
        SkillDefinition(
            name="known",
            description="Known skill",
            path=tmp_path,
            content="X",
        )
    )

    result = pm._read_skill_handler({"name": "unknown"})
    assert "not found" in result
    assert "known" in result


def test_read_skill_tool_registered_when_skills_exist(tmp_path: Path) -> None:
    pm = PluginManager()
    pm._all_skills.append(
        SkillDefinition(
            name="code-review",
            description="Review code",
            path=tmp_path,
            content="Review instructions",
        )
    )

    registry = ToolRegistry()
    pm.setup_tools(registry)

    tool = registry.get("read_skill")
    assert tool is not None
    assert "code-review" in tool.input_schema["properties"]["name"]["enum"]


def test_read_skill_tool_not_registered_when_no_skills() -> None:
    pm = PluginManager()
    registry = ToolRegistry()
    pm.setup_tools(registry)

    with __import__("pytest").raises(KeyError):
        registry.get("read_skill")


def test_read_skill_handler_unknown_name(tmp_path: Path) -> None:
    pm = PluginManager()
    pm._all_skills.append(
        SkillDefinition(name="real-skill", description="Exists", path=tmp_path, content="body")
    )

    result = pm._read_skill_handler({"name": "nonexistent"})
    assert "not found" in result
    assert "real-skill" in result


def test_build_system_prompt_preloaded_skills_injected(tmp_path: Path) -> None:
    pm = PluginManager()
    pm._all_skills.append(
        SkillDefinition(
            name="feishu-fmt",
            description="Feishu format",
            path=tmp_path,
            content="Format rules for Feishu",
        )
    )
    pm._all_skills.append(
        SkillDefinition(
            name="code-review", description="Review", path=tmp_path, content="Review instructions"
        )
    )

    prompt = pm.build_system_prompt("base", preloaded_skills=["feishu-fmt"])

    assert '<skill_content name="feishu-fmt">' in prompt
    assert "Format rules for Feishu" in prompt
    assert '<skill name="code-review">Review</skill>' in prompt
    assert "feishu-fmt" not in prompt.split("</skill_content>")[-1].split("<available_skills>")[-1]


def test_build_system_prompt_no_preloaded_shows_all_in_catalog(tmp_path: Path) -> None:
    pm = PluginManager()
    pm._all_skills.append(
        SkillDefinition(name="s1", description="Skill one", path=tmp_path, content="body1")
    )

    prompt = pm.build_system_prompt("base")

    assert "<available_skills>" in prompt
    assert '<skill name="s1">Skill one</skill>' in prompt
    assert "<skill_content" not in prompt


def test_hooks_engine_priority_ordering() -> None:
    engine = HooksEngine()
    order: list[str] = []

    engine.register("test", lambda: order.append("second"), priority=10)
    engine.register("test", lambda: order.append("first"), priority=1)
    engine.register("test", lambda: order.append("third"), priority=20)

    engine.fire("test")

    assert order == ["first", "second", "third"]


def test_hooks_engine_fire_collects_results() -> None:
    engine = HooksEngine()
    engine.register("prompt", lambda: "part-a", priority=1)
    engine.register("prompt", lambda: "part-b", priority=2)

    results = engine.fire("prompt")
    assert results == ["part-a", "part-b"]


def test_hooks_engine_fire_with_kwargs() -> None:
    engine = HooksEngine()
    engine.register("end", lambda session_id, messages: f"ended-{session_id}")

    results = engine.fire("end", session_id="s1", messages=[])
    assert results == ["ended-s1"]


def test_hooks_engine_safe_call_filters_extra_kwargs() -> None:
    engine = HooksEngine()
    engine.register("prompt", lambda: "no-args-needed")

    results = engine.fire("prompt", extra_kwarg="should be ignored")
    assert results == ["no-args-needed"]


# ---- Skills loading tests ----


def test_load_skills_with_frontmatter(tmp_path: Path) -> None:
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        '---\nname: my-skill\ndescription: "Does things"\n---\n\nFull instructions here.',
        encoding="utf-8",
    )

    skills = load_skills(tmp_path)
    assert len(skills) == 1
    assert skills[0].name == "my-skill"
    assert skills[0].description == "Does things"
    assert skills[0].content == "Full instructions here."


def test_load_skills_without_frontmatter(tmp_path: Path) -> None:
    skill_dir = tmp_path / "legacy-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Legacy Skill\nDo legacy things.", encoding="utf-8")

    skills = load_skills(tmp_path)
    assert len(skills) == 1
    assert skills[0].name == "legacy-skill"
    assert skills[0].description == "Legacy Skill"
    assert "Do legacy things." in skills[0].content


def test_load_skills_empty_dir(tmp_path: Path) -> None:
    assert load_skills(tmp_path / "nonexistent") == []


# ---- sanitize_for_feishu tests ----


def test_sanitize_for_feishu_hr_blank_line() -> None:
    from hermit.plugins.builtin.adapters.feishu.reply import sanitize_for_feishu

    text = "some text\n---\nmore text"
    result = sanitize_for_feishu(text)
    assert "\n\n---" in result


def test_sanitize_for_feishu_preserves_valid_hr() -> None:
    from hermit.plugins.builtin.adapters.feishu.reply import sanitize_for_feishu

    text = "some text\n\n---\nmore text"
    result = sanitize_for_feishu(text)
    assert result == text


def test_sanitize_for_feishu_truncates_oversized() -> None:
    from hermit.plugins.builtin.adapters.feishu.reply import sanitize_for_feishu

    text = "x" * 30_000
    result = sanitize_for_feishu(text, locale="zh-CN")
    assert len(result.encode("utf-8")) < 28_000
    assert "已截断" in result


def test_sanitize_does_not_transform_headings_or_tables() -> None:
    """Headings and tables are now the agent's responsibility via skill instructions."""
    from hermit.plugins.builtin.adapters.feishu.reply import sanitize_for_feishu

    text = "### Sub heading\n\n| a | b |\n|---|---|\n| 1 | 2 |"
    result = sanitize_for_feishu(text)
    assert "### Sub heading" in result
    assert "| a | b |" in result


# ---- _should_use_card tests ----


def test_should_use_card_plain_text() -> None:
    from hermit.plugins.builtin.adapters.feishu.reply import should_use_card as _should_use_card

    assert not _should_use_card("好的，稍后给你结果。")
    assert not _should_use_card("收到，我来处理一下。")
    assert not _should_use_card("Hello, how can I help you today?")


def test_should_use_card_markdown_signals() -> None:
    from hermit.plugins.builtin.adapters.feishu.reply import should_use_card as _should_use_card

    assert _should_use_card("**加粗文字**")
    assert _should_use_card("# 一级标题\n内容")
    assert _should_use_card("## 二级标题")
    assert _should_use_card("- 列表项")
    assert _should_use_card("1. 有序列表")
    assert _should_use_card("---")
    assert _should_use_card("`行内代码`")
    assert _should_use_card("[链接](https://example.com)")
    assert _should_use_card("~~删除线~~")


def test_should_use_card_feishu_extensions() -> None:
    from hermit.plugins.builtin.adapters.feishu.reply import should_use_card as _should_use_card

    assert _should_use_card("<font color='red'>文字</font>")
    assert _should_use_card("<at id='all'></at>")
    assert _should_use_card("<highlight>注意</highlight>")
    assert _should_use_card("<note>说明</note>")
    assert _should_use_card("<text_tag color='violet'>标签</text_tag>")
    assert _should_use_card("<table columns={[]} data={[]}/>")
    assert _should_use_card("<row><col flex=1>内容</col></row>")
    assert _should_use_card("<feishu_image key='img_v2_xxx'/>")


def test_build_result_card_uses_schema_2_0_header_and_summary() -> None:
    from hermit.plugins.builtin.adapters.feishu.reply import build_result_card

    card = build_result_card("# 处理完成\n\n## 结果\n- 一切正常")

    assert card["schema"] == "2.0"
    assert card["config"]["update_multi"] is True
    assert card["config"]["summary"]["content"] == "处理完成"
    assert card["header"]["title"]["content"] == "处理完成"
    assert card["header"]["template"] == "green"
    assert card["body"]["elements"][0]["tag"] == "markdown"
    assert card["body"]["elements"][0]["content"] == "**结果**"
    assert card["body"]["elements"][1]["tag"] == "markdown"
    assert "- 一切正常" in card["body"]["elements"][1]["content"]


def test_build_thinking_card_uses_schema_2_0() -> None:
    from hermit.plugins.builtin.adapters.feishu.reply import build_thinking_card

    card = build_thinking_card("正在分析需求...")

    assert card["schema"] == "2.0"
    assert card["config"]["update_multi"] is True
    assert card["config"]["summary"]["content"] == "正在分析需求..."
    assert card["body"]["elements"][0]["content"] == "*正在分析需求...*"


def test_build_result_card_renders_feishu_image_tag() -> None:
    from hermit.plugins.builtin.adapters.feishu.reply import build_result_card

    card = build_result_card("说明文字\n\n<feishu_image key='img_v2_xxx'/>\n\n---\n\n后续说明")

    assert [element["tag"] for element in card["body"]["elements"]] == [
        "markdown",
        "img",
        "hr",
        "markdown",
    ]
    assert card["body"]["elements"][1]["img_key"] == "img_v2_xxx"


def test_build_result_card_adds_header_tags_for_structured_image_content() -> None:
    from hermit.plugins.builtin.adapters.feishu.reply import build_result_card

    card = build_result_card(
        "# 处理结果\n\n概览说明\n\n## 步骤\n1. 第一项\n\n<feishu_image key='img_v2_xxx'/>",
        locale="zh-CN",
    )

    assert "subtitle" in card["header"]
    assert [tag["text"]["content"] for tag in card["header"]["text_tag_list"]] == [
        "图文",
        "列表",
    ]


def test_build_result_card_uses_english_tags_when_locale_is_en_us() -> None:
    from hermit.plugins.builtin.adapters.feishu.reply import build_result_card

    card = build_result_card(
        "# Result\n\nOverview\n\n## Steps\n1. First item\n\n<feishu_image key='img_v2_xxx'/>",
        locale="en-US",
    )

    assert [tag["text"]["content"] for tag in card["header"]["text_tag_list"]] == [
        "Media",
        "List",
    ]
