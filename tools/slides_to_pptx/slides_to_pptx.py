#!/usr/bin/env python3
"""
Slides-to-PowerPoint pipeline.

Takes a folder of slide photos (iPhone JPEG/HEIC/PNG), detects the slide
rectangle in each image, corrects perspective, crops, de-glares, boosts
contrast and sharpness, and assembles the results into a 16:9 .pptx.

Usage:
    python3 slides_to_pptx.py --input inbox/ --output output/deck.pptx

Optional:
    --processed processed/   # also save cleaned JPEGs
    --order name|mtime       # slide ordering (default: name)
    --target-width 1920      # output image width in pixels
    --no-warp                # skip perspective correction
    --layout image|caption   # slide layout (default: image)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps
from pptx import Presentation
from pptx.util import Emu, Pt

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass  # HEIC support is optional; JPEG/PNG still work.

SUPPORTED_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".webp"}


# ---------- I/O ----------

def load_image(path: Path) -> np.ndarray:
    """Load an image (HEIC-aware) and return BGR uint8."""
    try:
        pil = Image.open(path)
        pil = ImageOps.exif_transpose(pil)  # honor iPhone orientation
        pil = pil.convert("RGB")
    except Exception as e:
        raise RuntimeError(f"could not open {path}: {e}")
    rgb = np.asarray(pil)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def save_jpeg(path: Path, bgr: np.ndarray, quality: int = 92) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])


# ---------- Slide detection & perspective correction ----------

def _order_corners(pts: np.ndarray) -> np.ndarray:
    """Return corners ordered TL, TR, BR, BL."""
    pts = pts.reshape(4, 2).astype(np.float32)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).flatten()
    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    tr = pts[np.argmin(d)]
    bl = pts[np.argmax(d)]
    return np.array([tl, tr, br, bl], dtype=np.float32)


def find_slide_quad(bgr: np.ndarray) -> np.ndarray | None:
    """Return 4 corners of the largest slide-like quadrilateral, or None."""
    h, w = bgr.shape[:2]
    scale = 1000.0 / max(h, w)
    small = cv2.resize(bgr, None, fx=scale, fy=scale)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 9, 75, 75)
    edges = cv2.Canny(gray, 50, 180)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    img_area = small.shape[0] * small.shape[1]
    best: tuple[float, np.ndarray] | None = None

    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:10]:
        area = cv2.contourArea(c)
        if area < img_area * 0.15:  # must cover >=15% of frame
            break
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            if best is None or area > best[0]:
                best = (area, approx)

    if best is None:
        return None
    quad = best[1].astype(np.float32) / scale  # back to full-res coords
    return _order_corners(quad)


def warp_to_rect(bgr: np.ndarray, quad: np.ndarray, aspect: float = 16 / 9) -> np.ndarray:
    """Warp the quadrilateral to a flat rectangle with the target aspect."""
    tl, tr, br, bl = quad
    w_top = np.linalg.norm(tr - tl)
    w_bot = np.linalg.norm(br - bl)
    h_left = np.linalg.norm(bl - tl)
    h_right = np.linalg.norm(br - tr)
    measured_w = (w_top + w_bot) / 2
    measured_h = (h_left + h_right) / 2

    # Pick output size that preserves detail, then force target aspect.
    out_w = int(max(measured_w, measured_h * aspect))
    out_h = int(round(out_w / aspect))
    dst = np.array([[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]], np.float32)
    M = cv2.getPerspectiveTransform(quad, dst)
    return cv2.warpPerspective(bgr, M, (out_w, out_h), flags=cv2.INTER_CUBIC)


# ---------- Fidelity enhancement ----------

def reduce_moire_and_glare(bgr: np.ndarray) -> np.ndarray:
    """Suppress screen moire patterns and tame specular glare."""
    # Edge-preserving smoothing knocks down moire without muddying text.
    smoothed = cv2.bilateralFilter(bgr, d=5, sigmaColor=35, sigmaSpace=7)

    # Detect blown-out glare spots and inpaint them.
    hsv = cv2.cvtColor(smoothed, cv2.COLOR_BGR2HSV)
    v = hsv[:, :, 2]
    s = hsv[:, :, 1]
    glare = ((v > 245) & (s < 25)).astype(np.uint8) * 255
    if glare.mean() > 1:  # only inpaint if a meaningful glare area exists
        glare = cv2.dilate(glare, np.ones((3, 3), np.uint8), iterations=1)
        smoothed = cv2.inpaint(smoothed, glare, 3, cv2.INPAINT_TELEA)
    return smoothed


def enhance_contrast_and_sharpness(bgr: np.ndarray) -> np.ndarray:
    """CLAHE on luminance + unsharp mask for legible text."""
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    balanced = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)

    blur = cv2.GaussianBlur(balanced, (0, 0), sigmaX=1.2)
    sharp = cv2.addWeighted(balanced, 1.5, blur, -0.5, 0)
    return sharp


def process_image(bgr: np.ndarray, warp: bool, target_width: int) -> np.ndarray:
    """Full per-slide pipeline."""
    if warp:
        quad = find_slide_quad(bgr)
        if quad is not None:
            bgr = warp_to_rect(bgr, quad, aspect=16 / 9)
        # If no quad found, fall through with the raw frame; still enhance.

    # Resize to target width (preserve aspect) before enhancement for speed.
    h, w = bgr.shape[:2]
    if w != target_width:
        new_h = int(round(h * target_width / w))
        bgr = cv2.resize(bgr, (target_width, new_h), interpolation=cv2.INTER_AREA)

    bgr = reduce_moire_and_glare(bgr)
    bgr = enhance_contrast_and_sharpness(bgr)
    return bgr


# ---------- PowerPoint assembly ----------

def _fit_picture(slide, img_path: Path, box_left: int, box_top: int,
                 box_w: int, box_h: int) -> None:
    """Add the image centered inside the given box, preserving aspect ratio."""
    with Image.open(img_path) as pil:
        iw, ih = pil.size
    img_ratio = iw / ih
    box_ratio = box_w / box_h
    if img_ratio >= box_ratio:
        width = box_w
        height = int(box_w / img_ratio)
    else:
        height = box_h
        width = int(box_h * img_ratio)
    left = box_left + (box_w - width) // 2
    top = box_top + (box_h - height) // 2
    slide.shapes.add_picture(str(img_path), left, top, width=width, height=height)


def build_pptx(image_paths: list[Path], out_path: Path, layout: str = "image") -> None:
    prs = Presentation()
    # 16:9 at 13.333in x 7.5in (PowerPoint widescreen default).
    prs.slide_width = Emu(12192000)
    prs.slide_height = Emu(6858000)
    blank = prs.slide_layouts[6]

    sw, sh = prs.slide_width, prs.slide_height

    # For the captioned layout, reserve the bottom ~22% of the slide for notes.
    caption_h = int(sh * 0.22) if layout == "caption" else 0
    pad = Emu(228600) if layout == "caption" else 0  # 0.25in gutter

    for img in image_paths:
        slide = prs.slides.add_slide(blank)
        img_box_h = sh - caption_h - (pad if caption_h else 0)
        _fit_picture(slide, img, 0, 0, sw, img_box_h)

        if layout == "caption":
            tb = slide.shapes.add_textbox(
                Emu(457200),                       # 0.5in left margin
                img_box_h + pad,                   # below the image
                sw - Emu(914400),                  # 1in total side margins
                caption_h - pad,
            )
            tf = tb.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.text = "Notes…"
            run = p.runs[0]
            run.font.size = Pt(18)
            run.font.italic = True

    out_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(out_path)


# ---------- CLI ----------

def discover_inputs(folder: Path, order: str) -> list[Path]:
    files = [p for p in folder.iterdir() if p.suffix.lower() in SUPPORTED_EXT]
    if order == "mtime":
        files.sort(key=lambda p: p.stat().st_mtime)
    else:
        files.sort(key=lambda p: p.name.lower())
    return files


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Turn slide photos into a PowerPoint.")
    ap.add_argument("--input", required=True, type=Path, help="Folder of slide photos")
    ap.add_argument("--output", required=True, type=Path, help="Output .pptx path")
    ap.add_argument("--processed", type=Path, help="Optional folder to save cleaned JPEGs")
    ap.add_argument("--order", choices=["name", "mtime"], default="name")
    ap.add_argument("--target-width", type=int, default=1920)
    ap.add_argument("--no-warp", action="store_true", help="Skip perspective correction")
    ap.add_argument("--layout", choices=["image", "caption"], default="image",
                    help="'image' = full-bleed photo; 'caption' = photo + editable notes box")
    args = ap.parse_args(argv)

    if not args.input.is_dir():
        print(f"error: --input {args.input} is not a directory", file=sys.stderr)
        return 2

    inputs = discover_inputs(args.input, args.order)
    if not inputs:
        print(f"error: no supported images in {args.input}", file=sys.stderr)
        return 2

    processed_dir = args.processed or (args.output.parent / "_processed")
    processed_dir.mkdir(parents=True, exist_ok=True)

    cleaned_paths: list[Path] = []
    for i, src in enumerate(inputs, 1):
        print(f"[{i}/{len(inputs)}] {src.name}", flush=True)
        try:
            bgr = load_image(src)
            cleaned = process_image(bgr, warp=not args.no_warp, target_width=args.target_width)
            dst = processed_dir / f"{src.stem}_clean.jpg"
            save_jpeg(dst, cleaned)
            cleaned_paths.append(dst)
        except Exception as e:
            print(f"  ! skipped ({e})", file=sys.stderr)

    if not cleaned_paths:
        print("error: no images processed successfully", file=sys.stderr)
        return 1

    build_pptx(cleaned_paths, args.output, layout=args.layout)
    print(f"wrote {args.output} with {len(cleaned_paths)} slide(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
