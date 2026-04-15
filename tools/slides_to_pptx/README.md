# slides_to_pptx

Turn a folder of slide photos (iPhone JPEG/HEIC/PNG) into a clean 16:9 PowerPoint deck.

Each photo is:
1. Loaded with EXIF orientation respected (so iPhone rotation Just Works)
2. Perspective-corrected — the slide rectangle is detected and warped flat
3. Cropped to the slide bounds (as a side effect of the warp)
4. De-glared (blown-out highlights inpainted) and slightly bilateral-smoothed to suppress display moire
5. Contrast-boosted via CLAHE on the L channel and sharpened with an unsharp mask

The cleaned images are then assembled into a widescreen `.pptx`, one image per slide, letterboxed to fit.

## Setup

```
pip install opencv-python-headless numpy Pillow python-pptx
```

## Usage

```
python3 slides_to_pptx.py --input inbox/ --output output/deck.pptx
```

Flags:

| flag | default | meaning |
| --- | --- | --- |
| `--input` | *(required)* | folder of photos |
| `--output` | *(required)* | `.pptx` path |
| `--processed` | `<output>/_processed` | where to save cleaned JPEGs |
| `--order` | `name` | `name` or `mtime` |
| `--target-width` | `1920` | output image width in pixels |
| `--no-warp` | off | skip perspective correction (use if the detector mis-fires) |

## Live-capture workflow

1. Shoot slides on iPhone.
2. AirDrop to a Mac (or use iCloud Photos / scp / rsync) so they land in `inbox/`.
3. Re-run the script — it's idempotent, just overwrites the deck.

## Known limits

- **HEIC**: Pillow needs `pillow-heif` installed to read HEIC directly. If that's not available, export as JPEG on the phone first (Settings → Camera → Formats → Most Compatible).
- **Glare**: the inpaint tool handles small specular hotspots; large glare washes are not recoverable from pixels alone.
- **Slide detection**: requires the slide to occupy at least ~15% of the frame and have visible edges. If the detector misses, use `--no-warp` and the raw (EXIF-rotated) frame is used.
