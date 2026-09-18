# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2025 Delos Data, Inc.
"""
The report's header logo.

A report is one file that gets attached to an email, copied to a share or archived with the
run, so the logo has to travel inside it -- an ``<img src>`` pointing at a path on the runner
is a broken image everywhere the file lands. An SVG is inlined; anything else is embedded as
a ``data:`` URI.
"""

import base64
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

__all__ = ["Logo", "DEFAULT_LOGO_PACKAGE_PATH", "load_logo"]

#: The bundled Delos Data lockup, cropped to the artwork and with its wordmark on
#: ``currentColor`` so one asset reads on the report's light and dark themes alike.
DEFAULT_LOGO_PACKAGE_PATH = "delos-data-logo.svg"

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
    #: What a text sink shows in place of the image.
    alt: str = "Delos Data"

    @property
    def is_empty(self) -> bool:
        return not self.markup


def _inline_svg(text: str, alt: str) -> str:
    """
    Prepare an SVG for inlining into the report's own document.

    Inlined rather than wrapped in a ``data:`` URI so the wordmark's ``currentColor`` resolves
    against the report's theme. The XML prolog has to go -- it is only legal at the very start
    of a document, and here the fragment lands mid-``<body>``.
    """
    text = re.sub(r"<\?xml[^>]*\?>\s*", "", text)
    text = re.sub(r"<!DOCTYPE[^>]*>\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<!--.*?-->\s*", "", text, flags=re.S)
    text = text.strip()

    # Height comes from CSS, so drop any the file hardcodes; an SVG with width/height but no
    # viewBox cannot scale at all, so give it one from those dimensions before they go.
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


def load_logo(source: str | Path | None = None, *, alt: str = "Delos Data") -> Logo:
    """
    Load the header logo from *source*, or the bundled Delos Data lockup when it is None.

    Never raises: a report whose logo could not be read is still the run's results, so an
    unreadable or unrecognised file degrades to a report with no image rather than to a failed
    session at the very end of a nightly run.
    """
    try:
        if source is None:
            asset = resources.files(__package__).joinpath("assets", DEFAULT_LOGO_PACKAGE_PATH)
            return Logo(_inline_svg(asset.read_text(encoding="utf-8"), alt), alt)

        path = Path(source).expanduser()
        media_type = _MEDIA_TYPES.get(path.suffix.lower())
        if media_type is None:
            return Logo("", alt)
        if media_type == "image/svg+xml":
            return Logo(_inline_svg(path.read_text(encoding="utf-8"), alt), alt)
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return Logo(f"<img src='data:{media_type};base64,{encoded}' alt='{alt}'>", alt)
    except OSError, UnicodeDecodeError, ModuleNotFoundError:
        return Logo("", alt)
