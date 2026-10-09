r"""Local Files & PDF MCP server for Open WebUI.

Tools:
  find_files        - fast local file search by name and/or content (regex)
  read_pdf_text     - extract the embedded text layer of a PDF (pypdf)
  ocr_pdf           - OCR an image-based (scanned) PDF
                      * engine="vision"  -> LM Studio vision model (default, best)
                      * engine="local"   -> RapidOCR (ONNX, offline fallback)
  extract_pdf       - smart PDF reader: text layer first, then vision OCR
  ocr_image         - OCR/vision any image file (screenshot, photo, scan)
  file_info         - metadata + text preview for a file
  download_url      - download any file type (PDF, CSV, JSON, images, ZIP,
                      DOCX, HTML, datasets, ...) from an http(s) URL to disk

Runs over stdio; wrap with mcpo to expose as an OpenAPI tool server to Open WebUI.

Dependencies: mcp==1.30.0  pypdfium2  pypdf  pillow   (+ optional rapidocr-onnxruntime for local OCR)
"""

from __future__ import annotations

import base64
import io
import json
import mimetypes
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Scope -- every path the model can touch must live under one of these roots
# ---------------------------------------------------------------------------
HOME = Path(os.environ.get("USERPROFILE") or str(Path.home()))


def _known_folder(guid: str, default: Path) -> Path:
    """Resolve a Windows known folder (registry 'User Shell Folders')."""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as k:
            v, _ = winreg.QueryValueEx(k, "{%s}" % guid)
        v = os.path.expandvars(v)
        if v and os.path.isdir(v):
            return Path(v)
    except OSError:
        pass
    return default


GUID_DOWNLOADS = "374DE290-123F-4565-9164-39C4925E467B"
GUID_DESKTOP = "B4BFCC3A-DB2C-424C-B029-7FE99A87C641"
GUID_DOCUMENTS = "A520A1A4-1780-4FF6-BD18-167343C5AF16"

ROOTS = {
    "home": HOME,
    "documents": _known_folder(GUID_DOCUMENTS, HOME / "Documents"),
    "downloads": _known_folder(GUID_DOWNLOADS, HOME / "Downloads"),
    "desktop": _known_folder(GUID_DESKTOP, HOME / "Desktop"),
    "tmp": Path(os.environ.get("DOCS_TMP") or tempfile.gettempdir()),
    "data": HOME / ".open-webui-data",
}

EXCLUDE_DIRS = {
    "AppData",  # huge, noisy
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".cache",
    "ms-playwright",
    "plugin-staging",
    ".local",
    "uv",
    "npm-cache",
    "scoop",
    ".mypy_cache",
    ".pytest_cache",
    "System Volume Information",
    "$RECYCLE.BIN",
}

MAX_SCAN_SECONDS = 25.0   # abandon a scan past this wall-clock budget
MAX_FILES_SCANNED = 250_000

# Text-ish extensions considered for content search
TEXT_EXTS = {
    ".txt", ".md", ".log", ".json", ".jsonl", ".csv", ".tsv", ".xml", ".html",
    ".htm", ".css", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".py", ".pyw",
    ".ipynb", ".sh", ".ps1", ".cmd", ".bat", ".ini", ".cfg", ".conf", ".yaml",
    ".yml", ".toml", ".env", ".sql", ".c", ".h", ".cpp", ".hpp", ".java",
    ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".cs", ".vue", ".svelte",
    ".rst", ".tex", ".bib", ".properties", ".lock",
}

MAX_CONTENT_FILE = 2 * 1024 * 1024  # skip content scan above 2 MB
MAX_RESULTS = 100

# Double-click-dangerous extensions download_url refuses; those come from the
# user's browser, not from a model-driven tool.
BLOCKED_DOWNLOAD_EXTS = {
    ".exe", ".msi", ".bat", ".cmd", ".ps1", ".vbs", ".scr", ".com",
    ".pif", ".dll", ".cpl", ".reg", ".lnk",
}
DOWNLOAD_CHUNK = 1024 * 1024
MAX_DOWNLOAD_MB_HARD = 500

LMSTUDIO_URL = os.environ.get("LMSTUDIO_URL", "http://localhost:1234/v1")

# Substrings that mark a model id as likely vision-capable
VISION_HINTS = (
    "vl", "vision", "vlm", "llava", "gemma-3", "gemma3", "minicpm", "internvl",
    "qwen3-vl", "qwen2.5-vl", "qwen2-vl", "phi-4-multimodal", "olmocr", "pixtral",
    "glm-4v", "bonsai", "prism", "molmo",
)


def _resolve(root_key: str, rel: str | None = None) -> Path:
    root = ROOTS.get(root_key)
    if root is None:
        raise ValueError(f"unknown root '{root_key}'; allowed: {sorted(ROOTS)}")
    root = root.resolve()
    if rel:
        p = (root / rel).resolve()
        if not (p == root or root in p.parents):
            raise ValueError(f"path escapes root '{root_key}': {p}")
        return p
    return root


def _as_dict(path: Path, match_line: str | None = None) -> dict:
    try:
        st = path.stat()
        return {
            "path": str(path),
            "name": path.name,
            "size": st.st_size,
            "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            "match": match_line[:400] if match_line else None,
        }
    except OSError:
        return {"path": str(path), "name": path.name, "match": match_line}


def _walker(base: Path):
    """Yield files under root with a wall-clock budget and junction/symlink
    pruning. Stops early (truncated=True) instead of hanging on huge trees."""
    state = {"truncated": False, "count": 0, "deadline": time.monotonic() + MAX_SCAN_SECONDS}

    def gen():
        for dirpath, dirnames, filenames in os.walk(base, topdown=True, followlinks=False):
            dirnames[:] = [
                d for d in dirnames
                if d not in EXCLUDE_DIRS
                and not os.path.islink(os.path.join(dirpath, d))
            ]
            for fn in filenames:
                state["count"] += 1
                if state["count"] % 500 == 0 and time.monotonic() >= state["deadline"]:
                    state["truncated"] = True
                    return
                if state["count"] > MAX_FILES_SCANNED:
                    state["truncated"] = True
                    return
                yield Path(dirpath) / fn

    return gen(), state


# ---------------------------------------------------------------------------
# find_files
# ---------------------------------------------------------------------------
mcp = FastMCP("Local Files & PDF")


@mcp.tool()
def find_files(
    name_pattern: str = "*",
    root: str = "home",
    content_regex: str = "",
    case_sensitive: bool = False,
    max_results: int = 50,
) -> str:
    """Find files on this machine by filename pattern and optionally by content.

    - name_pattern: glob like '*.pdf', 'report*', or '*' for everything. A plain
      substring also works (it is wrapped in *...*). Case-insensitive by default.
    - root: search root. Allowed: home, documents, downloads, desktop, tmp, data.
    - content_regex: if given, only files whose *text content* matches the regex
      are returned (text-like files only, 2 MB cap each). Use when you know a
      phrase/identifier inside the file.
    - max_results: cap on returned matches.
    Returns JSON object: {"results": [{path, name, size, modified, match?}, ...],
    "truncated": true|false, "scanned": n}. 'truncated' is set when the scan hit
    its time/file budget before finishing — refine the pattern/root and retry.
    """
    base = _resolve(root)
    pat = name_pattern if name_pattern else "*"
    if not any(c in pat for c in "*?["):
        pat = f"*{pat}*"
    flags = 0 if case_sensitive else re.IGNORECASE
    name_re = re.compile(fnmatch_to_regex(pat), flags)

    content_re = None
    if content_regex:
        try:
            content_re = re.compile(content_regex, flags)
        except re.error as e:
            return json.dumps({"error": f"bad content_regex: {e}"})

    results: list[dict] = []
    gen, state = _walker(base)
    for f in gen:
        if not name_re.match(f.name):
            continue
        extra = None
        if content_re is not None:
            if f.suffix.lower() not in TEXT_EXTS or f.stat().st_size > MAX_CONTENT_FILE:
                continue
            extra = _scan_content(f, content_re)
            if extra is None:
                continue
        results.append(_as_dict(f, extra))
        if len(results) >= min(max_results, MAX_RESULTS):
            break
    out = {
        "results": results[: min(max_results, MAX_RESULTS)],
        "truncated": state["truncated"],
        "scanned": state["count"],
    }
    return json.dumps(out, ensure_ascii=False)


def _scan_content(path: Path, regex: re.Pattern) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh, 1):
                if regex.search(line):
                    return f"line {i}: {line.rstrip()[:300]}"
    except (OSError, UnicodeError):
        return None
    return None


def fnmatch_to_regex(pattern: str) -> str:
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        i += 1
        if c == "*":
            out.append(".*")
        elif c == "?":
            out.append(".")
        elif c == "[":
            j = i
            if j < n and pattern[j] in "!^":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 1
            if j >= n:
                out.append("\\[")
            else:
                stuff = pattern[i:j].replace("\\", "\\\\")
                if stuff.startswith("!"):
                    stuff = "^" + stuff[1:]
                out.append(f"[{stuff}]")
                i = j + 1
        else:
            out.append(re.escape(c))
    return "".join(out) + "$"


@mcp.tool()
def file_info(path: str, preview_chars: int = 2000) -> str:
    """Return metadata and a text preview for an absolute file path.

    Use after find_files to peek at a candidate. Binary files return metadata
    plus 'binary' flag; PDFs should use read_pdf_text / ocr_pdf instead.
    """
    p = Path(path).resolve()
    if not p.is_file():
        return json.dumps({"error": f"not a file: {path}"})
    d = _as_dict(p)
    if p.suffix.lower() in TEXT_EXTS or p.suffix.lower() in {".err", ".out"}:
        try:
            d["preview"] = p.read_text(encoding="utf-8", errors="replace")[:preview_chars]
        except OSError as e:
            d["preview"] = f"<unreadable: {e}>"
    else:
        d["binary"] = True
    return json.dumps(d, ensure_ascii=False)


# ---------------------------------------------------------------------------
# download_url
# ---------------------------------------------------------------------------
def _download_target(
    name_opt: str,
    url_path: str,
    disposition: str | None,
    ctype: str,
    dest_dir: Path,
) -> Path:
    """Pick a safe, collision-free target path inside dest_dir."""
    cand = (name_opt or "").strip()
    if not cand and disposition:
        m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", disposition, re.I)
        if m:
            cand = urllib.parse.unquote(m.group(1)).strip()
    if not cand:
        cand = Path(url_path.replace("\\", "/")).name
    # remove anything that could steer out of dest_dir or break the filesystem
    cand = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", cand).strip(" .")
    if not cand:
        cand = "download"
    if len(cand) > 140:
        p0 = Path(cand)
        cand = p0.stem[: 140 - len(p0.suffix)] + p0.suffix
    if not Path(cand).suffix:
        ext = mimetypes.guess_extension(ctype) or ""
        if ext == ".jpe":
            ext = ".jpg"
        cand += ext
    target = dest_dir / cand
    if target.suffix.lower() in BLOCKED_DOWNLOAD_EXTS:
        raise ValueError(
            f"refusing to save '{target.name}' ({target.suffix}): "
            "executables/installers must be downloaded through the user's "
            "browser, not this tool"
        )
    stem, suffix = target.stem, target.suffix
    n = 1
    while target.exists():  # never overwrite an existing file
        target = dest_dir / f"{stem} ({n}){suffix}"
        n += 1
    return target


@mcp.tool()
def download_url(
    url: str,
    dest_root: str = "downloads",
    subdir: str = "",
    filename: str = "",
    max_mb: int = 100,
) -> str:
    """Download a file from a URL and save it on this machine — any useful
    type: PDF, CSV/JSON/TXT/MD, images, ZIP, DOCX/XLSX, HTML, subtitles,
    datasets, small model weights, etc.

    - url: direct http(s) file URL. In a browser snapshot, take it from the
      link's '/url:' line. If the URL serves an HTML page, that HTML page is
      what gets saved — find the real file link instead.
    - dest_root: save location. Allowed: downloads (default), documents,
      desktop, tmp, data, home.
    - subdir: optional subfolder under dest_root, created automatically
      (e.g. subdir='research-papers' -> Downloads\\research-papers).
    - filename: optional exact name including extension. Default: name taken
      from the URL, else the server's Content-Disposition header, else
      inferred from the content type. Existing files are never overwritten —
      ' (1)', ' (2)'... is appended instead.
    - max_mb: size cap in MB (default 100, hard max 500); the partial file is
      deleted when the cap is exceeded.
    Returns JSON: {"path", "name", "size", "content_type", "source_url",
    "seconds"}. Next step: read PDFs with read_pdf_text/extract_pdf, preview
    anything else with file_info, locate files later with find_files.
    Executables (.exe, .msi, .bat, .ps1, ...) are refused by design.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return json.dumps({"error": f"only http/https URLs are supported, got: {url[:60]}"})
    if not parsed.netloc:
        return json.dumps({"error": "malformed URL (no host)"})
    try:
        max_mb = max(1, min(int(max_mb), MAX_DOWNLOAD_MB_HARD))
    except (TypeError, ValueError):
        return json.dumps({"error": "max_mb must be an integer"})
    limit = max_mb * DOWNLOAD_CHUNK
    try:
        dest_dir = _resolve(dest_root, subdir or None)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    dest_dir.mkdir(parents=True, exist_ok=True)

    req = urllib.request.Request(url, headers={
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
    })
    start = time.time()
    target: Path | None = None
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            final_url = r.geturl()
            if urllib.parse.urlparse(final_url).scheme not in ("http", "https"):
                return json.dumps({"error": f"redirected to unsupported URL: {final_url}"})
            ctype = (r.headers.get("Content-Type") or "application/octet-stream") \
                .split(";")[0].strip()
            clen = r.headers.get("Content-Length")
            if clen:
                try:
                    if int(clen) > limit:
                        return json.dumps({"error":
                            f"remote file is {int(clen) / DOWNLOAD_CHUNK:.1f} MB, "
                            f"over the {max_mb} MB cap"})
                except ValueError:
                    pass
            try:
                target = _download_target(
                    filename, parsed.path, r.headers.get("Content-Disposition"),
                    ctype, dest_dir,
                )
            except ValueError as e:
                return json.dumps({"error": str(e)})

            written = 0
            with open(target, "wb") as fh:
                while True:
                    chunk = r.read(DOWNLOAD_CHUNK)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > limit:
                        fh.close()
                        target.unlink(missing_ok=True)
                        return json.dumps({"error":
                            f"download exceeded the {max_mb} MB cap; "
                            "partial file deleted"})
                    fh.write(chunk)
    except urllib.error.HTTPError as e:
        return json.dumps({"error": f"HTTP {e.code} {e.reason} for {url}"})
    except OSError as e:
        if target is not None:
            target.unlink(missing_ok=True)
        return json.dumps({"error": f"download failed: {getattr(e, 'reason', e)}"})

    return json.dumps({
        "path": str(target),
        "name": target.name,
        "size": written,
        "content_type": ctype,
        "source_url": final_url,
        "seconds": round(time.time() - start, 1),
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# PDF tools
# ---------------------------------------------------------------------------
@mcp.tool()
def read_pdf_text(path: str, first_page: int = 1, last_page: int = 0) -> str:
    """Extract the embedded text layer from a PDF using pypdf (fast, no OCR).

    Use this for normal, text-based PDFs. If it returns little or no text, the
    PDF is image-based and you should call ocr_pdf instead.
    first_page/last_page are 1-based; last_page=0 means 'to the end'.
    """
    p = Path(path).resolve()
    if not p.is_file():
        return json.dumps({"error": f"not a file: {path}"})
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(p))
        total = len(reader.pages)
        lo = max(1, first_page)
        hi = min(total, last_page if last_page else total)
        parts = []
        for i in range(lo - 1, hi):
            txt = (reader.pages[i].extract_text() or "").strip()
            parts.append(f"--- page {i + 1} ---\n{txt}")
        joined = "\n\n".join(parts)
        return json.dumps(
            {"pages": total, "extracted": hi - lo + 1, "text": joined[:250_000]},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": f"read_pdf_text failed: {e}"})


@mcp.tool()
def ocr_pdf(path: str, first_page: int = 1, last_page: int = 0, dpi: int = 220,
            engine: str = "vision", vision_model: str = "") -> str:
    """Read text from an image-based (scanned) PDF.

    engine:
      "vision" (default) - render pages to images and transcribe them with a
        vision-capable model served by LM Studio (best quality, uses your GPU).
        If vision_model is empty it auto-picks a loaded model that looks
        vision-capable; if none is loaded, the error tells you which to add.
      "local" - RapidOCR (ONNX, fully offline; no model server needed).

    dpi controls render resolution (150-300; higher = better on small print).
    Returns JSON {pages, method, text} with per-page sections.
    """
    p = Path(path).resolve()
    if not p.is_file():
        return json.dumps({"error": f"not a file: {path}"})
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(str(p))
        total = len(pdf)
        lo = max(1, first_page)
        hi = min(total, last_page if last_page else total)
        parts = []
        start = time.time()

        if engine == "vision":
            model = _pick_vision_model(vision_model)
            if model is None:
                return json.dumps({
                    "error": (
                        "no vision-capable model is loaded in LM Studio. Load one "
                        "(e.g. qwen2.5-vl, gemma-3, minicpm-v, llava) or call "
                        "ocr_pdf with engine='local' for the offline RapidOCR path. "
                        f"Loaded models: {_lmstudio_models()}"
                    )
                })
            fell_back = []
            for i in range(lo - 1, hi):
                img = pdf[i].render(scale=dpi / 72.0).to_pil()
                if max(img.size) > 2000:  # bound the payload; VLMs downscale anyway
                    img = img.copy()
                    img.thumbnail((2000, 2000))
                txt, fin = _vision_transcribe(img, model)
                txt = (txt or "").strip()
                if not txt or fin == "length":
                    # Empty: reasoning-model loops burn the budget and return
                    # nothing. "length": a non-empty but truncated transcription
                    # (e.g. a reasoning model that spends most of max_tokens
                    # thinking, then gets cut off mid-page). Both are failures;
                    # RapidOCR reads the same page reliably.
                    txt = _local_ocr(pdf=(str(p), i + 1, i + 1, dpi))[0].strip()
                    fell_back.append(f"p{i + 1}:{'length' if fin == 'length' else 'empty'}")
                parts.append(f"--- page {i + 1} ---\n{txt}")
            method = f"vision:{model}"
            if fell_back:
                method += " (+local fallback " + ",".join(fell_back) + ")"
        elif engine == "local":
            texts = _local_ocr(pdf=(str(p), lo, hi, dpi))
            for j, txt in enumerate(texts):
                parts.append(f"--- page {lo + j} ---\n{txt}")
            method = "local:RapidOCR"
        else:
            return json.dumps({"error": f"unknown engine '{engine}' (vision|local)"})

        return json.dumps(
            {"pages": total, "method": method, "seconds": round(time.time() - start, 1),
             "text": "\n\n".join(parts)[:250_000]},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": f"ocr_pdf failed: {e}"})


@mcp.tool()
def extract_pdf(path: str, engine: str = "vision") -> str:
    """Read any PDF smartly: try the embedded text layer first; if the extracted
    text is negligible (<40 chars/page on average), fall back to OCR
    (engine: "vision" via LM Studio, or "local" via RapidOCR).
    Returns JSON {method: 'text'|'vision'|'local', pages, text}.
    """
    p = Path(path).resolve()
    if not p.is_file():
        return json.dumps({"error": f"not a file: {path}"})
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(p))
        total = len(reader.pages)
        texts = [(reader.pages[i].extract_text() or "").strip() for i in range(total)]
        chars = sum(len(t) for t in texts)
        if total and chars / total >= 40:
            joined = "\n\n".join(f"--- page {i+1} ---\n{t}" for i, t in enumerate(texts))
            return json.dumps({"method": "text", "pages": total, "text": joined[:250_000]},
                              ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"extract_pdf text pass failed: {e}"})
    return ocr_pdf(str(p), engine=engine)


# ---------------------------------------------------------------------------
# Image OCR / vision
# ---------------------------------------------------------------------------
@mcp.tool()
def ocr_image(path: str, engine: str = "vision", vision_model: str = "") -> str:
    """Read all text visible in an image file (PNG/JPG/WEBP/BMP/TIFF/GIF —
    screenshots, photos of documents or whiteboards, charts, slides, memes).

    engine:
      "vision" (default) - transcribe with a vision-capable model served by
        LM Studio (best quality, uses the GPU). Auto-picks a loaded VLM;
        pass vision_model to force a specific one. If none is loaded the
        error says which to add.
      "local" - RapidOCR (ONNX, fully offline; no model server needed).
    Only the first frame of animated GIFs / multi-page TIFFs is read.
    Returns JSON {method, width, height, seconds, text}.
    """
    p = Path(path).resolve()
    if not p.is_file():
        return json.dumps({"error": f"not a file: {path}"})
    try:
        if p.stat().st_size > 128 * 1024 * 1024:
            return json.dumps({"error": "image is larger than 128 MB"})
        from PIL import Image, ImageOps

        img = ImageOps.exif_transpose(Image.open(p)).convert("RGB")
        w, h = img.size
        start = time.time()

        if engine == "vision":
            model = _pick_vision_model(vision_model)
            if model is None:
                return json.dumps({
                    "error": (
                        "no vision-capable model is loaded in LM Studio. Load one "
                        "(e.g. qwen2.5-vl, gemma-3, minicpm-v, llava) or call "
                        "ocr_image with engine='local' for the offline RapidOCR path. "
                        f"Loaded models: {_lmstudio_models()}"
                    )
                })
            work = img
            if max(work.size) > 2000:  # bound the base64 payload
                work = work.copy()
                work.thumbnail((2000, 2000))
            txt, fin = _vision_transcribe(work, model)
            txt = (txt or "").strip()
            method = f"vision:{model}"
            if not txt or fin == "length":
                # Empty or token-truncated vision content is a failed read
                # (reasoning loops / budget spent on thinking); use RapidOCR.
                reason = "length" if fin == "length" else "empty"
                txt = _local_ocr(img_paths=[str(p)])[0].strip()
                method += f" (+local fallback:{reason})"
        elif engine == "local":
            txt = _local_ocr(img_paths=[str(p)])[0]
            method = "local:RapidOCR"
        else:
            return json.dumps({"error": f"unknown engine '{engine}' (vision|local)"})

        return json.dumps(
            {"method": method, "width": w, "height": h,
             "seconds": round(time.time() - start, 1),
             "text": (txt or "").strip()[:100_000]},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": f"ocr_image failed: {e}"})


# ---------------------------------------------------------------------------
# LM Studio vision helpers
# ---------------------------------------------------------------------------
def _lmstudio_models() -> list[str]:
    try:
        with urllib.request.urlopen(f"{LMSTUDIO_URL}/models", timeout=10) as r:
            return [m["id"] for m in json.load(r)["data"]]
    except Exception:
        return []


def _pick_vision_model(preferred: str) -> str | None:
    models = _lmstudio_models()
    if preferred and preferred in models:
        return preferred
    # Authoritative first: the native API reports vision type and load state,
    # so we can prefer a loaded VLM and skip broken entries (ids containing
    # '@?' are incomplete downloads that fail to load -> HTTP 400).
    native: list[dict] = []
    try:
        base = LMSTUDIO_URL.rsplit("/v1", 1)[0]
        with urllib.request.urlopen(f"{base}/api/v0/models", timeout=10) as r:
            j = json.load(r)
        items = j.get("data", j) if isinstance(j, dict) else j
        native = [x for x in items if isinstance(x, dict)]
    except Exception:
        native = []
    if native:
        vlms = [x for x in native
                if x.get("type") == "vlm" and "@?" not in str(x.get("id", ""))]
        for want_loaded in (True, False):
            for x in vlms:
                if (x.get("state") == "loaded") == want_loaded:
                    return x["id"]
    # Last resort: name heuristics on the OpenAI list (still skip broken ids).
    for m in models:
        low = m.lower()
        if "@?" in m:
            continue
        if any(h in low for h in VISION_HINTS):
            return m
    return None


def _vision_transcribe(img, model: str) -> tuple[str | None, str]:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    mime = "image/png"
    if buf.tell() > 2_000_000:  # photo-like pages: JPEG is far smaller
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=88)
        mime = "image/jpeg"
    b64 = base64.b64encode(buf.getvalue()).decode()
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text",
                 "text": "Transcribe ALL text in this document image exactly as written, "
                         "preserving reading order line by line, top to bottom. "
                         "If you are unsure about a character or symbol, pick your first "
                         "best guess and move on - never deliberate, explain, or refuse. "
                         "Output only the transcription, no commentary."},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }],
        "temperature": 0.0,
        "max_tokens": 8000,
    }
    req = urllib.request.Request(
        f"{LMSTUDIO_URL}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            j = json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:800]
        raise RuntimeError(
            f"LM Studio rejected the vision request (HTTP {e.code}): {body}"
        ) from None
    choice = j["choices"][0]
    return choice["message"].get("content"), choice.get("finish_reason", "")


# ---------------------------------------------------------------------------
# Local OCR (engine="local"): runs RapidOCR in a worker subprocess.
# numpy/ONNX never load inside this server process - their first native
# load here stalled for minutes on the event-loop thread and wedged every
# tool call, while the standalone worker imports them in ~1s. The worker
# also renders PDF pages itself, one at a time, so RAM stays flat.
# ---------------------------------------------------------------------------
def _local_ocr(img_paths: list[str] | None = None,
               pdf: tuple[str, int, int, int] | None = None) -> list[str]:
    """OCR loose images (img_paths) or PDF page range (pdf=path,first,last,dpi).

    Returns exactly one text string per image/page; on worker failure every
    entry is the same "[local-ocr ...]" error string, so callers still zip 1:1.
    """
    tmp = ROOTS["tmp"]
    tmp.mkdir(exist_ok=True)
    result_fp = tmp / f"_ocr_{os.getpid()}_{time.time_ns()}.json"
    worker = Path(__file__).with_name("rapidocr_worker.py")
    n = len(img_paths or []) or (max(0, (pdf[2] or 0) - pdf[1] + 1) if pdf else 1)
    args = [sys.executable, str(worker), str(result_fp)]
    if pdf:
        args += ["--pdf", pdf[0], str(pdf[1]), str(pdf[2]), str(pdf[3])]
    else:
        args += list(img_paths or [])
    try:
        subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                       text=True, timeout=max(120, 60 * n))
        if not result_fp.is_file():
            return ["[local-ocr worker produced no result]"] * max(1, n)
        j = json.loads(result_fp.read_text(encoding="utf-8"))
        if "error" in j:
            return [f"[local-ocr worker error: {j['error']}]"] * max(1, n)
        texts = list(j.get("texts") or [])
        while len(texts) < n:
            texts.append("")
        return texts[:n]
    except subprocess.TimeoutExpired:
        return [f"[local-ocr worker timed out after {max(120, 60 * n)}s]"] * max(1, n)
    finally:
        result_fp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
def _selftest() -> int:
    from PIL import Image, ImageDraw, ImageFont

    tmp = ROOTS["tmp"]
    tmp.mkdir(exist_ok=True)
    img_path = tmp / "_ocr_selftest.pdf"

    img = Image.new("RGB", (1000, 1300), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 42)
    except Exception:
        font = ImageFont.load_default()
    for y, ln in enumerate(
        ["INVOICE #007", "PowerPlan quarterly simulation report",
         "Total amount: 1 234.56 PLN", "Customer: Lodz Innovation Heroes",
         "Deadline: 2026-10-15"], start=2
    ):
        d.text((80, y * 110), ln, fill="black", font=font)
    img.save(img_path, "PDF", resolution=220)
    print(f"[selftest] wrote {img_path}")

    # text layer pass should find nothing (image-only PDF)
    t = json.loads(read_pdf_text(str(img_path)))
    print(f"[selftest] read_pdf_text chars: {len(t.get('text',''))} (expect ~0)")

    # local OCR path
    res = json.loads(ocr_pdf(str(img_path), engine="local"))
    text = res.get("text", "")
    ok = all(k in text for k in ("INVOICE", "PowerPlan", "1 234.56", "Innovation Heroes"))
    print(f"[selftest] local OCR method={res.get('method')} ok={ok}")
    print("[selftest] first 250 chars:", text[:250].replace("\n", " | "))

    # vision path: report which model would be used (may be none)
    model = _pick_vision_model("")
    print(f"[selftest] vision model auto-pick: {model!r}")

    img_path.unlink(missing_ok=True)
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    mcp.run(transport="stdio")