# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2025 Delos Data, Inc.
"""
The report's header logo.
"""

import base64
import re
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Logo", "load_logo"]

_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}


@dataclass(frozen=True)
class Logo:
    """A header logo, already self-contained: *markup* embeds no external reference."""

    markup: str
    alt: str = "logo"

    @property
    def is_empty(self) -> bool:
        return not self.markup


def _inline_svg(text: str, alt: str) -> str:
    """
    Prepare an SVG for inlining into the report's own document.
    """
    text = re.sub(r"<\?xml[^>]*\?>\s*", "", text)
    text = re.sub(r"<!DOCTYPE[^>]*>\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<!--.*?-->\s*", "", text, flags=re.S)
    text = text.strip()

    if "viewBox" not in text:
        width = re.search(r'\swidth="([\d.]+)', text)
        height = re.search(r'\sheight="([\d.]+)', text)
        if width and height:
            text = text.replace("<svg", f'<svg viewBox="0 0 {width.group(1)} {height.group(1)}"', 1)
    opening = re.match(r"<svg[^>]*>", text)
    if opening:
        stripped = re.sub(r'\s(?:width|height)="[^"]*"', "", opening.group(0))
        if "aria-label" not in stripped:
            stripped = stripped.replace("<svg", f'<svg role="img" aria-label="{alt}"', 1)
        text = stripped + text[opening.end() :]
    return text


def load_logo(source: str | Path, *, alt: str = "logo") -> Logo:
    """
    Load the header logo from *source*. No logo is bundled: a suite that wants one in its
    report passes its own with ``--report-logo``.
    """
    try:
        path = Path(source).expanduser()
        media_type = _MEDIA_TYPES.get(path.suffix.lower())
        if media_type is None:
            return Logo("", alt)
        if media_type == "image/svg+xml":
            return Logo(_inline_svg(path.read_text(encoding="utf-8"), alt), alt)
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return Logo(f"<img src='data:{media_type};base64,{encoded}' alt='{alt}'>", alt)
    except OSError, UnicodeDecodeError:
        return Logo("", alt)
