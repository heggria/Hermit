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

# Matches any <feishu_…> tag — both paired open/close variants
# (e.g. <feishu_chat_id>oc_xxx</feishu_chat_id>) *and* self-closing variants
# (e.g. <feishu_image key='img_v2_xxx'/>).  The previous pattern only handled
# paired tags, allowing self-closing tags to leak into stored or compared text.
_FEISHU_META_RE: re.Pattern[str] = re.compile(
    r"<feishu_[^>]*/>\s*"  # self-closing:  <feishu_image key='…'/>
    r"|"
    r"<feishu_[^>]+>.*?</feishu_[^>]+>\s*",  # paired:  <feishu_chat_id>…</feishu_chat_id>
    re.DOTALL,
)
