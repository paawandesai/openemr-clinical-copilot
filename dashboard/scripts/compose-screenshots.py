"""Composes docs/dashboard/screenshots/side-by-side-pid{20,49}.png from the
PNGs that `SCREENSHOTS=1 npm run test:e2e -- screenshots` writes.
Needs Pillow. Run from anywhere: python3 dashboard/scripts/compose-screenshots.py
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[2] / "docs" / "dashboard" / "screenshots"


def label(img: Image.Image, text: str) -> Image.Image:
    bar = Image.new("RGB", (img.width, 44), (17, 24, 39))
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 22)
    except OSError:
        font = ImageFont.load_default()
    ImageDraw.Draw(bar).text((14, 10), text, fill=(255, 255, 255), font=font)
    out = Image.new("RGB", (img.width, img.height + 44), "white")
    out.paste(bar, (0, 0))
    out.paste(img, (0, 44))
    return out


def stack(imgs: list[Image.Image], gap: int = 12) -> Image.Image:
    out = Image.new("RGB", (max(i.width for i in imgs), sum(i.height for i in imgs) + gap * (len(imgs) - 1)), (230, 230, 230))
    y = 0
    for i in imgs:
        out.paste(i, (0, y))
        y += i.height + gap
    return out


for pid in (20, 49):
    php = stack([Image.open(OUT / f"php-pid{pid}-{part}.png").convert("RGB") for part in ("header", "cards", "labs")])
    left = label(php, f"Before: OpenEMR PHP dashboard (pid {pid})")
    new = Image.open(OUT / f"new-pid{pid}.png").convert("RGB")
    new = new.crop((0, 0, new.width, min(new.height, max(left.height - 44, 1900))))
    right = label(new, f"After: React port over FHIR (pid {pid}, lab list cropped)")
    out = Image.new("RGB", (left.width + right.width + 24, max(left.height, right.height)), (200, 200, 200))
    out.paste(left, (0, 0))
    out.paste(right, (left.width + 24, 0))
    out.save(OUT / f"side-by-side-pid{pid}.png", optimize=True)
    print(pid, out.size)
