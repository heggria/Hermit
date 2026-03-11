"""Shared Feishu lark-oapi client factory."""
from __future__ import annotations

import os
from typing import Any


def build_lark_client(settings: object | None = None) -> Any:
    """Build a lark-oapi Client from settings or environment variables.

    Reads HERMIT_FEISHU_APP_ID / HERMIT_FEISHU_APP_SECRET (with
    legacy FEISHU_APP_ID / FEISHU_APP_SECRET as fallbacks).

    Raises RuntimeError if credentials are not configured.
    """
    if settings is None:
        try:
            from hermit.config import get_settings

            settings = get_settings()
        except Exception:
            settings = None

    app_id = str(
        getattr(settings, "feishu_app_id", None)
        or os.environ.get("HERMIT_FEISHU_APP_ID", os.environ.get("FEISHU_APP_ID", ""))
    )
    app_secret = str(
        getattr(settings, "feishu_app_secret", None)
        or os.environ.get("HERMIT_FEISHU_APP_SECRET", os.environ.get("FEISHU_APP_SECRET", ""))
    )
    if not app_id or not app_secret:
        raise RuntimeError(
            "Feishu credentials not configured. "
            "Set HERMIT_FEISHU_APP_ID and HERMIT_FEISHU_APP_SECRET."
        )
    import lark_oapi as lark

    return lark.Client.builder().app_id(app_id).app_secret(app_secret).build()
