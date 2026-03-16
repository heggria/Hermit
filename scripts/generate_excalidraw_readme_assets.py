#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import json
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unicodedata
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "site" / "assets"
PNG_ASSETS = ASSETS / "png"
KROKI_URL = "https://kroki.io/excalidraw/svg"

PALETTE = {
    "canvas": "#f6f1e8",
    "ink": "#1f1b17",
    "muted": "#6f6256",
    "line": "#b9ab9a",
    "panel": "#fffdf9",
    "accent": "#f2e0d2",
    "accent_stroke": "#b77452",
    "sand": "#f6eedf",
    "gold": "#f2ead7",
    "green": "#e9efe1",
    "lavender": "#eee7f7",
}


def _char_width_units(char: str) -> float:
    if char in {" ", "\u3000"}:
        return 0.34
    if unicodedata.east_asian_width(char) in {"W", "F"}:
        return 1.0
    if char.isupper():
        return 0.72
    if char.islower() or char.isdigit():
        return 0.62
    if char in {"-", "+", ">", "<", "/", "\\"}:
        return 0.5
    return 0.48


def _contains_cjk(text: str) -> bool:
    return any(unicodedata.east_asian_width(char) in {"W", "F"} for char in text)


def estimate_text_box(text: str, font_size: int) -> tuple[float, float]:
    lines = text.split("\n")
    width = max(max(sum(_char_width_units(char) for char in line), 1) for line in lines) * font_size
    height = len(lines) * font_size * 1.25
    return round(width, 2), round(height, 2)


def text_height(text: str, font_size: int) -> float:
    return estimate_text_box(text, font_size)[1]


def wrap_text(text: str, font_size: int, max_width: float) -> str:
    wrapped_blocks: list[str] = []
    for block in text.split("\n"):
        if not block:
            wrapped_blocks.append("")
            continue
        if _contains_cjk(block):
            tokens = list(block)
            joiner = ""
        else:
            tokens = block.split()
            joiner = " "
        if not tokens:
            wrapped_blocks.append("")
            continue
        lines: list[str] = []
        current = tokens[0]
        for token in tokens[1:]:
            candidate = f"{current}{joiner}{token}" if joiner else f"{current}{token}"
            if estimate_text_box(candidate, font_size)[0] <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = token
        lines.append(current)
        wrapped_blocks.append("\n".join(lines))
    return "\n".join(wrapped_blocks)


def fit_font_size(
    text: str,
    font_size: int,
    max_width: float,
    *,
    min_font_size: int = 12,
) -> int:
    while font_size > min_font_size and estimate_text_box(text, font_size)[0] > max_width:
        font_size -= 1
    return font_size


class Scene:
    def __init__(
        self,
        name: str,
        width: int,
        height: int,
        *,
        default_font_family: int = 5,
    ) -> None:
        self.name = name
        self.width = width
        self.height = height
        self.default_font_family = default_font_family
        self.elements: list[dict] = []
        self._counter = 0
        self._nonce = 1000
        self._seed = 2000
        self._now = int(time.time() * 1000)

    def _base(self, element_type: str, x: float, y: float) -> dict:
        self._counter += 1
        self._nonce += 17
        self._seed += 29
        return {
            "id": f"{self.name}-{self._counter}",
            "type": element_type,
            "x": x,
            "y": y,
            "angle": 0,
            "opacity": 100,
            "groupIds": [],
            "frameId": None,
            "roundness": {"type": 3},
            "updated": self._now,
            "link": None,
            "locked": False,
            "version": 1,
            "versionNonce": self._nonce,
            "isDeleted": False,
            "seed": self._seed,
            "index": f"a{self._counter}",
        }

    def rectangle(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        *,
        stroke: str = PALETTE["ink"],
        fill: str = PALETTE["panel"],
        fill_style: str = "solid",
        stroke_width: int = 2,
        roughness: int = 1,
    ) -> None:
        element = self._base("rectangle", x, y)
        element.update(
            {
                "width": width,
                "height": height,
                "strokeColor": stroke,
                "backgroundColor": fill,
                "fillStyle": fill_style,
                "strokeWidth": stroke_width,
                "strokeStyle": "solid",
                "roughness": roughness,
                "boundElements": [],
            }
        )
        self.elements.append(element)

    def text(
        self,
        x: float,
        y: float,
        value: str,
        *,
        font_size: int = 24,
        color: str = PALETTE["ink"],
        align: str = "left",
        font_family: int | None = None,
    ) -> None:
        width, height = estimate_text_box(value, font_size)
        element = self._base("text", x, y)
        element["roundness"] = None
        element.update(
            {
                "width": width,
                "height": height,
                "strokeColor": color,
                "backgroundColor": "transparent",
                "fillStyle": "solid",
                "strokeWidth": 1,
                "strokeStyle": "solid",
                "roughness": 1,
                "boundElements": [],
                "text": value,
                "fontSize": font_size,
                "fontFamily": font_family or self.default_font_family,
                "textAlign": align,
                "verticalAlign": "top",
                "containerId": None,
                "originalText": value,
                "autoResize": True,
                "lineHeight": 1.25,
            }
        )
        self.elements.append(element)

    def arrow(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        *,
        stroke: str = PALETTE["line"],
        stroke_width: int = 3,
    ) -> None:
        element = self._base("arrow", x1, y1)
        element["roundness"] = None
        element.update(
            {
                "width": x2 - x1,
                "height": y2 - y1,
                "strokeColor": stroke,
                "backgroundColor": "transparent",
                "fillStyle": "solid",
                "strokeWidth": stroke_width,
                "strokeStyle": "solid",
                "roughness": 1,
                "boundElements": [],
                "points": [[0, 0], [x2 - x1, y2 - y1]],
                "lastCommittedPoint": [x2 - x1, y2 - y1],
                "startBinding": None,
                "endBinding": None,
                "startArrowhead": None,
                "endArrowhead": "triangle",
            }
        )
        self.elements.append(element)

    def scene_json(self) -> dict:
        return {
            "type": "excalidraw",
            "version": 2,
            "source": "https://excalidraw.com",
            "elements": self.elements,
            "appState": {
                "viewBackgroundColor": PALETTE["canvas"],
                "gridSize": None,
                "gridStep": 5,
                "gridModeEnabled": False,
            },
            "files": {},
        }


def write_scene(path: Path, scene: Scene) -> None:
    path.write_text(json.dumps(scene.scene_json(), indent=2), encoding="utf-8")


def render_svg(scene_path: Path, svg_path: Path) -> None:
    payload_path = scene_path.with_suffix(".request.json")
    payload_path.write_text(
        json.dumps({"diagram_source": scene_path.read_text(encoding="utf-8")}),
        encoding="utf-8",
    )
    subprocess.run(
        [
            "curl",
            "-fsSL",
            "-X",
            "POST",
            "-H",
            "Content-Type: application/json",
            "--data-binary",
            f"@{payload_path}",
            KROKI_URL,
            "-o",
            str(svg_path),
        ],
        check=True,
    )
    payload_path.unlink(missing_ok=True)


def _find_chrome_headless_shell() -> Path | None:
    candidates = sorted(
        Path.home().glob(
            "Library/Caches/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-mac-arm64/chrome-headless-shell"
        )
    )
    for candidate in reversed(candidates):
        if candidate.exists():
            return candidate
    return None


def ensure_chrome_headless_shell() -> Path:
    chrome = _find_chrome_headless_shell()
    if chrome:
        return chrome
    if shutil.which("npx") is None:
        raise RuntimeError("Need `npx` to install Playwright Chromium. Install Node.js/npm first.")
    subprocess.run(["npx", "-y", "playwright@latest", "install", "chromium"], check=True)
    chrome = _find_chrome_headless_shell()
    if chrome:
        return chrome
    raise RuntimeError("Playwright Chromium headless shell was not found after install.")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


@contextlib.contextmanager
def repo_http_server(root: Path) -> str:
    port = free_port()

    def handler(*args: object, **kwargs: object) -> QuietHandler:
        return QuietHandler(*args, directory=str(root), **kwargs)

    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def render_png(slug: str, width: int, height: int, chrome: Path, base_url: str) -> Path:
    PNG_ASSETS.mkdir(parents=True, exist_ok=True)
    png_path = PNG_ASSETS / f"{slug}.png"
    with tempfile.TemporaryDirectory(prefix="excalidraw-export-", dir=ROOT) as tempdir:
        html_path = Path(tempdir) / f"{slug}.html"
        svg_url = f"{base_url}/docs/assets/{slug}.svg"
        html_doc = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    html, body {{
      margin: 0;
      padding: 0;
      background: {PALETTE["canvas"]};
    }}
    body {{
      width: {width}px;
      height: {height}px;
      overflow: hidden;
    }}
    img {{
      display: block;
      width: {width}px;
      height: {height}px;
    }}
  </style>
</head>
<body>
  <img src="{svg_url}" alt="{slug}">
</body>
</html>
"""
        html_path.write_text(html_doc, encoding="utf-8")
        html_url = f"{base_url}/{html_path.relative_to(ROOT).as_posix()}"
        subprocess.run(
            [
                str(chrome),
                "--headless",
                "--disable-gpu",
                "--hide-scrollbars",
                "--virtual-time-budget=3000",
                f"--window-size={width},{height}",
                f"--screenshot={png_path}",
                html_url,
            ],
            check=True,
        )
    return png_path


def differentiators_scene(locale: str = "en") -> Scene:
    is_zh = locale == "zh-cn"
    slug = "hermit-differentiators-zh-cn" if is_zh else "hermit-differentiators"
    scene = Scene(slug, 1160, 770, default_font_family=2 if is_zh else 5)
    scene.rectangle(24, 24, 1112, 722, stroke=PALETTE["line"], fill="#fbf8f2")
    tag_text = "核心差异" if is_zh else "WHY HERMIT"
    tag_w = 118 if is_zh else 146
    scene.rectangle(56, 52, tag_w, 38, stroke=PALETTE["line"], fill=PALETTE["accent"])
    scene.text(82, 62, tag_text, font_size=16, color=PALETTE["muted"])

    headline = (
        "不只是 agent 外壳。\n而是受治理的本地运行时。"
        if is_zh
        else "Not just an agent wrapper.\nA governed local-first runtime."
    )
    headline_y = 118
    headline_fs = 34 if is_zh else 36
    subtitle_fs = 18 if is_zh else 19
    scene.text(56, headline_y, headline, font_size=headline_fs)
    subtitle = (
        "Hermit 把审批、证据、恢复，和\nartifact 原生上下文放进同一条操作者路径。"
        if is_zh
        else "Hermit adds approval, evidence, recovery,\n"
        "and artifact-native context to the same operator loop."
    )
    subtitle_y = headline_y + text_height(headline, headline_fs) + 18
    scene.text(56, subtitle_y, subtitle, font_size=subtitle_fs, color=PALETTE["muted"])

    cards_top = subtitle_y + text_height(subtitle, subtitle_fs) + 34
    card_w = 504
    card_h = 190
    card_gap_x = 40
    card_gap_y = 24
    card_title_fs = 20 if is_zh else 22
    card_body_fs = 15 if is_zh else 16
    cards = (
        [
            (
                56,
                cards_top,
                card_w,
                card_h,
                PALETTE["accent"],
                "控制面",
                "副作用前先审批",
                "模型提出动作。\nKernel 决定哪些需要审批。",
            ),
            (
                56 + card_w + card_gap_x,
                cards_top,
                card_w,
                card_h,
                PALETTE["gold"],
                "证据",
                "执行后看 receipt\n和 proof",
                "重要动作会留下持久证据。\n看的不是模糊日志。",
            ),
            (
                56,
                cards_top + card_h + card_gap_y,
                card_w,
                card_h,
                PALETTE["green"],
                "恢复",
                "天然带 rollback\n意识",
                "已支持的 receipt 可回滚。\n恢复不是事后补救。",
            ),
            (
                56 + card_w + card_gap_x,
                cards_top + card_h + card_gap_y,
                card_w,
                card_h,
                PALETTE["lavender"],
                "上下文",
                "artifact 原生上下文\n与记忆",
                "上下文来自 artifact、task state、\nbelief 和 governed memory。",
            ),
        ]
        if is_zh
        else [
            (
                56,
                cards_top,
                card_w,
                card_h,
                PALETTE["accent"],
                "CONTROL",
                "Approval before side effects",
                "The model proposes work.\nThe kernel decides what needs approval.",
            ),
            (
                56 + card_w + card_gap_x,
                cards_top,
                card_w,
                card_h,
                PALETTE["gold"],
                "EVIDENCE",
                "Receipts and proof\nafter execution",
                "Important actions leave durable evidence.\nYou inspect the record instead of a vague log.",
            ),
            (
                56,
                cards_top + card_h + card_gap_y,
                card_w,
                card_h,
                PALETTE["green"],
                "RECOVERY",
                "Rollback-aware by design",
                "Supported receipts can roll back.\nRecovery is part of the path, not cleanup later.",
            ),
            (
                56 + card_w + card_gap_x,
                cards_top + card_h + card_gap_y,
                card_w,
                card_h,
                PALETTE["lavender"],
                "CONTEXT",
                "Artifact-native context\nand memory",
                "Context comes from artifacts, task state,\nbeliefs, and governed memory.",
            ),
        ]
    )
    for x, y, w, h, tag_fill, tag, title, body in cards:
        scene.rectangle(x, y, w, h, stroke=PALETTE["line"], fill=PALETTE["panel"])
        tag_w = 98 if is_zh else 124
        scene.rectangle(x + 22, y + 18, tag_w, 32, stroke=PALETTE["line"], fill=tag_fill)
        scene.text(x + 42, y + 26, tag, font_size=16, color=PALETTE["muted"])
        title_y = y + 68
        wrapped_title = wrap_text(title, card_title_fs, w - 44)
        wrapped_body = wrap_text(body, card_body_fs, w - 44)
        scene.text(x + 22, title_y, wrapped_title, font_size=card_title_fs)
        body_y = title_y + text_height(wrapped_title, card_title_fs) + 16
        scene.text(x + 22, body_y, wrapped_body, font_size=card_body_fs, color=PALETTE["muted"])
    return scene


def governed_path_scene(locale: str = "en") -> Scene:
    is_zh = locale == "zh-cn"
    slug = "hermit-governed-path-zh-cn" if is_zh else "hermit-governed-path"
    scene = Scene(slug, 1240, 820, default_font_family=2 if is_zh else 5)
    scene.rectangle(24, 24, 1192, 772, stroke=PALETTE["line"], fill="#fbf8f2")
    tag_text = "执行闭环" if is_zh else "GOVERNED PATH"
    tag_w = 120 if is_zh else 170
    scene.rectangle(56, 52, tag_w, 38, stroke=PALETTE["line"], fill=PALETTE["accent"])
    scene.text(82, 62, tag_text, font_size=16, color=PALETTE["muted"])

    headline = (
        "从 prompt 到 proof\n中间发生了什么" if is_zh else "What happens between\nprompt and proof"
    )
    headline_y = 112
    headline_fs = 34 if is_zh else 36
    subtitle_fs = 18 if is_zh else 19
    scene.text(56, headline_y, headline, font_size=headline_fs)
    subtitle = (
        "Hermit 会把一个任务变成\n可检查的执行路径，带审批、回执和回滚。"
        if is_zh
        else "Hermit turns one task into a visible execution path\n"
        "with approvals, receipts, and rollback."
    )
    subtitle_y = headline_y + text_height(headline, headline_fs) + 16
    scene.text(56, subtitle_y, subtitle, font_size=subtitle_fs, color=PALETTE["muted"])

    flow_y = subtitle_y + text_height(subtitle, subtitle_fs) + 34
    body_fs = 18 if not is_zh else 17
    boxes = (
        [
            (60, flow_y + 20, 174, 118, PALETTE["panel"], "入口", "CLI / Chat\n飞书 / 调度"),
            (258, flow_y + 20, 186, 118, PALETTE["panel"], "内核", "Task -> Step\nStepAttempt"),
            (468, flow_y - 2, 232, 160, PALETTE["sand"], "治理", "Policy gate\n审批\n权限边界"),
            (724, flow_y + 20, 182, 118, PALETTE["panel"], "执行", "工具在\nscope 内运行"),
            (930, flow_y + 20, 144, 118, PALETTE["panel"], "证据", "回执\n证明"),
        ]
        if is_zh
        else [
            (
                60,
                flow_y + 20,
                174,
                118,
                PALETTE["panel"],
                "INGRESS",
                "CLI / Chat\nFeishu / Scheduler",
            ),
            (258, flow_y + 20, 186, 118, PALETTE["panel"], "KERNEL", "Task -> Step\nStepAttempt"),
            (
                468,
                flow_y - 2,
                232,
                160,
                PALETTE["sand"],
                "GOVERNANCE",
                "Policy gate\napproval\nscoped authority",
            ),
            (724, flow_y + 20, 182, 118, PALETTE["panel"], "EXECUTION", "Tools run\ninside scope"),
            (930, flow_y + 20, 144, 118, PALETTE["panel"], "EVIDENCE", "Receipt\nproof"),
        ]
    )
    for x, y, w, h, fill, tag, body in boxes:
        scene.rectangle(x, y, w, h, stroke=PALETTE["line"], fill=fill)
        scene.text(x + 20, y + 18, tag, font_size=16, color=PALETTE["muted"])
        wrapped_body = wrap_text(body, body_fs, w - 40)
        scene.text(x + 20, y + 46, wrapped_body, font_size=body_fs)

    mid_y = flow_y + 74
    scene.arrow(234, mid_y, 258, mid_y)
    scene.arrow(444, mid_y, 468, mid_y)
    scene.arrow(700, mid_y, 724, mid_y)
    scene.arrow(906, mid_y, 930, mid_y)

    inspect_y = flow_y + 226
    scene.rectangle(88, inspect_y, 1016, 192, stroke=PALETTE["line"], fill=PALETTE["panel"])
    inspect_title = (
        "任务完成后，操作者还能检查什么" if is_zh else "What the operator can inspect after the run"
    )
    scene.text(118, inspect_y + 34, inspect_title, font_size=28 if is_zh else 30)
    pills = (
        [
            (118, inspect_y + 104, 150, 60, "任务状态", PALETTE["gold"]),
            (286, inspect_y + 104, 150, 60, "审批记录", PALETTE["gold"]),
            (454, inspect_y + 104, 150, 60, "回执", PALETTE["gold"]),
            (622, inspect_y + 104, 174, 60, "证明包", PALETTE["gold"]),
            (814, inspect_y + 104, 126, 60, "回滚", PALETTE["green"]),
        ]
        if is_zh
        else [
            (118, inspect_y + 104, 162, 60, "Task state", PALETTE["gold"]),
            (300, inspect_y + 104, 162, 60, "Approvals", PALETTE["gold"]),
            (482, inspect_y + 104, 162, 60, "Receipts", PALETTE["gold"]),
            (664, inspect_y + 104, 186, 60, "Proof bundles", PALETTE["gold"]),
            (870, inspect_y + 104, 126, 60, "Rollback", PALETTE["green"]),
        ]
    )
    for x, y, w, h, label, fill in pills:
        scene.rectangle(x, y, w, h, stroke=PALETTE["line"], fill=fill)
        scene.text(x + 26, y + 18, label, font_size=20 if is_zh else 21)
    return scene


def architecture_scene(locale: str = "en") -> Scene:
    is_zh = locale == "zh-cn"
    slug = "hermit-architecture-overview-zh-cn" if is_zh else "hermit-architecture-overview"
    scene = Scene(slug, 1240, 860, default_font_family=2 if is_zh else 5)
    scene.rectangle(24, 24, 1192, 812, stroke=PALETTE["line"], fill="#fbf8f2")
    tag_text = "架构总览" if is_zh else "ARCHITECTURE"
    tag_w = 120 if is_zh else 162
    scene.rectangle(56, 52, tag_w, 38, stroke=PALETTE["line"], fill=PALETTE["accent"])
    scene.text(82, 62, tag_text, font_size=16, color=PALETTE["muted"])

    headline = (
        "一个 kernel，多个\n操作者入口" if is_zh else "One kernel, multiple\noperator surfaces"
    )
    headline_y = 110
    headline_fs = 34 if is_zh else 36
    subtitle_fs = 18 if is_zh else 19
    scene.text(56, headline_y, headline, font_size=headline_fs)
    subtitle = (
        "Hermit 的重心是 task kernel，\n不是某一个 chat 或 adapter surface。"
        if is_zh
        else "Hermit's center of gravity is the task kernel,\n"
        "not any single chat or adapter surface."
    )
    subtitle_y = headline_y + text_height(headline, headline_fs) + 14
    scene.text(56, subtitle_y, subtitle, font_size=subtitle_fs, color=PALETTE["muted"])

    surfaces_y = subtitle_y + text_height(subtitle, subtitle_fs) + 30
    scene.rectangle(56, surfaces_y, 1128, 118, stroke=PALETTE["line"], fill=PALETTE["panel"])
    scene.text(
        80,
        surfaces_y + 28,
        "操作者入口" if is_zh else "Operator surfaces",
        font_size=28 if is_zh else 30,
    )
    labels = (
        ["CLI", "Chat", "飞书", "调度", "Webhook", "更多入口"]
        if is_zh
        else ["CLI", "Chat", "Feishu", "Scheduler", "Webhook", "More adapters"]
    )
    x_positions = [80, 204, 328, 456, 602, 748]
    widths = [88, 88, 106, 122, 110, 184]
    for x, label, w in zip(x_positions, labels, widths, strict=True):
        scene.rectangle(x, surfaces_y + 74, w, 30, stroke=PALETTE["line"], fill=PALETTE["accent"])
        wrapped_label = wrap_text(label, 15, w - 20)
        scene.text(x + 20, surfaces_y + 81, wrapped_label, font_size=15, color=PALETTE["muted"])

    kernel_y = surfaces_y + 162
    scene.arrow(620, surfaces_y + 118, 620, kernel_y)
    scene.rectangle(136, kernel_y, 968, 206, stroke=PALETTE["accent_stroke"], fill=PALETTE["panel"])
    scene.text(
        164,
        kernel_y + 26,
        "任务内核" if is_zh else "TASK KERNEL",
        font_size=15,
        color=PALETTE["muted"],
    )
    scene.text(
        164,
        kernel_y + 52,
        "受治理的中心" if is_zh else "The governed center",
        font_size=32 if is_zh else 34,
    )

    kernel_boxes = [
        (164, kernel_y + 108, 196, 70, "任务控制器" if is_zh else "Task controller"),
        (388, kernel_y + 108, 150, 70, "上下文" if is_zh else "Context"),
        (566, kernel_y + 108, 150, 70, "策略" if is_zh else "Policy"),
        (744, kernel_y + 108, 226, 70, "执行层" if is_zh else "Execution layer"),
    ]
    for x, y, w, h, label in kernel_boxes:
        scene.rectangle(x, y, w, h, stroke=PALETTE["line"], fill=PALETTE["gold"])
        wrapped_label = wrap_text(label, 18 if is_zh else 19, w - 36)
        scene.text(x + 20, y + 22, wrapped_label, font_size=18 if is_zh else 19)

    mid_y = kernel_y + 143
    scene.arrow(360, mid_y, 388, mid_y)
    scene.arrow(538, mid_y, 566, mid_y)
    scene.arrow(716, mid_y, 744, mid_y)

    durable_y = kernel_y + 238
    scene.arrow(620, kernel_y + 206, 620, durable_y)
    scene.rectangle(56, durable_y, 1128, 94, stroke=PALETTE["line"], fill=PALETTE["panel"])
    durable_text = wrap_text(
        "持久层\n账本、回执、证明、回滚、记忆"
        if is_zh
        else "Durable layer\nledger, receipts, proof, rollback, memory",
        24 if is_zh else 26,
        1040,
    )
    scene.text(82, durable_y + 18, durable_text, font_size=24 if is_zh else 26)
    return scene


def generic_vs_hermit_scene(locale: str = "en") -> Scene:
    is_zh = locale == "zh-cn"
    slug = "hermit-vs-generic-agent-zh-cn" if is_zh else "hermit-vs-generic-agent"
    scene = Scene(slug, 1160, 780, default_font_family=2 if is_zh else 5)
    scene.rectangle(24, 24, 1112, 732, stroke=PALETTE["line"], fill="#fbf8f2")
    tag_text = "为什么不一样" if is_zh else "WHY HERMIT"
    tag_w = 132 if is_zh else 142
    scene.rectangle(56, 52, tag_w, 38, stroke=PALETTE["line"], fill=PALETTE["accent"])
    scene.text(82, 62, tag_text, font_size=16, color=PALETTE["muted"])

    headline = (
        "同样的模型循环。\n完全不同的控制面。"
        if is_zh
        else "Same model loop.\nDifferent operator contract."
    )
    headline_y = 118
    headline_fs = 34 if is_zh else 36
    subtitle_fs = 18 if is_zh else 20
    scene.text(56, headline_y, headline, font_size=headline_fs)
    subtitle = (
        "Hermit 会主动放慢副作用，换来过后依然清楚。"
        if is_zh
        else "Hermit slows side effects down so work stays legible afterward."
    )
    subtitle_y = headline_y + text_height(headline, headline_fs) + 16
    scene.text(56, subtitle_y, subtitle, font_size=subtitle_fs, color=PALETTE["muted"])

    column_top = subtitle_y + text_height(subtitle, subtitle_fs) + 30
    scene.rectangle(56, column_top, 492, 474, stroke=PALETTE["line"], fill=PALETTE["panel"])
    scene.rectangle(
        612, column_top, 492, 474, stroke=PALETTE["accent_stroke"], fill=PALETTE["panel"]
    )

    left_tag = "普通 agent" if is_zh else "GENERIC"
    right_tag = "Hermit" if is_zh else "HERMIT"
    left_tag_fs = fit_font_size(left_tag, 15, 112 if is_zh else 96)
    right_tag_fs = fit_font_size(right_tag, 15, 76)
    scene.rectangle(
        82, column_top + 24, 124 if is_zh else 108, 32, stroke=PALETTE["line"], fill="#ece4d9"
    )
    scene.text(104, column_top + 31, left_tag, font_size=left_tag_fs, color=PALETTE["muted"])
    scene.rectangle(638, column_top + 24, 108, 32, stroke=PALETTE["line"], fill=PALETTE["green"])
    scene.text(664, column_top + 31, right_tag, font_size=right_tag_fs, color=PALETTE["muted"])

    left_items = (
        [
            ("动作路径", "model -> tool -> log"),
            ("控制面", "提示词约束"),
            ("运行之后", "trace + transcript"),
            ("恢复方式", "手工清理"),
        ]
        if is_zh
        else [
            ("Action path", "model -> tool -> log"),
            ("Control", "prompt discipline"),
            ("After the run", "traces + transcript"),
            ("Recovery", "manual cleanup"),
        ]
    )
    right_items = (
        [
            ("动作路径", "task -> approval -> receipt"),
            ("控制面", "policy + 权限边界"),
            ("运行之后", "proof + receipt + task state"),
            ("恢复方式", "已支持时可 rollback"),
        ]
        if is_zh
        else [
            ("Action path", "task -> approval -> receipt"),
            ("Control", "policy + scoped authority"),
            ("After the run", "proof + receipts + task state"),
            ("Recovery", "rollback when supported"),
        ]
    )

    def draw_column(items: list[tuple[str, str]], x: int) -> None:
        y = column_top + 70
        for title, body in items:
            scene.rectangle(x, y, 410, 72, stroke=PALETTE["line"], fill=PALETTE["gold"])
            scene.text(x + 20, y + 14, title, font_size=16, color=PALETTE["muted"])
            wrapped = wrap_text(body, 18 if is_zh else 20, 370)
            scene.text(x + 20, y + 34, wrapped, font_size=18 if is_zh else 20)
            y += 88

    draw_column(left_items, 82)
    draw_column(right_items, 638)

    left_summary = "当下快，事后模糊" if is_zh else "Fast now, vague later"
    right_summary = "现在更慢，事后更清楚" if is_zh else "Slower now, clearer later"
    left_summary_fs = fit_font_size(left_summary, 16, 370)
    right_summary_fs = fit_font_size(right_summary, 16, 370)
    summary_y = column_top + 418
    scene.rectangle(82, summary_y, 410, 36, stroke=PALETTE["line"], fill="#f2e7dc")
    scene.text(102, summary_y + 9, left_summary, font_size=left_summary_fs, color=PALETTE["muted"])
    scene.rectangle(638, summary_y, 410, 36, stroke=PALETTE["line"], fill="#e9efe1")
    scene.text(
        658, summary_y + 9, right_summary, font_size=right_summary_fs, color=PALETTE["muted"]
    )
    return scene


def build_scenes() -> dict[str, Scene]:
    return {
        "hermit-differentiators": differentiators_scene(),
        "hermit-differentiators-zh-cn": differentiators_scene("zh-cn"),
        "hermit-governed-path": governed_path_scene(),
        "hermit-governed-path-zh-cn": governed_path_scene("zh-cn"),
        "hermit-architecture-overview": architecture_scene(),
        "hermit-architecture-overview-zh-cn": architecture_scene("zh-cn"),
        "hermit-vs-generic-agent": generic_vs_hermit_scene(),
        "hermit-vs-generic-agent-zh-cn": generic_vs_hermit_scene("zh-cn"),
    }


def main() -> None:
    scenes = build_scenes()
    chrome = ensure_chrome_headless_shell()
    with repo_http_server(ROOT) as base_url:
        for slug, scene in scenes.items():
            scene_path = ASSETS / f"{slug}.excalidraw.json"
            svg_path = ASSETS / f"{slug}.svg"
            write_scene(scene_path, scene)
            render_svg(scene_path, svg_path)
            png_path = render_png(slug, scene.width, scene.height, chrome, base_url)
            print(f"generated {scene_path.relative_to(ROOT)}")
            print(f"generated {svg_path.relative_to(ROOT)}")
            print(f"generated {png_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
