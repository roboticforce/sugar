#!/usr/bin/env python3
"""Generate the SugarDash app icon (AppIcon.icns).

Run from apps/macos:
    python3 make-icon.py                 # writes Resources/AppIcon.icns
    python3 make-icon.py --preview       # also writes /tmp/sugardash-icon-1024.png

Requires Pillow (and numpy, optional). The icon is a coral→amber squircle with a
white rounded "S" monogram, matching the app's warm "sugar" accent and staying
legible at 16px.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SIZE = 1024
CORNER_RADIUS = 228
FONT_PATH = "/System/Library/Fonts/SFNSRounded.ttf"
FALLBACK_FONT = "/System/Library/Fonts/HelveticaNeue.ttc"

CORAL = (255, 79, 109)
AMBER = (255, 182, 92)


def linear_gradient(size: int) -> Image.Image:
    try:
        import numpy as np

        y, x = np.mgrid[0:size, 0:size]
        t = (x + y) / (2 * (size - 1))
        c1 = np.array(CORAL, dtype=float)
        c2 = np.array(AMBER, dtype=float)
        arr = (c1 * (1 - t)[..., None] + c2 * t[..., None]).astype("uint8")
        return Image.fromarray(arr, "RGB").convert("RGBA")
    except ImportError:
        # Fallback without numpy: vertical gradient via ImageDraw lines.
        image = Image.new("RGBA", (size, size))
        draw = ImageDraw.Draw(image)
        for y in range(size):
            t = y / (size - 1)
            color = tuple(
                int(c1 + (c2 - c1) * t) for c1, c2 in zip(CORAL, AMBER)
            ) + (255,)
            draw.line([(0, y), (size, y)], fill=color)
        return image


def squircle_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=radius, fill=255
    )
    return mask


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in (FONT_PATH, FALLBACK_FONT):
        try:
            font = ImageFont.truetype(path, size)
            break
        except OSError:
            continue
    else:  # pragma: no cover - macOS always has one of these
        raise SystemExit("no usable system font found")
    # SF Pro Rounded is a variable font; prefer the bold axis when available.
    try:
        names = font.get_variation_names()
        for candidate in (b"Bold", b"Semibold", b"Medium"):
            if candidate in names:
                font.set_variation_by_name(candidate)
                break
    except Exception:
        pass
    return font


def draw_icon() -> Image.Image:
    image = linear_gradient(SIZE)
    image.putalpha(squircle_mask(SIZE, CORNER_RADIUS))

    # Soft top-left highlight so the square doesn't look flat.
    highlight = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    hd = ImageDraw.Draw(highlight)
    hd.ellipse(
        [-SIZE * 0.35, -SIZE * 0.55, SIZE * 0.75, SIZE * 0.35],
        fill=(255, 255, 255, 26),
    )
    highlight = highlight.filter(ImageFilter.GaussianBlur(60))
    highlight.putalpha(Image.composite(highlight.getchannel("A"), Image.new("L", (SIZE, SIZE), 0), squircle_mask(SIZE, CORNER_RADIUS)))
    image = Image.alpha_composite(image, highlight)

    # Inner edge.
    ImageDraw.Draw(image).rounded_rectangle(
        [10, 10, SIZE - 11, SIZE - 11],
        radius=CORNER_RADIUS - 10,
        outline=(255, 255, 255, 18),
        width=4,
    )

    font = load_font(640)
    text = "S"
    bbox = ImageDraw.Draw(image).textbbox((0, 0), text, font=font)
    width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    position = ((SIZE - width) / 2 - bbox[0], (SIZE - height) / 2 - bbox[1] + 6)

    shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text(position, text, font=font, fill=(60, 10, 20, 90))
    image = Image.alpha_composite(image, shadow.filter(ImageFilter.GaussianBlur(18)))

    ImageDraw.Draw(image).text(position, text, font=font, fill=(255, 255, 255, 255))

    return image


def write_icns(master: Image.Image, out: Path) -> None:
    iconset = out.parent / "AppIcon.iconset"
    if iconset.exists():
        for child in iconset.iterdir():
            child.unlink()
    iconset.mkdir(parents=True, exist_ok=True)
    for base in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            pixels = base * scale
            name = f"icon_{base}x{base}{'@2x' if scale == 2 else ''}.png"
            master.resize((pixels, pixels), Image.LANCZOS).save(iconset / name)
    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True
    )
    for child in iconset.iterdir():
        child.unlink()
    iconset.rmdir()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true", help="write a 1024px PNG preview")
    parser.add_argument(
        "--out",
        default=str(Path(__file__).parent / "Resources" / "AppIcon.icns"),
        help="output .icns path",
    )
    args = parser.parse_args()

    master = draw_icon()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_icns(master, out)
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    if args.preview:
        preview = Path("/tmp/sugardash-icon-1024.png")
        master.save(preview)
        print(f"preview {preview}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
