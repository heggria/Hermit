#!/usr/bin/env python3
from __future__ import annotations

import string
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hermit.i18n import DEFAULT_LOCALE, catalog_locales, load_catalog


def _fields(template: str) -> set[str]:
    formatter = string.Formatter()
    return {
        field_name
        for _literal, field_name, _format_spec, _conversion in formatter.parse(template)
        if field_name
    }


def _format_keys(keys: Iterable[str]) -> str:
    return ", ".join(sorted(keys))


def main() -> int:
    locales = catalog_locales()
    baseline = load_catalog(DEFAULT_LOCALE, include_default=False)
    errors: list[str] = []

    if not baseline:
        errors.append(f"{DEFAULT_LOCALE} catalog is empty.")

    baseline_keys = set(baseline)

    for locale in locales:
        entries = load_catalog(locale, include_default=False)
        if locale == DEFAULT_LOCALE:
            continue

        keys = set(entries)
        missing = baseline_keys - keys
        extra = keys - baseline_keys
        if missing:
            errors.append(f"{locale} is missing keys: {_format_keys(missing)}")
        if extra:
            errors.append(f"{locale} has extra keys: {_format_keys(extra)}")

        for key in sorted(baseline_keys & keys):
            default_fields = _fields(baseline[key])
            locale_fields = _fields(entries[key])
            if default_fields != locale_fields:
                errors.append(
                    f"{locale} placeholder mismatch for {key}: "
                    f"default={sorted(default_fields)} locale={sorted(locale_fields)}"
                )

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    print(f"i18n catalogs OK for locales: {', '.join(locales)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
