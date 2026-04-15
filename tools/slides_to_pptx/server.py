#!/usr/bin/env python3
"""
Phone-friendly upload server for slides_to_pptx.

Open the printed URL in your iPhone's browser, tap "Choose Files" to pick
photos from your Photo Library, upload them, then tap "Build deck" to
download a processed .pptx.

Run:
    python3 server.py                # binds 0.0.0.0:8000
    python3 server.py --port 9000
"""

from __future__ import annotations

import argparse
import shutil
import socket
import sys
import tempfile
import uuid
from pathlib import Path

from flask import Flask, Response, render_template_string, request, send_file
from werkzeug.utils import secure_filename

import slides_to_pptx  # local module

HERE = Path(__file__).parent
INBOX = HERE / "inbox"
PROCESSED = HERE / "processed"
OUTPUT = HERE / "output"

for d in (INBOX, PROCESSED, OUTPUT):
    d.mkdir(exist_ok=True)

ALLOWED_EXT = slides_to_pptx.SUPPORTED_EXT

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200 MB per request

PAGE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Slides to PowerPoint</title>
  <style>
    :root { color-scheme: light dark; }
    * { box-sizing: border-box; }
    body { font: 17px -apple-system, system-ui, sans-serif;
           margin: 0; padding: 24px 18px 80px; max-width: 560px;
           margin-inline: auto; }
    h1 { font-size: 1.4rem; margin: 0 0 6px; }
    p.sub { margin: 0 0 22px; opacity: 0.7; }
    .card { border: 1px solid rgba(128,128,128,0.3); border-radius: 14px;
            padding: 18px; margin-bottom: 18px; }
    .count { font-size: 2.2rem; font-weight: 600; margin: 0; }
    .count small { font-size: 0.9rem; font-weight: 400; opacity: 0.6;
                   margin-left: 6px; }
    label.picker, button, .btn { display: block; width: 100%;
      padding: 14px 16px; margin-top: 10px; border-radius: 12px;
      border: 0; font-size: 1rem; font-weight: 600;
      text-align: center; cursor: pointer; text-decoration: none; }
    label.picker { background: #007aff; color: white; }
    button.primary, .btn.primary { background: #34c759; color: white; }
    button.secondary, .btn.secondary { background: transparent;
      color: #ff3b30; border: 1px solid currentColor; }
    input[type=file] { display: none; }
    select { width: 100%; padding: 12px; border-radius: 10px;
             border: 1px solid rgba(128,128,128,0.4);
             background: transparent; font-size: 1rem; }
    .status { margin-top: 10px; font-size: 0.9rem; opacity: 0.8;
              min-height: 1.2em; }
    ul.files { list-style: none; padding: 0; margin: 12px 0 0;
               font-size: 0.9rem; opacity: 0.75; max-height: 160px;
               overflow-y: auto; }
    ul.files li { padding: 4px 0; border-bottom: 1px solid
                  rgba(128,128,128,0.15); }
    progress { width: 100%; height: 10px; margin-top: 8px; }
  </style>
</head>
<body>
  <h1>Slides → PowerPoint</h1>
  <p class="sub">Pick slide photos. Enhance. Export a 16:9 deck.</p>

  <div class="card">
    <p class="count">{{ count }}<small>photo{{ '' if count==1 else 's' }} queued</small></p>
    {% if names %}
      <ul class="files">{% for n in names %}<li>{{ n }}</li>{% endfor %}</ul>
    {% endif %}

    <form id="upload" method="post" action="/upload" enctype="multipart/form-data">
      <label class="picker" for="file">+ Add photos</label>
      <input id="file" name="photos" type="file" accept="image/*,.heic,.heif"
             multiple onchange="document.getElementById('upload').submit()">
    </form>
    <progress id="bar" value="0" max="100" style="display:none"></progress>
    <div class="status" id="status"></div>
  </div>

  <div class="card">
    <label for="layout" style="font-size:0.9rem;opacity:0.75">Slide layout</label>
    <select id="layout" name="layout">
      <option value="image">Full-bleed photo</option>
      <option value="caption">Photo + notes box</option>
    </select>
    <form method="post" action="/build" id="buildform">
      <input type="hidden" name="layout" id="layoutInput" value="image">
      <button type="submit" class="primary"
              {% if count == 0 %}disabled{% endif %}>Build deck</button>
    </form>
    <form method="post" action="/reset" onsubmit="return confirm('Discard all queued photos?')">
      <button type="submit" class="secondary"
              {% if count == 0 %}disabled{% endif %}>Clear queue</button>
    </form>
  </div>

<script>
  const layoutSel = document.getElementById('layout');
  const layoutInput = document.getElementById('layoutInput');
  layoutSel.addEventListener('change', () => layoutInput.value = layoutSel.value);

  // AJAX upload with progress so the page doesn't feel frozen on big batches.
  const form = document.getElementById('upload');
  const bar = document.getElementById('bar');
  const status = document.getElementById('status');
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const data = new FormData(form);
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/upload');
    xhr.upload.onprogress = (ev) => {
      if (!ev.lengthComputable) return;
      bar.style.display = 'block';
      bar.value = (ev.loaded / ev.total) * 100;
      status.textContent = 'Uploading ' + Math.round(bar.value) + '%';
    };
    xhr.onload = () => {
      if (xhr.status === 200) { window.location.reload(); }
      else { status.textContent = 'Upload failed: ' + xhr.status; }
    };
    xhr.onerror = () => { status.textContent = 'Network error.'; };
    xhr.send(data);
  });
</script>
</body>
</html>
"""


def _queued_files() -> list[Path]:
    return sorted(
        (p for p in INBOX.iterdir()
         if p.is_file() and p.suffix.lower() in ALLOWED_EXT),
        key=lambda p: p.stat().st_mtime,
    )


@app.route("/")
def index() -> str:
    files = _queued_files()
    return render_template_string(PAGE, count=len(files), names=[f.name for f in files])


@app.route("/upload", methods=["POST"])
def upload() -> tuple[str, int]:
    uploaded = request.files.getlist("photos")
    saved = 0
    for f in uploaded:
        if not f.filename:
            continue
        ext = Path(f.filename).suffix.lower()
        if ext not in ALLOWED_EXT:
            continue
        # Prefix with a timestamp-ish unique token so multiple batches preserve order.
        unique = uuid.uuid4().hex[:8]
        safe = secure_filename(f.filename) or f"photo{ext}"
        dst = INBOX / f"{unique}_{safe}"
        f.save(dst)
        saved += 1
    return f"saved {saved}", 200


@app.route("/reset", methods=["POST"])
def reset() -> Response:
    for p in _queued_files():
        p.unlink(missing_ok=True)
    return Response(status=303, headers={"Location": "/"})


@app.route("/build", methods=["POST"])
def build() -> Response:
    files = _queued_files()
    if not files:
        return Response("no photos queued", status=400)

    layout = request.form.get("layout", "image")
    if layout not in {"image", "caption"}:
        layout = "image"

    # Process each photo and write to a fresh processed-subdir per build,
    # so sequential builds don't interfere.
    run_id = uuid.uuid4().hex[:8]
    run_processed = PROCESSED / run_id
    run_processed.mkdir(parents=True, exist_ok=True)

    cleaned: list[Path] = []
    for src in files:
        try:
            bgr = slides_to_pptx.load_image(src)
            clean = slides_to_pptx.process_image(bgr, warp=True, target_width=1920)
            dst = run_processed / f"{src.stem}_clean.jpg"
            slides_to_pptx.save_jpeg(dst, clean)
            cleaned.append(dst)
        except Exception as e:
            app.logger.warning("skip %s: %s", src.name, e)

    if not cleaned:
        shutil.rmtree(run_processed, ignore_errors=True)
        return Response("all photos failed to process", status=500)

    out_path = OUTPUT / f"deck_{run_id}.pptx"
    slides_to_pptx.build_pptx(cleaned, out_path, layout=layout)
    return send_file(out_path, as_attachment=True, download_name="deck.pptx")


# ---------- server boot ----------

def _lan_ip() -> str:
    """Best-effort LAN IP so the printed URL is reachable from your phone."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    print(f"\nOpen on your phone:  http://{_lan_ip()}:{args.port}")
    print(f"Also available at:   http://127.0.0.1:{args.port}\n", flush=True)
    app.run(host=args.host, port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
