from __future__ import annotations

from pathlib import Path

from hermit import autostart


def test_build_plist_uses_adapter_flag() -> None:
    plist = autostart._build_plist(Path("/tmp/hermit"), "feishu", Path("/tmp/logs"))

    assert "<string>serve</string>" in plist
    assert "<string>--adapter</string>" in plist
    assert "<string>feishu</string>" in plist

