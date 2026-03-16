from __future__ import annotations

import argparse
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from hermit import __version__
from hermit.apps.companion.control import hermit_base_dir
from hermit.infra.system.i18n import tr

APP_NAME = "Hermit"
BUNDLE_ID = "com.hermit.menubar"


def _t(message_key: str, default: str | None = None, **kwargs: object) -> str:
    return tr(message_key, default=default, **kwargs)  # type: ignore[arg-type]


def _base_dir_slug(base_dir: Path) -> str:
    resolved = base_dir.expanduser()
    default_base_dir = Path.home() / ".hermit"
    if resolved == default_base_dir:
        return ""
    name = resolved.name
    if name.startswith(".hermit-"):
        name = name[len(".hermit-") :]
    elif name == ".hermit":
        return ""
    elif name.startswith("."):
        name = name[1:]
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return slug or "custom"


def app_name(base_dir: Path | None = None) -> str:
    resolved = (base_dir or hermit_base_dir()).expanduser()
    slug = _base_dir_slug(resolved)
    if not slug:
        return APP_NAME
    return f"{APP_NAME} {slug.title()}"


def bundle_id(base_dir: Path | None = None) -> str:
    resolved = (base_dir or hermit_base_dir()).expanduser()
    slug = _base_dir_slug(resolved)
    if not slug:
        return BUNDLE_ID
    return f"{BUNDLE_ID}.{slug}"


def app_path(target: Path | None = None, *, base_dir: Path | None = None) -> Path:
    return (target or (Path.home() / "Applications" / f"{app_name(base_dir)}.app")).expanduser()


def _launcher_command() -> list[str]:  # pyright: ignore[reportUnusedFunction]
    companion_bin = Path(sys.executable).parent / "hermit-menubar"
    if companion_bin.exists():
        return [str(companion_bin)]
    return [sys.executable, "-m", "hermit.apps.companion.menubar"]


def _bundle_python_target() -> Path:
    return Path(sys.executable).resolve()


def _project_root() -> Path | None:
    candidate = Path(__file__).resolve().parents[2]
    if (candidate / "pyproject.toml").exists():
        return candidate
    return None


def _icon_source() -> Path | None:
    project_root = _project_root()
    if project_root is None:
        return None
    source = project_root / "docs" / "hermit-icon.svg"
    if source.exists():
        return source
    return None


def _install_bundle_icon(resources_dir: Path) -> str | None:
    source = _icon_source()
    if source is None or sys.platform != "darwin":
        return None
    if shutil.which("sips") is None or shutil.which("iconutil") is None:
        return None

    icon_name = "HermitMenu"
    iconset_dir = resources_dir / f"{icon_name}.iconset"
    if iconset_dir.exists():
        shutil.rmtree(iconset_dir)
    iconset_dir.mkdir(parents=True, exist_ok=True)

    try:
        with tempfile.TemporaryDirectory(prefix="hermit-icon-") as temp_dir:
            temp_path = Path(temp_dir)
            raster_png = temp_path / "hermit-icon.png"
            inset_png = temp_path / "hermit-icon-inset.png"
            subprocess.run(
                [
                    "sips",
                    "-s",
                    "format",
                    "png",
                    str(source),
                    "--out",
                    str(raster_png),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if not raster_png.exists():
                return None

            # Keep a consistent margin so the symbol does not appear oversized in Finder.
            subprocess.run(
                [
                    "sips",
                    "-z",
                    "992",
                    "992",
                    str(raster_png),
                    "--out",
                    str(inset_png),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                [
                    "sips",
                    "--padToHeightWidth",
                    "1024",
                    "1024",
                    str(inset_png),
                    "--out",
                    str(raster_png),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            sizes = {
                "icon_16x16.png": 16,
                "icon_16x16@2x.png": 32,
                "icon_32x32.png": 32,
                "icon_32x32@2x.png": 64,
                "icon_128x128.png": 128,
                "icon_128x128@2x.png": 256,
                "icon_256x256.png": 256,
                "icon_256x256@2x.png": 512,
                "icon_512x512.png": 512,
                "icon_512x512@2x.png": 1024,
            }
            for filename, size in sizes.items():
                subprocess.run(
                    [
                        "sips",
                        "-z",
                        str(size),
                        str(size),
                        str(raster_png),
                        "--out",
                        str(iconset_dir / filename),
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

            icns_path = resources_dir / f"{icon_name}.icns"
            if icns_path.exists():
                icns_path.unlink()
            subprocess.run(
                [
                    "iconutil",
                    "-c",
                    "icns",
                    str(iconset_dir),
                    "-o",
                    str(icns_path),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return icns_path.name
    except (OSError, subprocess.CalledProcessError):
        return None
    finally:
        if iconset_dir.exists():
            shutil.rmtree(iconset_dir, ignore_errors=True)


def install_app_bundle(
    *,
    target: Path | None = None,
    adapter: str = "feishu",
    profile: str | None = None,
    base_dir: Path | None = None,
) -> Path:
    resolved_base_dir = (base_dir or hermit_base_dir()).expanduser()
    bundle = app_path(target, base_dir=resolved_base_dir)
    contents = bundle / "Contents"
    macos_dir = contents / "MacOS"
    resources_dir = contents / "Resources"
    macos_dir.mkdir(parents=True, exist_ok=True)
    resources_dir.mkdir(parents=True, exist_ok=True)
    resolved_app_name = app_name(resolved_base_dir)
    resolved_bundle_id = bundle_id(resolved_base_dir)
    icon_file = _install_bundle_icon(resources_dir)

    info = {
        "CFBundleDisplayName": resolved_app_name,
        "CFBundleExecutable": "HermitMenu",
        "CFBundleIdentifier": resolved_bundle_id,
        "CFBundleName": resolved_app_name,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": "1",
        "LSUIElement": True,
    }
    if icon_file:
        info["CFBundleIconFile"] = icon_file
    (contents / "Info.plist").write_bytes(plistlib.dumps(info))

    env_lines = [f'export HERMIT_BASE_DIR="{resolved_base_dir}"']
    if profile:
        env_lines.append(f'export HERMIT_PROFILE="{profile}"')
    bundled_python = macos_dir / "python3"
    if bundled_python.exists() or bundled_python.is_symlink():
        bundled_python.unlink()
    project_root = _project_root()
    if project_root is not None:
        helper_path = project_root / "scripts" / "hermit-common.sh"
        exec_line = (
            f'source "{helper_path}"\n'
            'UV_BIN="$(resolve_uv_bin)"\n'
            f'exec "${{UV_BIN}}" run --project "{project_root}" --python 3.13 '
            f'python -m hermit.apps.companion.menubar --adapter "{adapter}"'
        )
    else:
        target_python = _bundle_python_target()
        os.symlink(target_python, bundled_python)
        exec_line = (
            f'exec "$APP_ROOT/python3" -m hermit.apps.companion.menubar --adapter "{adapter}"'
        )
    launcher = "\n".join(
        [
            "#!/bin/zsh",
            "set -e",
            'APP_ROOT="$(cd "$(dirname "$0")" && pwd)"',
            *env_lines,
            exec_line,
            "",
        ]
    )
    launcher_path = macos_dir / "HermitMenu"
    launcher_path.write_text(launcher, encoding="utf-8")
    launcher_path.chmod(0o755)
    return bundle


def open_app_bundle(target: Path | None = None) -> None:
    subprocess.Popen(
        ["open", str(app_path(target))], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def _run_osascript(script: str) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def login_item_enabled(name: str | None = None) -> bool:
    resolved_name = name or app_name()
    script = (
        f'tell application "System Events"\n  return exists login item "{resolved_name}"\nend tell'
    )
    try:
        output = _run_osascript(script).lower()
    except Exception:
        return False
    return output == "true"


def enable_login_item(target: Path | None = None) -> str:
    bundle = app_path(target)
    if not bundle.exists():
        raise RuntimeError(
            _t(
                "companion.appbundle.login_item.bundle_missing",
                "App bundle not found: {bundle}",
                bundle=bundle,
            )
        )
    resolved_name = bundle.stem
    script = (
        'tell application "System Events"\n'
        f'  if exists login item "{resolved_name}" then delete login item "{resolved_name}"\n'
        f'  make login item at end with properties {{name:"{resolved_name}", path:"{bundle}", hidden:false}}\n'
        "end tell"
    )
    _run_osascript(script)
    return _t(
        "companion.appbundle.login_item.enabled",
        "Enabled login item for {name}.",
        name=resolved_name,
    )


def disable_login_item(name: str | None = None) -> str:
    resolved_name = name or app_name()
    script = (
        'tell application "System Events"\n'
        f'  if exists login item "{resolved_name}" then delete login item "{resolved_name}"\n'
        "end tell"
    )
    _run_osascript(script)
    return _t(
        "companion.appbundle.login_item.disabled",
        "Disabled login item for {name}.",
        name=resolved_name,
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=_t(
            "companion.appbundle.argparse.description",
            "Install Hermit menu bar app bundle",
        )
    )
    parser.add_argument(
        "--target",
        default=None,
        help=_t("companion.appbundle.argparse.target", "Custom .app target path."),
    )
    parser.add_argument(
        "--adapter",
        default="feishu",
        help=_t("companion.appbundle.argparse.adapter", "Adapter to manage."),
    )
    parser.add_argument(
        "--profile",
        default=None,
        help=_t(
            "companion.appbundle.argparse.profile",
            "Optional HERMIT_PROFILE override.",
        ),
    )
    parser.add_argument(
        "--base-dir",
        default=None,
        help=_t(
            "companion.appbundle.argparse.base_dir",
            "Optional HERMIT_BASE_DIR override.",
        ),
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help=_t("companion.appbundle.argparse.open", "Open the installed app."),
    )
    parser.add_argument(
        "--enable-login-item",
        action="store_true",
        help=_t(
            "companion.appbundle.argparse.enable_login_item",
            "Enable companion login item.",
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if sys.platform != "darwin":
        print(
            _t(
                "companion.appbundle.main.mac_only",
                "Hermit menu bar app bundle is only supported on macOS.",
            ),
            file=sys.stderr,
        )
        return 1
    args = _parse_args(argv or sys.argv[1:])
    target = Path(args.target).expanduser() if args.target else None
    base_dir = Path(args.base_dir).expanduser() if args.base_dir else None
    bundle = install_app_bundle(
        target=target, adapter=args.adapter, profile=args.profile, base_dir=base_dir
    )
    print(
        _t(
            "companion.appbundle.main.installed",
            "Installed app bundle: {bundle}",
            bundle=bundle,
        )
    )
    if args.enable_login_item:
        print(enable_login_item(bundle))
    if args.open:
        open_app_bundle(bundle)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
