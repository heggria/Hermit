from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Callable

from hermit import __version__
from hermit.companion.appbundle import (
    app_path,
    disable_login_item,
    enable_login_item,
    install_app_bundle,
    login_item_enabled,
    open_app_bundle,
)
from hermit.companion.control import (
    companion_log_path,
    config_path,
    docs_path,
    ensure_base_dir,
    ensure_config_file,
    format_exception_message,
    hermit_base_dir,
    hermit_log_dir,
    load_profile_runtime_settings,
    load_runtime_settings,
    log_companion_event,
    open_in_textedit,
    open_path,
    open_url,
    project_repo_url,
    project_wiki_url,
    readme_path,
    reload_service,
    run_hermit_command,
    service_status,
    start_service,
    stop_service,
    switch_profile,
    update_profile_bool_and_restart,
)
from hermit.config import get_settings
from hermit.i18n import resolve_locale, tr

try:
    import rumps
except Exception as exc:  # pragma: no cover - runtime dependency on macOS only
    rumps = None
    _import_error: Exception | None = exc
else:  # pragma: no cover - exercised manually on macOS
    _import_error = None


def _t(key: str, **kwargs: object) -> str:
    settings = get_settings()
    locale_raw = getattr(settings, "locale", None)
    locale = resolve_locale(str(locale_raw) if locale_raw is not None else None)
    return tr(key, locale=locale, **kwargs)  # type: ignore[arg-type]


def _profile_menu_entry(profile_name: str, *, base_dir: Path) -> tuple[str, bool]:
    settings = load_profile_runtime_settings(profile_name, base_dir=base_dir)
    if settings.has_auth:
        return profile_name, True
    return _t(
        "menubar.profile.entry", profile=profile_name, status=_t("menubar.profile.auth.missing")
    ), False


def _about_message(*, adapter: str, base_dir: Path) -> str:
    settings = load_runtime_settings(base_dir)
    profile = settings.resolved_profile or settings.default_profile or _t("menubar.profile.default")
    lines = [
        _t("menubar.about.version", version=__version__),
        _t("menubar.about.adapter", adapter=adapter),
        _t("menubar.about.profile", profile=profile),
        _t("menubar.about.provider", provider=settings.provider),
        _t("menubar.about.model", model=settings.model),
        _t("menubar.about.base_dir", base_dir=base_dir),
        _t("menubar.about.readme", readme=readme_path()),
        _t("menubar.about.docs", docs=docs_path()),
        _t("menubar.about.repo", url=project_repo_url()),
    ]
    return "\n".join(lines)


if rumps is not None:  # pragma: no branch - class only exists when dependency is available

    class HermitMenuApp(rumps.App):  # pragma: no cover - macOS UI exercised manually
        def __init__(
            self, *, adapter: str, profile: str | None = None, base_dir: Path | None = None
        ) -> None:
            super().__init__(_t("menubar.title"), quit_button=None)  # type: ignore[arg-type]
            self.adapter = adapter
            self.profile = profile
            self.base_dir = base_dir or hermit_base_dir()
            self.busy_label: str | None = None
            self.status_item = rumps.MenuItem(_t("menubar.status.checking"))  # type: ignore[union-attr]
            self.profile_item = rumps.MenuItem(_t("menubar.profile.pending"))  # type: ignore[union-attr]
            self.provider_item = rumps.MenuItem(_t("menubar.provider.pending"))  # type: ignore[union-attr]
            self.model_item = rumps.MenuItem(_t("menubar.model.pending"))  # type: ignore[union-attr]
            self.start_item = rumps.MenuItem(  # type: ignore[union-attr]
                _t("menubar.action.start"), callback=self._start_service
            )
            self.stop_item = rumps.MenuItem(_t("menubar.action.stop"), callback=self._stop_service)  # type: ignore[union-attr]
            self.reload_item = rumps.MenuItem(  # type: ignore[union-attr]
                _t("menubar.action.reload"), callback=self._reload_service
            )
            self.switch_profile_item = rumps.MenuItem(_t("menubar.action.switch_profile"))  # type: ignore[union-attr]
            self.feature_toggles_item = rumps.MenuItem(_t("menubar.action.feature_toggles"))  # type: ignore[union-attr]
            self.thread_progress_item = rumps.MenuItem(  # type: ignore[union-attr]
                _t("plugin.feishu.variable.thread_progress"), callback=self._toggle_thread_progress
            )
            self.scheduler_enabled_item = rumps.MenuItem(  # type: ignore[union-attr]
                _t("plugin.scheduler.variable.enabled"), callback=self._toggle_scheduler_enabled
            )
            self.webhook_enabled_item = rumps.MenuItem(  # type: ignore[union-attr]
                _t("plugin.webhook.variable.enabled"), callback=self._toggle_webhook_enabled
            )
            self.autostart_item = rumps.MenuItem(  # type: ignore[union-attr]
                _t("menubar.action.autostart"), callback=self._toggle_autostart
            )
            self.menu_login_item = rumps.MenuItem(  # type: ignore[union-attr]
                _t("menubar.action.menu_login_item"), callback=self._toggle_menu_login_item
            )
            self.install_app_item = rumps.MenuItem(  # type: ignore[union-attr]
                _t("menubar.action.install_or_open"), callback=self._install_or_open_menu_app
            )
            self.open_settings_item = rumps.MenuItem(  # type: ignore[union-attr]
                _t("menubar.action.open_settings"), callback=self._open_config
            )
            self.open_readme_item = rumps.MenuItem(  # type: ignore[union-attr]
                _t("menubar.action.open_readme"), callback=self._open_readme
            )
            self.open_wiki_item = rumps.MenuItem(  # type: ignore[union-attr]
                _t("menubar.action.open_wiki"), callback=self._open_wiki
            )
            self.about_item = rumps.MenuItem(_t("menubar.action.about"), callback=self._show_about)  # type: ignore[union-attr]
            self.menu = [
                self.status_item,
                self.profile_item,
                self.provider_item,
                self.model_item,
                None,
                self.start_item,
                self.stop_item,
                self.reload_item,
                self.switch_profile_item,
                self.feature_toggles_item,
                None,
                self.autostart_item,
                self.menu_login_item,
                self.install_app_item,
                None,
                self.open_settings_item,
                self.open_readme_item,
                self.open_wiki_item,
                rumps.MenuItem(_t("menubar.action.open_logs"), callback=self._open_logs),  # type: ignore[union-attr]
                rumps.MenuItem(_t("menubar.action.open_home"), callback=self._open_base_dir),  # type: ignore[union-attr]
                None,
                self.about_item,
                rumps.MenuItem(_t("menubar.action.quit"), callback=self._quit_app),  # type: ignore[union-attr]
            ]
            self.feature_toggles_item.add(self.thread_progress_item)  # type: ignore[union-attr]
            self.feature_toggles_item.add(self.scheduler_enabled_item)  # type: ignore[union-attr]
            self.feature_toggles_item.add(self.webhook_enabled_item)  # type: ignore[union-attr]
            self.refresh_status(None)  # type: ignore[union-attr]

        def _notify(self, title: str, message: str) -> None:
            rumps.notification(title, "", message)  # type: ignore[union-attr]

        def _alert(self, title: str, message: str) -> None:
            try:
                rumps.alert(title=title, message=message)  # type: ignore[union-attr]
            except Exception:
                self._notify(title, message)

        def _record_result(
            self, action: str, message: str, *, level: str = "INFO", detail: str | None = None
        ) -> None:
            log_companion_event(action, message, base_dir=self.base_dir, level=level, detail=detail)

        def _handle_failure(
            self, title: str, action: str, message: str, *, detail: str | None = None
        ) -> None:
            self._record_result(action, message, level="ERROR", detail=detail)
            open_path(hermit_log_dir(self.base_dir))
            self._alert(title, f"{message}\n\nLogs: {companion_log_path(self.base_dir)}")

        def _set_busy(self, label: str | None) -> None:
            self.busy_label = label
            if label:
                self.title = _t("menubar.title.loading")
                self.status_item.title = _t("menubar.status.loading", action=label)
            else:
                self.title = _t("menubar.title")

        def _show_result(
            self,
            title: str,
            action: str,
            fn: Callable[..., Any],
            *args: Any,
            busy_label: str | None = None,
            **kwargs: Any,
        ) -> None:
            self._set_busy(busy_label)
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:
                message, detail = format_exception_message(exc)
                self._handle_failure(title, action, message, detail=detail)
            else:
                message = str(result)
                if message.startswith("Failed") or message.startswith("Error"):
                    self._handle_failure(title, action, message)
                else:
                    self._record_result(action, message)
                    self._notify(title, message)
            finally:
                self._set_busy(None)
                self.refresh_status(None)  # type: ignore[union-attr]

        @rumps.timer(5)  # type: ignore[union-attr]
        def refresh_status(self, _sender: Any) -> None:
            if self.busy_label:
                self.status_item.title = _t("menubar.status.loading", action=self.busy_label)
                return
            try:
                settings = load_runtime_settings(self.base_dir)
                state = service_status(self.adapter, base_dir=self.base_dir)
                running_text = (
                    _t("menubar.status.running") if state.running else _t("menubar.status.stopped")
                )
                if state.running and state.pid is not None:
                    running_text = _t("menubar.status.pid", text=running_text, pid=state.pid)
                autostart = (
                    _t("menubar.status.launchd_on")
                    if state.autostart_loaded
                    else _t("menubar.status.launchd_off")
                )
                self.status_item.title = _t(
                    "menubar.status.summary", running=running_text, autostart=autostart
                )
                resolved_profile = settings.resolved_profile or _t("menubar.profile.default")
                self.profile_item.title = _t("menubar.profile.title", profile=resolved_profile)
                self.provider_item.title = _t("menubar.provider.title", provider=settings.provider)
                self.model_item.title = _t("menubar.model.title", model=settings.model)
                self._refresh_profile_menu(
                    settings.resolved_profile, list(settings.config_profiles)
                )
                self.autostart_item.state = 1 if state.autostart_loaded else 0
                self.menu_login_item.state = 1 if login_item_enabled() else 0
                self.thread_progress_item.state = 1 if bool(settings.feishu_thread_progress) else 0
                self.scheduler_enabled_item.state = 1 if bool(settings.scheduler_enabled) else 0
                self.webhook_enabled_item.state = 1 if bool(settings.webhook_enabled) else 0
                if state.autostart_loaded:
                    self.start_item.title = _t("menubar.action.start.managed")
                    self.start_item.set_callback(None)  # type: ignore[union-attr]
                    self.stop_item.title = _t("menubar.action.stop.disable_autostart")
                    self.stop_item.set_callback(None)  # type: ignore[union-attr]
                elif state.running:
                    self.start_item.title = _t("menubar.action.start.running")
                    self.start_item.set_callback(None)  # type: ignore[union-attr]
                    self.stop_item.title = _t("menubar.action.stop")
                    self.stop_item.set_callback(self._stop_service)  # type: ignore[union-attr]
                else:
                    self.start_item.title = _t("menubar.action.start")
                    self.start_item.set_callback(self._start_service)  # type: ignore[union-attr]
                    self.stop_item.title = _t("menubar.action.stop.not_running")
                    self.stop_item.set_callback(None)  # type: ignore[union-attr]
                self.reload_item.title = (
                    _t("menubar.action.reload")
                    if state.running
                    else _t("menubar.action.reload.not_running")
                )
                self.reload_item.set_callback(self._reload_service if state.running else None)  # type: ignore[union-attr]
                self.install_app_item.title = (
                    _t("menubar.action.open_menu_app")
                    if login_item_enabled()
                    else _t("menubar.action.install_or_open")
                )
            except Exception as exc:
                message, detail = format_exception_message(exc)
                self.status_item.title = _t("menubar.status.error")
                self._record_result("refresh_status", message, level="ERROR", detail=detail)

        def _refresh_profile_menu(self, current_profile: str | None, profiles: list[str]) -> None:
            if getattr(self.switch_profile_item, "_menu", None) is not None:
                self.switch_profile_item.clear()
            if not profiles:
                item = rumps.MenuItem(_t("menubar.profile.none"))  # type: ignore[union-attr]
                item.set_callback(None)  # type: ignore[union-attr]
                self.switch_profile_item.add(item)  # type: ignore[union-attr]
                return
            for name in sorted(profiles):
                title, available = _profile_menu_entry(name, base_dir=self.base_dir)
                item = rumps.MenuItem(title)  # type: ignore[union-attr]
                item.state = 1 if name == current_profile else 0
                if name == current_profile or not available:
                    item.set_callback(None)  # type: ignore[union-attr]
                else:
                    item.set_callback(self._make_switch_profile_callback(name))  # type: ignore[union-attr]
                self.switch_profile_item.add(item)  # type: ignore[union-attr]

        def _make_switch_profile_callback(self, profile_name: str) -> Callable[[Any], None]:
            def _callback(_sender: Any) -> None:
                self._show_result(
                    _t("menubar.title"),
                    f"switch_profile:{profile_name}",
                    switch_profile,
                    self.adapter,
                    profile_name,
                    base_dir=self.base_dir,
                    busy_label=_t("menubar.status.loading.switch_profile"),
                )

            return _callback

        def _start_service(self, _sender: Any) -> None:
            self._show_result(
                _t("menubar.title"),
                "start_service",
                start_service,
                self.adapter,
                base_dir=self.base_dir,
                busy_label=_t("menubar.status.loading.start"),
            )

        def _stop_service(self, _sender: Any) -> None:
            self._show_result(
                _t("menubar.title"),
                "stop_service",
                stop_service,
                self.adapter,
                base_dir=self.base_dir,
                busy_label=_t("menubar.status.loading.stop"),
            )

        def _reload_service(self, _sender: Any) -> None:
            self._show_result(
                _t("menubar.title"),
                "reload_service",
                reload_service,
                self.adapter,
                base_dir=self.base_dir,
                busy_label=_t("menubar.status.loading.reload"),
            )

        def _toggle_autostart(self, _sender: Any) -> None:
            state = service_status(self.adapter, base_dir=self.base_dir)
            command = [
                "autostart",
                "disable" if state.autostart_loaded else "enable",
                "--adapter",
                self.adapter,
            ]
            self._show_result(
                _t("menubar.title"),
                "toggle_autostart",
                lambda: run_hermit_command(
                    command,
                    base_dir=self.base_dir,
                ).stdout.strip(),
                busy_label=_t("menubar.status.loading.autostart"),
            )

        def _toggle_menu_login_item(self, _sender: Any) -> None:
            if login_item_enabled():
                self._show_result(
                    _t("menubar.title"),
                    "disable_menu_login_item",
                    disable_login_item,
                    busy_label=_t("menubar.status.loading.menu_login_item"),
                )
                return
            bundle = install_app_bundle(adapter=self.adapter, base_dir=self.base_dir)
            self._show_result(
                _t("menubar.title"),
                "enable_menu_login_item",
                enable_login_item,
                bundle,
                busy_label=_t("menubar.status.loading.menu_login_item"),
            )

        def _current_profile_name(self) -> str:
            settings = load_runtime_settings(self.base_dir)
            return settings.resolved_profile or settings.default_profile or "default"

        def _toggle_profile_bool(self, key: str, current_value: bool) -> None:
            profile_name = self._current_profile_name()
            self._show_result(
                _t("menubar.title"),
                f"toggle_profile_bool:{key}",
                update_profile_bool_and_restart,
                self.adapter,
                profile_name,
                key,
                not current_value,
                base_dir=self.base_dir,
                busy_label=_t("menubar.status.loading.restart"),
            )

        def _toggle_thread_progress(self, _sender: Any) -> None:
            self._toggle_profile_bool(
                "feishu_thread_progress",
                bool(load_runtime_settings(self.base_dir).feishu_thread_progress),
            )

        def _toggle_scheduler_enabled(self, _sender: Any) -> None:
            self._toggle_profile_bool(
                "scheduler_enabled", bool(load_runtime_settings(self.base_dir).scheduler_enabled)
            )

        def _toggle_webhook_enabled(self, _sender: Any) -> None:
            self._toggle_profile_bool(
                "webhook_enabled", bool(load_runtime_settings(self.base_dir).webhook_enabled)
            )

        def _install_or_open_menu_app(self, _sender: Any) -> None:
            try:
                bundle = app_path()
                if not bundle.exists():
                    bundle = install_app_bundle(adapter=self.adapter, base_dir=self.base_dir)
                    message = _t("menubar.notify.installed_menu_app", bundle=bundle)
                    self._record_result("install_menu_app", message)
                    self._notify(_t("menubar.title"), message)
                open_app_bundle(bundle)
                self._record_result("open_menu_app", str(bundle))
            except Exception as exc:
                message, detail = format_exception_message(exc)
                self._handle_failure(_t("menubar.title"), "open_menu_app", message, detail=detail)

        def _open_config(self, _sender: Any) -> None:
            try:
                ensure_base_dir(self.base_dir)
                target = config_path(self.base_dir)
                if not target.exists():
                    target = ensure_config_file(self.base_dir)
                    message = _t("menubar.notify.config_missing", base_dir=self.base_dir)
                    self._record_result("open_config", message)
                    self._notify(_t("menubar.title"), message)
                open_in_textedit(target)
                self._record_result("open_config", str(target))
            except Exception as exc:
                message, detail = format_exception_message(exc)
                self._handle_failure(_t("menubar.title"), "open_config", message, detail=detail)

        def _open_readme(self, _sender: Any) -> None:
            try:
                target = readme_path()
                if target.exists():
                    open_in_textedit(target)
                else:
                    open_url(project_repo_url())
                self._record_result("open_readme", str(target))
            except Exception as exc:
                message, detail = format_exception_message(exc)
                self._handle_failure(_t("menubar.title"), "open_readme", message, detail=detail)

        def _open_wiki(self, _sender: Any) -> None:
            try:
                url = project_wiki_url()
                open_url(url)
                self._record_result("open_wiki", url)
            except Exception as exc:
                message, detail = format_exception_message(exc)
                self._handle_failure(_t("menubar.title"), "open_wiki", message, detail=detail)

        def _show_about(self, _sender: Any) -> None:
            try:
                self._alert(
                    _t("menubar.action.about"),
                    _about_message(adapter=self.adapter, base_dir=self.base_dir),
                )
                self._record_result("show_about", __version__)
            except Exception as exc:
                message, detail = format_exception_message(exc)
                self._handle_failure(_t("menubar.title"), "show_about", message, detail=detail)

        def _open_logs(self, _sender: Any) -> None:
            try:
                target = hermit_log_dir(self.base_dir)
                open_path(target)
                self._record_result("open_logs", str(target))
            except Exception as exc:
                message, detail = format_exception_message(exc)
                self._handle_failure(_t("menubar.title"), "open_logs", message, detail=detail)

        def _open_base_dir(self, _sender: Any) -> None:
            try:
                open_path(self.base_dir)
                self._record_result("open_home", str(self.base_dir))
            except Exception as exc:
                message, detail = format_exception_message(exc)
                self._handle_failure(_t("menubar.title"), "open_home", message, detail=detail)

        def _quit_app(self, _sender: Any) -> None:
            rumps.quit_application()  # type: ignore[union-attr]


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=_t("menubar.argparse.description"))
    parser.add_argument("--adapter", default="feishu", help=_t("menubar.argparse.adapter"))
    parser.add_argument("--profile", default=None, help=_t("menubar.argparse.profile"))
    parser.add_argument("--base-dir", default=None, help=_t("menubar.argparse.base_dir"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if sys.platform != "darwin":
        print(_t("menubar.error.mac_only"), file=sys.stderr)
        return 1
    if rumps is None:
        print(_t("menubar.error.missing_rumps"), file=sys.stderr)
        if _import_error is not None:
            print(str(_import_error), file=sys.stderr)
        return 1
    args = _parse_args(argv or sys.argv[1:])
    base_dir = Path(args.base_dir).expanduser() if args.base_dir else None
    app = HermitMenuApp(adapter=args.adapter, profile=args.profile, base_dir=base_dir)
    app.run()  # type: ignore[union-attr]
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
