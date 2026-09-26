"""Local image snapshots and provider-neutral image transport helpers."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .errors import RequestError

MAX_IMAGE_BYTES = 20 * 1024 * 1024
ImageMediaType = Literal["image/png", "image/jpeg", "image/webp"]
IMAGE_SUFFIXES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


def _media_type(data: bytes) -> ImageMediaType:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("image must have a PNG, JPEG or WebP signature")


class ImageInput(BaseModel):
    """Immutable bytes captured before planning/generation; no source path is retained."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)
    data: bytes = Field(repr=False, min_length=1, max_length=MAX_IMAGE_BYTES)
    media_type: ImageMediaType

    @model_validator(mode="after")
    def validate_signature(self) -> ImageInput:
        if _media_type(self.data) != self.media_type:
            raise ValueError("image signature does not match media_type")
        return self

    @classmethod
    def from_file(cls, path: str | Path) -> ImageInput:
        """Read one local file once, with a bounded read and content-free errors."""
        try:
            with Path(path).expanduser().open("rb") as stream:
                data = stream.read(MAX_IMAGE_BYTES + 1)
        except (OSError, ValueError):
            raise RequestError("cannot read local image file", code="image_io") from None
        try:
            return cls(data=data, media_type=_media_type(data))
        except (ValueError, ValidationError):
            raise RequestError(
                "image must be PNG, JPEG or WebP and at most 20 MiB", code="invalid_image"
            ) from None

    def data_url(self) -> str:
        return f"data:{self.media_type};base64," + base64.b64encode(self.data).decode("ascii")


class ImageMetadata(BaseModel):
    """Content identity only, safe to include in plans and execution records."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    media_type: ImageMediaType
    size_bytes: int = Field(gt=0)


def image_metadata(images: list[ImageInput]) -> list[ImageMetadata]:
    return [
        ImageMetadata(
            sha256=hashlib.sha256(image.data).hexdigest(),
            media_type=image.media_type,
            size_bytes=len(image.data),
        )
        for image in images
    ]


def input_fingerprint(prompt: str, images: list[ImageMetadata]) -> str:
    envelope = {"prompt": prompt, "images": [image.model_dump() for image in images]}
    canonical = json.dumps(envelope, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_image_provider(provider: str, images: list[ImageInput]) -> None:
    if images and provider not in {"codex", "ollama", "azure-openai"}:
        raise RequestError(
            "image input is supported only by codex, ollama and azure-openai", code="unsupported_images"
        )


def message_content(prompt: str, images: list[ImageInput]) -> str | list[dict[str, Any]]:
    if not images:
        return prompt
    return [
        {"type": "text", "text": prompt},
        *[{"type": "image_url", "image_url": {"url": image.data_url()}} for image in images],
    ]
