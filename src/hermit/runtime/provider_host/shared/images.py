from __future__ import annotations

import base64
import io
import math
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Iterator

MAX_INLINE_IMAGE_BYTES = 4 * 1024 * 1024
_JPEG_QUALITIES = (85, 75, 65, 55, 45)
_WEBP_QUALITIES = (80, 65, 50)
_MAX_COMPRESSION_PASSES = 6


def prepare_messages_for_provider(
    messages: list[dict[str, Any]],
    *,
    max_inline_image_bytes: int = MAX_INLINE_IMAGE_BYTES,
) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content", "")
        if not isinstance(content, list):
            prepared.append(dict(message))
            continue
        prepared.append(
            {
                **message,
                "content": [
                    prepare_image_block(
                        cast("dict[str, Any]", block),
                        max_inline_image_bytes=max_inline_image_bytes,
                    )
                    if isinstance(block, dict)
                    else block
                    for block in cast("list[Any]", content)
                ],
            }
        )
    return prepared


def prepare_image_block(
    block: dict[str, Any],
    *,
    max_inline_image_bytes: int = MAX_INLINE_IMAGE_BYTES,
) -> dict[str, Any]:
    if str(block.get("type", "")) != "image":
        return dict(block)
    source = block.get("source", {})
    if not isinstance(source, dict):
        raise ValueError("Invalid image block: missing source")
    typed_source = cast("dict[str, Any]", source)
    if str(typed_source.get("type", "")) != "base64":
        return dict(block)

    media_type = _normalize_media_type(str(typed_source.get("media_type", "")).strip())
    data = str(typed_source.get("data", "")).strip()
    if not data:
        raise ValueError("Invalid image block: empty base64 image data")

    try:
        image_bytes = base64.b64decode(data, validate=False)
    except Exception as exc:
        raise ValueError("Invalid image block: malformed base64 image data") from exc

    if len(image_bytes) > max_inline_image_bytes:
        media_type, image_bytes = compress_image_bytes(
            image_bytes,
            media_type,
            max_bytes=max_inline_image_bytes,
        )

    return {
        **block,
        "source": {
            **typed_source,
            "media_type": media_type,
            "data": base64.b64encode(image_bytes).decode("ascii"),
        },
    }


def compress_image_bytes(
    image_bytes: bytes,
    media_type: str,
    *,
    max_bytes: int = MAX_INLINE_IMAGE_BYTES,
) -> tuple[str, bytes]:
    normalized_media_type = _normalize_media_type(media_type)
    if len(image_bytes) <= max_bytes:
        return normalized_media_type, image_bytes

    best_media_type = normalized_media_type
    best_bytes = image_bytes

    for compressor in (_compress_with_pillow, _compress_with_sips):
        try:
            candidate_media_type, candidate_bytes = compressor(
                image_bytes, normalized_media_type, max_bytes
            )
        except (ImportError, FileNotFoundError):
            continue
        except Exception:
            continue
        if len(candidate_bytes) < len(best_bytes):
            best_media_type = candidate_media_type
            best_bytes = candidate_bytes
        if len(best_bytes) <= max_bytes:
            return best_media_type, best_bytes

    if len(best_bytes) > max_bytes:
        raise ValueError(
            f"Image exceeds {max_bytes} bytes and could not be compressed below the provider limit"
        )
    return best_media_type, best_bytes


def _normalize_media_type(media_type: str) -> str:
    normalized = media_type or "application/octet-stream"
    if normalized == "image/jpg":
        return "image/jpeg"
    return normalized


def _compress_with_pillow(
    image_bytes: bytes,
    media_type: str,
    max_bytes: int,
) -> tuple[str, bytes]:
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(image_bytes)) as opened:
        current = ImageOps.exif_transpose(opened)
        current.load()

    best_media_type = media_type
    best_bytes = image_bytes
    resampling: Any = getattr(Image.Resampling, "LANCZOS", None) or getattr(Image, "LANCZOS", None)

    for _ in range(_MAX_COMPRESSION_PASSES):
        for candidate_media_type, candidate_bytes in _encode_candidates_with_pillow(
            current, media_type
        ):
            if len(candidate_bytes) < len(best_bytes):
                best_media_type = candidate_media_type
                best_bytes = candidate_bytes
            if len(candidate_bytes) <= max_bytes:
                return candidate_media_type, candidate_bytes
        # Incompressible at this resolution: skip further downscaling
        if len(best_bytes) > max_bytes * 2:
            break
        if current.width == 1 and current.height == 1:
            break
        current = _downscale_image(current, len(best_bytes), max_bytes, resampling=resampling)

    return best_media_type, best_bytes


def _encode_candidates_with_pillow(
    image: Any, media_type: str
) -> Iterator[tuple[str, bytes]]:
    """Yield compression candidates lazily so callers can exit early."""
    if media_type == "image/png":
        yield "image/png", _save_png(image)
    if media_type == "image/webp":
        for quality in _WEBP_QUALITIES:
            yield "image/webp", _save_webp(image, quality)
    for quality in _JPEG_QUALITIES:
        yield "image/jpeg", _save_jpeg(image, quality)


def _save_png(image: Any) -> bytes:
    buffer = io.BytesIO()
    working = image
    if getattr(working, "mode", "") not in {"1", "L", "LA", "P", "RGB", "RGBA"}:
        working = working.convert("RGBA")
    working.save(buffer, format="PNG", optimize=True, compress_level=9)
    return buffer.getvalue()


def _save_webp(image: Any, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", quality=quality, method=6)
    return buffer.getvalue()


def _save_jpeg(image: Any, quality: int) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    working = image
    if getattr(working, "mode", "") in {"RGBA", "LA"} or (
        getattr(working, "mode", "") == "P" and "transparency" in getattr(working, "info", {})
    ):
        alpha_ready = working.convert("RGBA")
        flattened = Image.new("RGB", alpha_ready.size, (255, 255, 255))
        flattened.paste(alpha_ready, mask=alpha_ready.getchannel("A"))
        working = flattened
    elif getattr(working, "mode", "") != "RGB":
        working = working.convert("RGB")
    working.save(buffer, format="JPEG", quality=quality, optimize=True, progressive=True)
    return buffer.getvalue()


def _downscale_image(image: Any, current_size: int, max_bytes: int, *, resampling: Any) -> Any:
    ratio = math.sqrt(max_bytes / max(current_size, 1)) * 0.95
    ratio = min(0.85, max(0.5, ratio))
    new_width = max(1, int(image.width * ratio))
    new_height = max(1, int(image.height * ratio))
    if (new_width, new_height) == image.size and image.size != (1, 1):
        new_width = max(1, image.width - 1)
        new_height = max(1, image.height - 1)
    return image.resize((new_width, new_height), resample=resampling)


def _compress_with_sips(
    image_bytes: bytes,
    media_type: str,
    max_bytes: int,
) -> tuple[str, bytes]:
    sips_path = shutil.which("sips")
    if not sips_path:
        raise FileNotFoundError("sips not found")

    suffix = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }.get(media_type, ".img")

    with tempfile.TemporaryDirectory(prefix="hermit-image-compress-") as tmpdir:
        src = Path(tmpdir) / f"input{suffix}"
        src.write_bytes(image_bytes)
        max_dimension = _read_sips_max_dimension(sips_path, src)

        best_media_type = media_type
        best_bytes = image_bytes

        for step in range(_MAX_COMPRESSION_PASSES):
            resize_to = max_dimension if max_dimension is not None else None
            if resize_to is not None:
                resize_to = max(1, int(resize_to * (0.85**step)))
            for quality in _JPEG_QUALITIES:
                out = Path(tmpdir) / f"candidate-{step}-{quality}.jpg"
                command = [sips_path]
                if resize_to is not None:
                    command.extend(["-Z", str(resize_to)])
                command.extend(
                    [
                        "-s",
                        "format",
                        "jpeg",
                        "--setProperty",
                        "formatOptions",
                        str(quality),
                        str(src),
                        "--out",
                        str(out),
                    ]
                )
                subprocess.run(command, check=True, capture_output=True, text=True)
                candidate = out.read_bytes()
                if len(candidate) < len(best_bytes):
                    best_media_type = "image/jpeg"
                    best_bytes = candidate
                if len(candidate) <= max_bytes:
                    return "image/jpeg", candidate
            # Incompressible at this resolution: skip further downscaling
            if len(best_bytes) > max_bytes * 2:
                break

        return best_media_type, best_bytes


def _read_sips_max_dimension(sips_path: str, image_path: Path) -> int | None:
    command = [sips_path, "-g", "pixelWidth", "-g", "pixelHeight", str(image_path)]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    width: int | None = None
    height: int | None = None
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("pixelWidth:"):
            width = int(stripped.split(":", 1)[1].strip())
        elif stripped.startswith("pixelHeight:"):
            height = int(stripped.split(":", 1)[1].strip())
    if width is None or height is None:
        return None
    return max(width, height)
