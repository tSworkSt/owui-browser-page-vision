"""Offline OCR worker process.

Runs RapidOCR out-of-process so the MCP helper server never imports
numpy/ONNX Runtime itself: inside the helper's stdio server the first
native-module load stalled for minutes on the event-loop thread and wedged
every tool call, while this standalone interpreter imports the same
libraries in under a second.

Usage:
  rapidocr_worker.py RESULT.json IMAGE [IMAGE ...]        # loose images
  rapidocr_worker.py RESULT.json --pdf PDF FIRST LAST DPI  # rendered pages

Writes UTF-8 JSON to RESULT.json:
  {"texts": ["...", ...]}  one entry per input, in order
  {"error": "..."}         on failure
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _ocr_images(engine, paths: list[str]) -> list[str]:
    import numpy as np
    from PIL import Image, ImageOps

    texts = []
    for p in paths:
        img = ImageOps.exif_transpose(Image.open(p)).convert("RGB")
        result, _ = engine(np.asarray(img))
        texts.append("\n".join(item[1] for item in result) if result else "")
    return texts


def _ocr_pdf(engine, pdf_path: str, first: int, last: int, dpi: int) -> list[str]:
    import numpy as np
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(pdf_path)
    lo = max(1, first)
    hi = min(len(pdf), last if last else len(pdf))
    texts = []
    for i in range(lo - 1, hi):  # one page at a time; keeps RAM flat
        img = pdf[i].render(scale=dpi / 72.0).to_pil()
        result, _ = engine(np.asarray(img))
        texts.append("\n".join(item[1] for item in result) if result else "")
    return texts


def main(argv: list[str]) -> int:
    out = Path(argv[1])
    try:
        from rapidocr_onnxruntime import RapidOCR

        engine = RapidOCR()
        if argv[2] == "--pdf":
            texts = _ocr_pdf(engine, argv[3], int(argv[4]), int(argv[5]), int(argv[6]))
        else:
            texts = _ocr_images(engine, argv[2:])
        out.write_text(json.dumps({"texts": texts}, ensure_ascii=False), encoding="utf-8")
        return 0
    except Exception as e:  # noqa: BLE001 - report anything back to the parent
        try:
            out.write_text(json.dumps({"error": str(e)}, ensure_ascii=False),
                           encoding="utf-8")
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
