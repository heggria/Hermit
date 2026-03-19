"""Shared regex constants for the kernel.task package.

These patterns strip runtime-injected XML tags from message text before
the text is stored, displayed, or compared.  Defining them once here
avoids drift between topics.py, conversation.py, outcomes.py and
controller.py.
"""

from __future__ import annotations

import re

# Matches <session_time>…</session_time> blocks (including trailing whitespace).
_SESSION_TIME_RE: re.Pattern[str] = re.compile(r"<session_time>.*?</session_time>\s*", re.DOTALL)

# Matches any <feishu_…>…</feishu_…> tag pair (including trailing whitespace).
_FEISHU_META_RE: re.Pattern[str] = re.compile(r"<feishu_[^>]+>.*?</feishu_[^>]+>\s*", re.DOTALL)
