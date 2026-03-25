from __future__ import annotations

import base64
import io
import subprocess
from pathlib import Path

import pytest

from hermit.runtime.provider_host.llm.claude import ClaudeProvider
from hermit.runtime.provider_host.llm.codex import (
    CodexOAuthProvider,
    CodexOAuthTokenManager,
    CodexProvider,
)
from hermit.runtime.provider_host.shared import images as provider_images
from hermit.runtime.provider_host.shared.contracts import ProviderRequest
from hermit.runtime.provider_host.shared.images import prepare_messages_for_provider


def test_prepare_messages_for_provider_compresses_base64_images(monkeypatch) -> None:
    monkeypatch.setattr(
        "hermit.runtime.provider_host.shared.images.compress_image_bytes",
        lambda image_bytes, media_type, *, max_bytes: ("image/jpeg", b"tiny"),
    )

    messages = prepare_messages_for_provider(
        [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.b64encode(b"0123456789").decode("ascii"),
                        },
                    }
                ],
            }
        ],
        max_inline_image_bytes=5,
    )

    source = messages[0]["content"][0]["source"]
    assert source["media_type"] == "image/jpeg"
    assert base64.b64decode(source["data"]) == b"tiny"


def test_prepare_messages_for_provider_keeps_url_images() -> None:
    original = {
        "role": "user",
        "content": [
            {
                "type": "image",
                "source": {"type": "url", "url": "https://example.com/a.png"},
            }
        ],
    }

    messages = prepare_messages_for_provider([original])

    assert messages == [original]


def test_claude_provider_payload_uses_shared_image_preparation(monkeypatch) -> None:
    prepared_messages = [{"role": "user", "content": "prepared"}]
    monkeypatch.setattr(
        "hermit.runtime.provider_host.llm.claude.prepare_messages_for_provider",
        lambda messages: prepared_messages,
    )

    provider = ClaudeProvider(client=object(), model="claude-test")
    payload = provider._payload(
        ProviderRequest(
            model="claude-test",
            max_tokens=128,
            messages=[{"role": "user", "content": "original"}],
        )
    )

    assert payload["messages"] == prepared_messages


def test_codex_provider_payload_uses_shared_image_preparation(monkeypatch, tmp_path) -> None:
    prepared_messages = [{"role": "user", "content": "prepared"}]
    monkeypatch.setattr(
        "hermit.runtime.provider_host.llm.codex.prepare_messages_for_provider",
        lambda messages: prepared_messages,
    )

    provider = CodexProvider(api_key="test-key", model="gpt-5.4", cwd=tmp_path)
    payload = provider._payload(
        ProviderRequest(
            model="gpt-5.4",
            max_tokens=128,
            messages=[{"role": "user", "content": "original"}],
        )
    )

    assert payload["input"] == [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "prepared"}],
        }
    ]


def test_codex_oauth_provider_payload_uses_shared_image_preparation(monkeypatch, tmp_path) -> None:
    prepared_messages = [{"role": "user", "content": "prepared"}]
    monkeypatch.setattr(
        "hermit.runtime.provider_host.llm.codex.prepare_messages_for_provider",
        lambda messages: prepared_messages,
    )
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        '{"tokens":{"access_token":"header.eyJleHAiOiA0MTAyNDQ0ODAwLCAiY2xpZW50X2lkIjogImFwcCJ9.sig","refresh_token":"rt"}}',
        encoding="utf-8",
    )

    provider = CodexOAuthProvider(
        token_manager=CodexOAuthTokenManager(auth_path=auth_path),
        model="gpt-5.4",
    )
    payload = provider._payload(
        ProviderRequest(
            model="gpt-5.4",
            max_tokens=128,
            messages=[{"role": "user", "content": "original"}],
        )
    )

    assert payload["input"] == [{"type": "message", "role": "user", "content": "prepared"}]


def test_prepare_image_block_validation_and_passthrough(monkeypatch) -> None:
    assert provider_images.prepare_image_block({"type": "text", "text": "hello"}) == {
        "type": "text",
        "text": "hello",
    }

    with pytest.raises(ValueError, match="missing source"):
        provider_images.prepare_image_block({"type": "image", "source": "bad"})

    with pytest.raises(ValueError, match="empty base64 image data"):
        provider_images.prepare_image_block(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": "   "},
            }
        )

    monkeypatch.setattr(
        provider_images.base64,
        "b64decode",
        lambda data, validate=False: (_ for _ in ()).throw(ValueError("bad base64")),
    )

    with pytest.raises(ValueError, match="malformed base64 image data"):
        provider_images.prepare_image_block(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": "abc"},
            }
        )


def test_compress_image_bytes_handles_short_circuit_success_and_failure(monkeypatch) -> None:
    assert provider_images.compress_image_bytes(b"tiny", "image/jpg", max_bytes=10) == (
        "image/jpeg",
        b"tiny",
    )

    monkeypatch.setattr(
        provider_images,
        "_compress_with_pillow",
        lambda image_bytes, media_type, max_bytes: (_ for _ in ()).throw(
            ImportError("missing pillow")
        ),
    )
    monkeypatch.setattr(
        provider_images,
        "_compress_with_sips",
        lambda image_bytes, media_type, max_bytes: ("image/jpeg", b"small"),
    )

    assert provider_images.compress_image_bytes(b"x" * 20, "image/jpg", max_bytes=10) == (
        "image/jpeg",
        b"small",
    )

    monkeypatch.setattr(
        provider_images,
        "_compress_with_pillow",
        lambda image_bytes, media_type, max_bytes: ("image/png", b"x" * 12),
    )
    monkeypatch.setattr(
        provider_images,
        "_compress_with_sips",
        lambda image_bytes, media_type, max_bytes: (_ for _ in ()).throw(
            RuntimeError("sips failed")
        ),
    )

    with pytest.raises(ValueError, match="could not be compressed below the provider limit"):
        provider_images.compress_image_bytes(b"x" * 20, "image/png", max_bytes=10)


def test_encode_candidates_and_downscale_image(monkeypatch) -> None:
    monkeypatch.setattr(provider_images, "_save_png", lambda image: b"png")
    monkeypatch.setattr(
        provider_images, "_save_webp", lambda image, quality: f"webp-{quality}".encode("ascii")
    )
    monkeypatch.setattr(
        provider_images, "_save_jpeg", lambda image, quality: f"jpeg-{quality}".encode("ascii")
    )

    png_candidates = list(provider_images._encode_candidates_with_pillow(object(), "image/png"))
    webp_candidates = list(provider_images._encode_candidates_with_pillow(object(), "image/webp"))

    assert png_candidates[0] == ("image/png", b"png")
    assert png_candidates[-1][0] == "image/jpeg"
    assert any(candidate[0] == "image/webp" for candidate in webp_candidates)
    assert provider_images._normalize_media_type("") == "application/octet-stream"

    class FakeImage:
        def __init__(self, width: int, height: int) -> None:
            self.width = width
            self.height = height
            self.size = (width, height)

        def resize(self, size: tuple[int, int], resample=None):
            return FakeImage(*size)

    downscaled = provider_images._downscale_image(
        FakeImage(100, 50), current_size=10_000, max_bytes=100, resampling="LANCZOS"
    )
    assert downscaled.size[0] < 100
    assert downscaled.size[1] < 50


def test_save_helpers_and_compress_with_pillow(monkeypatch) -> None:
    from PIL import Image

    png_bytes = provider_images._save_png(Image.new("CMYK", (4, 4)))
    jpeg_rgba = provider_images._save_jpeg(Image.new("RGBA", (4, 4), (255, 0, 0, 128)), 80)
    jpeg_gray = provider_images._save_jpeg(Image.new("L", (4, 4), 128), 80)

    class FakeWebpImage:
        def save(self, buffer, *, format, quality, method) -> None:
            buffer.write(b"webp")

    assert png_bytes.startswith(b"\x89PNG")
    assert jpeg_rgba.startswith(b"\xff\xd8")
    assert jpeg_gray.startswith(b"\xff\xd8")
    assert provider_images._save_webp(FakeWebpImage(), 80) == b"webp"

    source = io.BytesIO()
    Image.new("RGBA", (8, 8), (255, 0, 0, 128)).save(source, format="PNG")

    monkeypatch.setattr(
        provider_images,
        "_encode_candidates_with_pillow",
        lambda image, media_type: (
            [("image/png", b"x" * 200)] if image.width > 1 else [("image/jpeg", b"x" * 50)]
        ),
    )
    monkeypatch.setattr(
        provider_images,
        "_downscale_image",
        lambda image, current_size, max_bytes, *, resampling: image.resize(
            (1, 1), resample=resampling
        ),
    )

    media_type, compressed = provider_images._compress_with_pillow(
        source.getvalue(), "image/png", 100
    )

    assert media_type == "image/jpeg"
    assert compressed == b"x" * 50


def test_sips_helpers_parse_dimensions_and_compress(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], *, check: bool, capture_output: bool, text: bool):
        calls.append(cmd)
        if "-g" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="pixelWidth: 300\npixelHeight: 200\n", stderr=""
            )
        out = Path(cmd[cmd.index("--out") + 1])
        out.write_bytes(b"tiny")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(provider_images.shutil, "which", lambda name: "/usr/bin/sips")
    monkeypatch.setattr(provider_images.subprocess, "run", fake_run)

    assert provider_images._read_sips_max_dimension("/usr/bin/sips", Path("/tmp/image.png")) == 300
    assert provider_images._compress_with_sips(b"raw-image-bytes", "image/png", 10) == (
        "image/jpeg",
        b"tiny",
    )
    assert any("--out" in cmd for cmd in calls)

    monkeypatch.setattr(
        provider_images.subprocess,
        "run",
        lambda cmd, *, check, capture_output, text: subprocess.CompletedProcess(
            cmd,
            0,
            stdout="pixelWidth: 300\n",
            stderr="",
        ),
    )

    assert provider_images._read_sips_max_dimension("/usr/bin/sips", Path("/tmp/image.png")) is None
