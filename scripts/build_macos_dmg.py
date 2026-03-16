#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from hermit import __version__
from hermit.apps.companion.appbundle import app_name, install_app_bundle


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value).strip("-")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a macOS DMG for the Hermit menu app.")
    parser.add_argument("--adapter", default="feishu", help="Adapter to manage.")
    parser.add_argument("--profile", default=None, help="Optional HERMIT_PROFILE override.")
    parser.add_argument("--base-dir", default=None, help="Optional HERMIT_BASE_DIR override.")
    parser.add_argument(
        "--out-dir",
        default="dist",
        help="Directory where the DMG should be written.",
    )
    parser.add_argument(
        "--volume-name",
        default=None,
        help="Optional mounted DMG volume name.",
    )
    return parser.parse_args(argv)


def build_dmg(
    *,
    adapter: str,
    profile: str | None,
    base_dir: Path | None,
    out_dir: Path,
    volume_name: str | None = None,
) -> Path:
    if sys.platform != "darwin":
        raise RuntimeError("DMG build is only supported on macOS.")
    if shutil.which("hdiutil") is None:
        raise RuntimeError("hdiutil not found. DMG build requires macOS system tools.")

    resolved_base_dir = base_dir.expanduser() if base_dir else None
    resolved_app_name = app_name(resolved_base_dir)
    resolved_volume_name = volume_name or f"{resolved_app_name} {__version__}"
    out_dir.mkdir(parents=True, exist_ok=True)
    dmg_name = f"{_safe_name(resolved_app_name)}-{__version__}.dmg"
    dmg_path = out_dir / dmg_name

    with tempfile.TemporaryDirectory(prefix="hermit-dmg-") as temp_dir:
        stage_dir = Path(temp_dir) / "stage"
        stage_dir.mkdir(parents=True, exist_ok=True)
        bundle_target = stage_dir / f"{resolved_app_name}.app"
        install_app_bundle(
            target=bundle_target,
            adapter=adapter,
            profile=profile,
            base_dir=resolved_base_dir,
        )
        applications_link = stage_dir / "Applications"
        if not applications_link.exists():
            applications_link.symlink_to("/Applications")

        if dmg_path.exists():
            dmg_path.unlink()

        subprocess.run(
            [
                "hdiutil",
                "create",
                "-volname",
                resolved_volume_name,
                "-srcfolder",
                str(stage_dir),
                "-ov",
                "-format",
                "UDZO",
                str(dmg_path),
            ],
            check=True,
        )

    return dmg_path


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    base_dir = Path(args.base_dir).expanduser() if args.base_dir else None
    dmg_path = build_dmg(
        adapter=args.adapter,
        profile=args.profile,
        base_dir=base_dir,
        out_dir=Path(args.out_dir).expanduser(),
        volume_name=args.volume_name,
    )
    print(dmg_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
