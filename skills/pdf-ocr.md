---
name: pdf-ocr
description: Read PDFs or images that contain scanned pages or pictures instead of selectable text. Prefer extract_pdf (auto-detects image-only PDFs) and ocr_image for standalone image files; both fall back to vision OCR (LM Studio VLM) or local RapidOCR. Use whenever a PDF yields no text or an image contains text the user needs.
---

# Reading Image-Based (Scanned) PDFs

Some PDFs have no text layer: they are just images of pages (scans, exported
slides, photographed documents). Text extraction returns nothing for them — OCR
is required.

## Detect first, then read

1. Call `extract_pdf(path=...)` on the `local-docs-mcp` server. It reads the text layer
   and, if the average text per page is negligible (<40 chars), automatically
   falls back to OCR. Response includes `method: text|vision|local`.

2. If you already know the PDF is scanned, call `ocr_pdf` directly:
   - Engine `vision` (default): pages are rendered and transcribed by a
     vision-capable model in LM Studio. Best quality on complex layouts,
     tables, and handwriting-adjacent content. The server auto-picks a loaded
     VLM; pass `vision_model` to force one. If the model returns no text, or is
     cut off mid-page by the token budget (reasoning models spend most of
     `max_tokens=8000` thinking), that page is automatically re-read with local
     RapidOCR and `method` flags it, e.g.
     `vision:<model> (+local fallback p1:length)`.
   - Engine `local`: RapidOCR (ONNX, fully offline). Good for clean printed
     text when no vision model is loaded. Slightly worse than a VLM on tables
     and layout.

## Standalone images

For an image file (screenshot, photo of a document, whiteboard, chart, slide)
use `ocr_image(path, engine=...)` — same two engines as above (`vision`
default, `local` offline). It applies EXIF rotation, caps huge images, and
returns `{method, width, height, text}`. Use this instead of converting the
image to PDF first.

## Tuning

- Low-quality scans: raise `dpi` to 300 for small print.
- Partial reads: use `first_page` / `last_page` (1-based) to OCR only the
  pages you need — vision OCR on a 27B model is slow, so page ranges matter.
- `last_page=0` means "to the end of the document".

## Rules

- If `read_pdf_text` returns text, do NOT OCR — the text layer is exact.
- OCR is imperfect: numbers can be misread (e.g. `1` vs `l`, `0` vs `O`).
  Flag obvious uncertainties when the user depends on precise numbers from a
  scan.
- If neither path works (e.g. LM Studio has no loadable VLM and local OCR
  errors), say so explicitly and suggest loading a VLM such as
  `qwen2.5-vl` or `gemma-3` in LM Studio.
