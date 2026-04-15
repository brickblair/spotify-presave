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
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

`pillow-heif` is optional but recommended — without it, iPhone HEIC files
are skipped. JPEG/PNG work either way. `Flask` is only needed for the
phone-upload server (below).

## Phone-upload mode (recommended for live capture)

```
python3 server.py                  # binds 0.0.0.0:8000
python3 server.py --port 9000
```

On start it prints two URLs — use the LAN one from your phone's browser.
From there:

1. Tap **+ Add photos** → iOS photo picker opens → select slides → Upload.
2. Repeat as you shoot more; the queue shows what's pending.
3. Pick a layout (full-bleed or photo + notes box).
4. Tap **Build deck** → the .pptx downloads to your phone.
5. **Clear queue** resets for the next event.

The same image pipeline runs server-side, so HEIC uploads, perspective
correction, de-glare, and sharpening all Just Work.

## CLI mode (for batches already on disk)

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
| `--layout` | `image` | `image` (full-bleed photo) or `caption` (photo + editable notes box) |

## Live-capture workflow

1. Shoot slides on iPhone.
2. AirDrop to a Mac (or use iCloud Photos / scp / rsync) so they land in `inbox/`.
3. Re-run the script — it's idempotent, just overwrites the deck.

## Known limits

- **Glare**: the inpaint tool handles small specular hotspots; large glare washes are not recoverable from pixels alone.
- **Slide detection**: requires the slide to occupy at least ~15% of the frame and have visible edges. If the detector misses, use `--no-warp` and the raw (EXIF-rotated) frame is used.
- **HEIC**: needs `pillow-heif`; otherwise export JPEG on the phone (Settings → Camera → Formats → Most Compatible).
