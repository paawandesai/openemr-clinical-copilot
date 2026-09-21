"""
Scan simulation: clean PDF -> image-only PDF with no text layer.

Each page is rasterised at the profile's DPI, put through a fixed pipeline
(paper tint -> skew -> uneven lighting -> blur -> sensor noise -> speckle ->
JPEG) and re-wrapped as a full-bleed image on a page of the original point
size. The result carries no text layer at all, which is the point: an extractor
has to OCR it rather than read a text stream.

The skew is applied through an affine matrix this module builds itself (PIL is
handed the inverse), so the exact clean-layout-point -> scan-pixel mapping is
known rather than inferred, and is recorded in the ground truth's ``transform``
block. Every random draw comes from a seeded ``numpy`` generator.
"""

from __future__ import annotations

import io
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pymupdf
from PIL import Image, ImageFilter

from evals.w2.factory.canvas import canonical_pdf_bytes, set_fixed_metadata
from evals.w2.factory.rng import derive_seed
from evals.w2.factory.spec import Quality


@dataclass(frozen=True)
class ScanProfile:
    name: str
    dpi: int
    #: Page skew is drawn uniformly from +/- this many degrees, per page.
    skew_degrees: float
    #: Gaussian sensor noise, in 0-255 units.
    noise_sigma: float
    blur_radius: float
    jpeg_quality: int
    #: 0 = flat lighting, 1 = heavy falloff at the page edges.
    vignette: float
    #: Amplitude of the smooth left-to-right illumination ramp, 0-1.
    illumination_ramp: float
    #: Fraction of pixels turned into dark specks.
    speckle_density: float
    paper_tint: tuple[int, int, int]


PROFILES: dict[Quality, ScanProfile] = {
    Quality.SCANNED_LIGHT: ScanProfile(
        name="scanned_light",
        dpi=200,
        skew_degrees=0.45,
        noise_sigma=3.2,
        blur_radius=0.35,
        jpeg_quality=82,
        vignette=0.10,
        illumination_ramp=0.05,
        speckle_density=0.00002,
        paper_tint=(252, 251, 247),
    ),
    Quality.SCANNED_HEAVY: ScanProfile(
        name="scanned_heavy",
        dpi=150,
        skew_degrees=1.7,
        noise_sigma=9.5,
        blur_radius=0.95,
        jpeg_quality=42,
        vignette=0.34,
        illumination_ramp=0.20,
        speckle_density=0.00035,
        paper_tint=(243, 239, 229),
    ),
}


def _invert_affine(m: tuple[float, float, float, float, float, float]) -> tuple[float, ...]:
    """Invert ``(a, c, e, b, d, f)`` as used by PIL's AFFINE transform."""
    a, c, e, b, d, f = m
    det = a * d - b * c
    if abs(det) < 1e-12:  # pragma: no cover - skew angles are always small
        raise ValueError("degenerate scan transform")
    return (d / det, -c / det, (c * f - d * e) / det,
            -b / det, a / det, (b * e - a * f) / det)


def _page_transform(width_px: int, height_px: int, scale: float, angle_degrees: float
                    ) -> tuple[float, float, float, float, float, float]:
    """
    Forward map from a point on the clean layout to a pixel in the scan.

        image_x = a*x + c*y + e
        image_y = b*x + d*y + f

    Built as translate(centre) . rotate(angle) . translate(-centre) . scale(dpi/72),
    with the rotation taken in image space (y down), i.e. a positive angle skews
    the page clockwise as printed.
    """
    theta = np.deg2rad(angle_degrees)
    cos_t, sin_t = float(np.cos(theta)), float(np.sin(theta))
    cx, cy = width_px / 2.0, height_px / 2.0
    a = scale * cos_t
    c = -scale * sin_t
    b = scale * sin_t
    d = scale * cos_t
    e = cx - cx * cos_t + cy * sin_t
    f = cy - cx * sin_t - cy * cos_t
    return (a, c, e, b, d, f)


def _lighting_field(shape: tuple[int, int], profile: ScanProfile, rng: np.random.Generator) -> np.ndarray:
    """A smooth multiplicative illumination field in roughly 0.55 - 1.05."""
    height, width = shape
    ys = np.linspace(-1.0, 1.0, height, dtype=np.float32)[:, None]
    xs = np.linspace(-1.0, 1.0, width, dtype=np.float32)[None, :]

    radius = np.sqrt(xs ** 2 + ys ** 2) / np.sqrt(2.0)
    vignette = 1.0 - profile.vignette * radius ** 2

    ramp_angle = float(rng.uniform(0.0, 2.0 * np.pi))
    ramp = (np.cos(ramp_angle) * xs + np.sin(ramp_angle) * ys)
    illumination = 1.0 + profile.illumination_ramp * ramp

    # One broad, off-centre bright patch, the way a platen light falls off.
    px, py = float(rng.uniform(-0.5, 0.5)), float(rng.uniform(-0.5, 0.5))
    patch = 1.0 + 0.05 * np.exp(-(((xs - px) ** 2 + (ys - py) ** 2) / 0.45))

    return (vignette * illumination * patch).astype(np.float32)


def _degrade_page(image: Image.Image, profile: ScanProfile, angle: float,
                  rng: np.random.Generator) -> tuple[Image.Image, tuple[float, ...]]:
    width_px, height_px = image.size
    scale = profile.dpi / 72.0
    # The raster already carries the dpi scale, so PIL is handed the rotation
    # alone (pixel -> pixel). The matrix recorded in the ground truth is the
    # full clean-layout-point -> scan-pixel map, i.e. the rotation composed
    # with that same scale.
    rotation = _page_transform(width_px, height_px, 1.0, angle)
    forward = _page_transform(width_px, height_px, scale, angle)

    skewed = image.transform(
        (width_px, height_px),
        Image.AFFINE,
        _invert_affine(rotation),
        resample=Image.BICUBIC,
        fillcolor=profile.paper_tint,
    )

    array = np.asarray(skewed, dtype=np.float32)
    array *= _lighting_field(array.shape[:2], profile, rng)[:, :, None]
    degraded = Image.fromarray(np.clip(array, 0, 255).astype(np.uint8), mode="RGB")

    if profile.blur_radius > 0:
        degraded = degraded.filter(ImageFilter.GaussianBlur(profile.blur_radius))

    array = np.asarray(degraded, dtype=np.float32)
    array += rng.normal(0.0, profile.noise_sigma, size=array.shape).astype(np.float32)

    if profile.speckle_density > 0:
        count = int(array.shape[0] * array.shape[1] * profile.speckle_density)
        if count:
            rows = rng.integers(0, array.shape[0], size=count)
            cols = rng.integers(0, array.shape[1], size=count)
            array[rows, cols] = rng.uniform(10.0, 70.0, size=(count, 1)).astype(np.float32)

    return Image.fromarray(np.clip(array, 0, 255).astype(np.uint8), mode="RGB"), forward


def scan(clean_pdf: bytes, quality: Quality, seed: int, scenario_id: str) -> tuple[bytes, dict[str, Any]]:
    """
    Rasterise, degrade and re-wrap ``clean_pdf``.

    Returns the image-only PDF and the ``transform`` block for the ground truth.
    Raises ``AssertionError`` if any output page still carries extractable text.
    """
    profile = PROFILES[quality]
    source = pymupdf.open(stream=clean_pdf, filetype="pdf")
    output = pymupdf.open()
    pages: list[dict[str, Any]] = []

    for index in range(source.page_count):
        rng = np.random.default_rng(derive_seed(seed, "scan", scenario_id, str(index)))
        page = source[index]
        pixmap = page.get_pixmap(dpi=profile.dpi, colorspace=pymupdf.csRGB, alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)

        angle = float(rng.uniform(-profile.skew_degrees, profile.skew_degrees))
        degraded, forward = _degrade_page(image, profile, angle, rng)

        buffer = io.BytesIO()
        degraded.save(buffer, format="JPEG", quality=profile.jpeg_quality, optimize=False, progressive=False)
        jpeg = buffer.getvalue()

        rect = page.rect
        new_page = output.new_page(width=rect.width, height=rect.height)
        new_page.insert_image(pymupdf.Rect(0, 0, rect.width, rect.height), stream=jpeg)

        pages.append(
            {
                "page": index + 1,
                "skew_degrees": round(angle, 5),
                "image_size_px": [degraded.width, degraded.height],
                "page_size_pt": [round(rect.width, 3), round(rect.height, 3)],
                "pdf_points_to_image_px": [round(v, 8) for v in forward],
                "jpeg_bytes": len(jpeg),
            }
        )

    set_fixed_metadata(output, title=f"{scenario_id} (scanned)", subject="synthetic scanned document")
    raw = canonical_pdf_bytes(output.tobytes(garbage=4, deflate=True))

    verify = pymupdf.open(stream=raw, filetype="pdf")
    for index in range(verify.page_count):
        leftover = verify[index].get_text("text").strip()
        assert not leftover, f"{scenario_id}: scanned page {index + 1} still has a text layer: {leftover[:80]!r}"

    transform = {
        "kind": "scan",
        "profile": asdict(profile),
        "mapping": (
            "image_x = a*x + c*y + e ; image_y = b*x + d*y + f, with "
            "pdf_points_to_image_px = [a, c, e, b, d, f] and (x, y) a point on the clean layout "
            "in PyMuPDF page space (top-left origin, y down)."
        ),
        "pages": pages,
    }
    return raw, transform
