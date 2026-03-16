---
name: hermit-readme-excalidraw-assets
description: Generate or update Hermit's README launch diagrams and docs hero visuals using Excalidraw scene JSON plus GitHub-safe SVG and PNG exports. Use when editing `docs/assets/*.excalidraw.json`, tightening README visual layout, or regenerating PNGs for GitHub rendering.
---

# Hermit README Excalidraw Assets

Use this skill when README or docs visuals should look like launch material and still render reliably on GitHub.

## Why this exists

GitHub README rendering is less stable with Mermaid and sometimes inconsistent with SVG fonts.

This repo keeps the diagrams in three forms, and now supports locale-specific variants:

- Excalidraw scene JSON for editable source
- SVG for docs and review
- PNG for README GitHub rendering

## Primary files

- `scripts/generate_excalidraw_readme_assets.py`
- `docs/assets/hermit-differentiators.excalidraw.json`
- `docs/assets/hermit-differentiators-zh-cn.excalidraw.json`
- `docs/assets/hermit-governed-path.excalidraw.json`
- `docs/assets/hermit-governed-path-zh-cn.excalidraw.json`
- `docs/assets/hermit-architecture-overview.excalidraw.json`
- `docs/assets/hermit-architecture-overview-zh-cn.excalidraw.json`
- `docs/assets/hermit-vs-generic-agent.excalidraw.json`
- `docs/assets/hermit-vs-generic-agent-zh-cn.excalidraw.json`
- `docs/assets/png/*.png`
- `README.md`
- `README.zh-CN.md`

## Required workflow

1. Edit the scene layout in `scripts/generate_excalidraw_readme_assets.py`.
2. Regenerate all locale variants:

```bash
python3 scripts/generate_excalidraw_readme_assets.py
```

3. Keep README image references pointed at the PNG versions, not Mermaid and not raw SVG.
4. Point `README.zh-CN.md` at the `-zh-cn` PNGs so the Chinese landing page uses real localized visuals.
5. Preview the exported PNGs and check for:
   - title and subtitle not overlapping
   - no big right-side or bottom blank area
   - no clipped pills, arrows, or outer frame
   - no text overflow inside cards
   - locale-specific copy should use the matching localized PNG, not the English fallback

## Layout rules

- Prefer tighter launch-poster spacing over roomy presentation slides.
- Shrink canvas width before shrinking type too aggressively.
- Use `text_height(...)` driven spacing instead of hardcoded vertical guesses when text wraps.
- Keep the hero title area compact so the diagram content reaches the fold quickly.
- For README, favor PNG exports because GitHub rendering is the final target.

## Export rules

The generator script already handles the stable export path:

1. build Excalidraw scene JSON
2. render SVG through Kroki
3. wrap the SVG in a tiny local HTML page
4. export PNG with Playwright Chromium headless shell

Do not replace this with `qlmanage` thumbnails or raw viewport screenshots unless you re-verify whitespace and font behavior.

## Validation

Run at least:

```bash
python3 -m py_compile scripts/generate_excalidraw_readme_assets.py
python3 scripts/generate_excalidraw_readme_assets.py
sips -g pixelWidth -g pixelHeight docs/assets/png/hermit-differentiators.png docs/assets/png/hermit-governed-path.png docs/assets/png/hermit-architecture-overview.png
```

If you need a visual pass, open the PNGs directly from:

- `docs/assets/png/hermit-differentiators.png`
- `docs/assets/png/hermit-governed-path.png`
- `docs/assets/png/hermit-architecture-overview.png`
