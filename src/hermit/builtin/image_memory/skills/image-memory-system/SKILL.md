---
name: image-memory-system
description: "Use when users refer to previously shared images, screenshots, QR codes, or photos, or when you need to store/search/reuse images across sessions."
---

You can use the cross-session image memory tools to manage and reuse images the user has shared before.

## When to use

- the user says things like “that image from just now,” “the screenshot from last time,” or “the QR code I sent earlier”
- you need to add a local image into long-term system memory
- you need to search historical images by summary, tags, or OCR text
- you need to attach a previously stored image again in a Feishu reply

## Tool overview

- `image_store_from_path`
  - stores a local image in image memory and immediately generates `summary`, `tags`, and `ocr_text`
- `image_search`
  - searches historical images by keyword; the keyword can come from the scenario, object, image text, or intended use
- `image_get`
  - retrieves full metadata for a specific image
- `image_attach_to_feishu`
  - returns a `<feishu_image key='...'/>` tag that can be inserted directly into the final reply

## Usage principles

- when the user refers to an old image, do not guess from memory; search first
- use `image_search` to narrow the scope, then `image_get` to inspect details
- if a tool returns `image_id`, prefer using `image_id` as the stable reference in later steps

## Feishu replies

- keep the tag returned by `image_attach_to_feishu` exactly as-is
- preferably place that tag on its own line instead of in the middle of a sentence
- you can place normal Markdown text before or after the tag

Example:

```md
这是你刚才提到的那张流程图：

<feishu_image key='img_v2_xxx'/>

如果需要，我也可以继续总结图里的关键步骤。
```
