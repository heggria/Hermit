from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

DEFAULT_LOCALE = "en-US"

_LOCALE_ALIASES = {
    "en": "en-US",
    "en-us": "en-US",
    "zh": "zh-CN",
    "zh-cn": "zh-CN",
    "zh-hans": "zh-CN",
}


def normalize_locale(value: str | None) -> str:
    if not value:
        return DEFAULT_LOCALE
    cleaned = value.strip().replace("_", "-")
    if not cleaned:
        return DEFAULT_LOCALE
    canonical = _LOCALE_ALIASES.get(cleaned.lower())
    if canonical:
        return canonical
    if "-" in cleaned:
        language, region = cleaned.split("-", 1)
        return f"{language.lower()}-{region.upper()}"
    return cleaned.lower()


def locale_from_env(environ: Mapping[str, str] | None = None) -> str:
    env = environ or os.environ
    for key in ("HERMIT_LOCALE", "LC_ALL", "LC_MESSAGES", "LANG"):
        raw = env.get(key)
        if not raw:
            continue
        candidate = raw.split(".", 1)[0].split("@", 1)[0]
        return normalize_locale(candidate)
    return DEFAULT_LOCALE


def resolve_locale(preferred: str | None = None, environ: Mapping[str, str] | None = None) -> str:
    if preferred:
        return normalize_locale(preferred)
    return locale_from_env(environ)


def _catalog_dir() -> Path:
    return Path(__file__).resolve().parent / "locales"


def catalog_locales() -> list[str]:
    root = _catalog_dir()
    if not root.exists():
        return [DEFAULT_LOCALE]

    discovered: set[str] = set()
    for child in root.iterdir():
        if child.is_file() and child.suffix == ".json":
            discovered.add(normalize_locale(child.stem))
        elif child.is_dir():
            discovered.add(normalize_locale(child.name))
    discovered.add(DEFAULT_LOCALE)
    return sorted(discovered)


def _catalog_paths(locale: str) -> list[Path]:
    canonical = normalize_locale(locale)
    root = _catalog_dir()
    paths: list[Path] = []

    single_file = root / f"{canonical}.json"
    if single_file.exists():
        paths.append(single_file)

    locale_dir = root / canonical
    if locale_dir.exists() and locale_dir.is_dir():
        paths.extend(sorted(path for path in locale_dir.glob("*.json") if path.is_file()))

    return paths


@lru_cache(maxsize=None)
def _load_catalog(locale: str) -> dict[str, str]:
    canonical = normalize_locale(locale)
    catalog = dict(_read_catalog(DEFAULT_LOCALE))
    if canonical != DEFAULT_LOCALE:
        catalog.update(_read_catalog(canonical))
    return catalog


def _read_catalog(locale: str) -> dict[str, str]:
    merged: dict[str, str] = {}
    for path in _catalog_paths(locale):
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        merged.update({str(key): str(value) for key, value in raw.items()})
    return merged


def load_catalog(locale: str, *, include_default: bool = True) -> dict[str, str]:
    canonical = normalize_locale(locale)
    if include_default:
        return dict(_load_catalog(canonical))
    return dict(_read_catalog(canonical))


def tr(
    message_key: str,
    *,
    locale: str | None = None,
    default: str | None = None,
    **kwargs: object,
) -> str:
    canonical = resolve_locale(locale)
    template = _load_catalog(canonical).get(
        message_key,
        default if default is not None else message_key,
    )
    if kwargs:
        try:
            return template.format(**kwargs)
        except Exception:
            return template
    return template


def localize_schema(schema: Any, *, locale: str | None = None) -> Any:
    resolved_locale = resolve_locale(locale)

    if isinstance(schema, list):
        return [localize_schema(item, locale=resolved_locale) for item in schema]

    if not isinstance(schema, dict):
        return schema

    localized: dict[str, Any] = {}
    description_key = schema.get("description_key")
    title_key = schema.get("title_key")
    default_description = schema.get("description")
    default_title = schema.get("title")
    has_schema_description = description_key is not None or isinstance(default_description, str)
    has_schema_title = title_key is not None or isinstance(default_title, str)

    for key, value in schema.items():
        if key in {"description_key", "title_key"}:
            continue
        localized[key] = localize_schema(value, locale=resolved_locale)

    if has_schema_description:
        localized["description"] = tr(
            str(description_key or ""),
            locale=resolved_locale,
            default=default_description if isinstance(default_description, str) else "",
        )
    if has_schema_title:
        localized["title"] = tr(
            str(title_key or ""),
            locale=resolved_locale,
            default=default_title if isinstance(default_title, str) else "",
        )
    return localized
