"""Additional coverage tests for src/hermit/apps/companion/menubar.py

Targets the ~10 missed statements: _parse_args, main() edge cases,
_t helper, _about_message edge cases.
"""

from __future__ import annotations

from hermit.apps.companion import menubar

# ---------------------------------------------------------------------------
# _parse_args
# ---------------------------------------------------------------------------


class TestParseArgs:
    def test_default_values(self) -> None:
        args = menubar._parse_args([])
        assert args.adapter == "feishu"
        assert args.profile is None
        assert args.base_dir is None

    def test_custom_adapter(self) -> None:
        args = menubar._parse_args(["--adapter", "slack"])
        assert args.adapter == "slack"

    def test_custom_profile(self) -> None:
        args = menubar._parse_args(["--profile", "dev"])
        assert args.profile == "dev"

    def test_custom_base_dir(self) -> None:
        args = menubar._parse_args(["--base-dir", "/tmp/hermit-test"])
        assert args.base_dir == "/tmp/hermit-test"

    def test_all_options(self) -> None:
        args = menubar._parse_args(
            [
                "--adapter",
                "telegram",
                "--profile",
                "prod",
                "--base-dir",
                "~/custom",
            ]
        )
        assert args.adapter == "telegram"
        assert args.profile == "prod"
        assert args.base_dir == "~/custom"


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


class TestMain:
    def test_non_darwin_returns_1(self, monkeypatch) -> None:
        monkeypatch.setattr(menubar.sys, "platform", "linux")
        result = menubar.main(argv=[])
        assert result == 1

    def test_missing_rumps_returns_1(self, monkeypatch) -> None:
        monkeypatch.setattr(menubar.sys, "platform", "darwin")
        monkeypatch.setattr(menubar, "rumps", None)
        monkeypatch.setattr(menubar, "_import_error", ImportError("no rumps"))
        result = menubar.main(argv=[])
        assert result == 1

    def test_missing_rumps_no_import_error(self, monkeypatch) -> None:
        monkeypatch.setattr(menubar.sys, "platform", "darwin")
        monkeypatch.setattr(menubar, "rumps", None)
        monkeypatch.setattr(menubar, "_import_error", None)
        result = menubar.main(argv=[])
        assert result == 1


# ---------------------------------------------------------------------------
# _t helper
# ---------------------------------------------------------------------------


class TestTranslationHelper:
    def test_returns_string(self) -> None:
        result = menubar._t("menubar.title")
        assert isinstance(result, str)
        assert len(result) > 0
