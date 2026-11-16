#!/usr/bin/env python3
"""Render the greeter field from the desktop wallpaper.

The picture stays recognizable under a dark veil, with a soft oval where the
0 rests. That oval's center matches drawPortalFrame. Pillow and numpy are
needed only to regenerate this asset, not to build an ISO.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent
DEFAULT_SPEC = ROOT / "portal-spec.json"
DEFAULT_OUTPUT = ROOT / "airootfs/usr/share/backgrounds/02os/portal-halo.png"
WALLPAPER_CANDIDATES = (
    ROOT / "airootfs/usr/share/backgrounds/02os/desktop.jpg",
    ROOT / "airootfs/usr/share/backgrounds/02os/desktop.png",
    ROOT.parent / "appearance/base/backgrounds/desktop.jpg",
    ROOT.parent / "appearance/base/backgrounds/desktop.png",
    ROOT.parent / "02_OS_repo/vendor/appearance/base/backgrounds/desktop.jpg",
)
# How much of the dark field covers the picture. The wallpaper stays visible.
VEIL = np.float32(0.58)


def load_spec(spec_path: Path = DEFAULT_SPEC) -> dict:
    """Load canonical portal geometry constants."""
    return json.loads(spec_path.read_text(encoding="utf-8"))


def resolve_wallpaper(explicit: Path | None) -> Path:
    """Locate the input wallpaper path or raise a clear error."""
    if explicit is not None:
        if not explicit.is_file():
            raise SystemExit(f"Wallpaper not found: {explicit}")
        return explicit
    for candidate in WALLPAPER_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise SystemExit(
        "No default wallpaper found; pass --wallpaper PATH explicitly."
    )


def cover(path: Path, width: int, height: int) -> Image.Image:
    """Scale and center-crop wallpaper to cover width x height."""
    image = Image.open(path).convert("RGB")
    scale = max(width / image.width, height / image.height)
    resized = image.resize(
        (round(image.width * scale), round(image.height * scale)),
        Image.Resampling.LANCZOS,
    )
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def _bayer8_dither(width: int, height: int, amplitude: float = 3.5) -> np.ndarray:
    """Return deterministic 8x8 ordered Bayer dither in float32 [0..1] RGB units."""
    matrix = np.array([[0]], dtype=np.float32)
    for _ in range(3):
        matrix = np.block([
            [np.float32(4.0) * matrix, np.float32(4.0) * matrix + np.float32(2.0)],
            [np.float32(4.0) * matrix + np.float32(3.0), np.float32(4.0) * matrix + np.float32(1.0)],
        ])
    normalized = ((matrix + np.float32(0.5)) / np.float32(64.0) - np.float32(0.5)) * np.float32(amplitude / 255.0)
    tiled = np.tile(normalized, (height // 8 + 1, width // 8 + 1))[:height, :width]
    return tiled[..., None]


def render_field(wallpaper: Path, width: int, height: int, spec: dict) -> Image.Image:
    """Render the veiled greeter field and quantize cleanly to an 8-bit PNG."""
    picture = np.asarray(cover(wallpaper, width, height), dtype=np.float32) / np.float32(255.0)
    x = np.arange(width, dtype=np.float32)[None, :]
    y = np.arange(height, dtype=np.float32)[:, None]

    # Align canvas fit and zero origin identically with motion.js drawPortalFrame.
    canvas_width = max(1, math.floor(min(width * 0.4, height * 0.66 * 512 / 448)))
    scale = np.float32(canvas_width * 0.59375 / 680.0)
    origin_x = np.float32(float(spec["originX"]))
    origin_y = np.float32(float(spec["originY"]))
    zero_x = np.float32(width / 2.0) + origin_x * scale
    zero_y = np.float32(height / 2.0) + origin_y * scale

    # The logo 0 is 264 units wide and 533 tall. The glow is that capsule,
    # enlarged, so the light sits on the same shape that just expanded.
    half_width = np.float32(132.0) * scale * np.float32(2.35)
    half_height = np.float32(266.5) * scale * np.float32(2.05)
    half_segment = np.float32(max(float(half_height - half_width), 0.0))
    dy_zero = y - zero_y
    along = np.clip(dy_zero, -half_segment, half_segment)
    radial = np.hypot(x - zero_x, dy_zero - along)
    oval_denom = np.float32(2.0) * (half_width * np.float32(0.92)) ** np.float32(2.0)
    oval = np.exp(-(radial * radial) / oval_denom)
    length_scale = half_height * np.float32(1.05)
    length = np.exp(-((np.abs(dy_zero) / length_scale) ** np.float32(2.0)) * np.float32(0.35))
    glow = oval * (np.float32(0.72) + np.float32(0.28) * length)

    # A wider, dimmer pool keeps the centered login card from sitting in a void.
    half_w = np.float32(width / 2.0)
    half_h = np.float32(height / 2.0)
    card = np.exp(-(
        ((x - half_w) / np.float32(width * 0.36)) ** np.float32(2.0)
        + ((y - half_h) / np.float32(height * 0.40)) ** np.float32(2.0)
    ))
    vx = (x - half_w) / half_w
    vy = (y - half_h) / half_h
    vignette = np.clip(
        np.float32(1.0) - np.float32(0.55) * (vx * vx * np.float32(0.85) + vy * vy) ** np.float32(1.2),
        np.float32(0.0),
        np.float32(1.0),
    )

    veil_color = np.array([9, 16, 24], dtype=np.float32) / np.float32(255.0)
    cyan = np.array([0, 186, 243], dtype=np.float32) / np.float32(255.0)
    # Edges fall a little darker so the picture reads in the middle.
    darkness = VEIL + (np.float32(1.0) - VEIL) * np.float32(0.28) * (np.float32(1.0) - vignette)
    rgb = picture * (np.float32(1.0) - darkness[..., None]) + veil_color * darkness[..., None]
    light = np.clip(glow * np.float32(0.10) + card * np.float32(0.03), np.float32(0.0), np.float32(1.0))
    rgb = np.clip(rgb + cyan * light[..., None], np.float32(0.0), np.float32(1.0))

    dithered = np.clip(rgb + _bayer8_dither(width, height), np.float32(0.0), np.float32(1.0))
    rgb_image = Image.fromarray(np.rint(dithered * np.float32(255.0)).astype(np.uint8), "RGB")
    return rgb_image.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)


def main():
    spec = load_spec()
    parser = argparse.ArgumentParser(description="Render the greeter field from a wallpaper.")
    parser.add_argument("--wallpaper", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--width", type=int, default=int(spec["width"]))
    parser.add_argument("--height", type=int, default=int(spec["height"]))
    args = parser.parse_args()

    if args.spec != DEFAULT_SPEC:
        spec = load_spec(args.spec)
    if args.width <= 0 or args.height <= 0:
        raise SystemExit(f"Invalid dimensions: {args.width}x{args.height}")

    wallpaper = resolve_wallpaper(args.wallpaper)
    image = render_field(wallpaper, args.width, args.height, spec)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.output, format="PNG", optimize=True)
    size = args.output.stat().st_size
    print(f"wrote {args.output} {image.size[0]}x{image.size[1]} ({size:,} bytes)")


if __name__ == "__main__":
    main()
