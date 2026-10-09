# owui-browser-page-vision

A heavily vibe-coded set of tools for **Open WebUI**: 
document download/read/OCR, browser automation, and the assistant skills.

```
Open WebUI - mcpo bridge :8091 - local_docs_mcp.py (stdlib MCP server)
structures - skills/*.md
scripts    - owui-mcp.cmd - starts all five bridges
             stop-owui-mcp.ps1 - stops the local-docs bridge safely
```

## Components

| Thing | Name | What it is |
|---|---|---|
| MCP tool server | `local-docs-mcp` | `local_docs_mcp.py` + `rapidocr_worker.py` - the tool server behind port `:8091` |
| Skills | `find-and-read-files` · `pdf-ocr` · `web-page-interaction` | Markdown skill docs loaded by the assistant |
| Launcher | `scripts/owui-mcp.cmd` | Starts all five Open WebUI MCP bridges |
| Stopper | `scripts/stop-owui-mcp.ps1` | Kills the local-docs bridge chain |

## Tools (local-docs-mcp)

| Tool | Purpose |
|---|---|
| `download_url` | Download any useful file type (PDF, images, CSV/JSON, ZIP, DOCX, HTML, …) from an http(s) URL. Never overwrites (` (1)` suffix), 100 MB default / 500 MB hard cap, refuses double-click executables by design. |
| `read_pdf_text` | Embedded text layer via pypdf (fast, exact). |
| `extract_pdf` | Text layer - and if a page averages <40 chars it counts as a scan and falls back to OCR automatically. |
| `ocr_pdf` | Scanned PDF pages via vision (LM Studio VLM) or local (RapidOCR) engine, rendered at configurable DPI. |
| `ocr_image` | Standalone images (PNG/JPG/WEBP/TIFF/GIF…): vision or local OCR, EXIF-rotated, huge images capped. |
| `find_files` / `file_info` | Locate files by name or text content; peek before reading. |

### Vision engine behavior

- Auto-picks a **loaded** VLM from LM Studio's native `/api/v0/models`
  (`type=vlm`);
- `max_tokens=8000`; reasoning models that spend their budget thinking still
  get a full page out.
- If a page/image returns **no text** or was **cut off mid-page**
  (`finish_reason=length`), it is automatically re-read with local RapidOCR
  and `method` says so, e.g. `vision:<model> (+local fallback p1:length)`.

### Runtime roots

Paths resolve from the OS user profile at startup (Windows known-folders
API, falling back to `HOME`); Overrides:
`DOCS_TMP` for the `tmp` root, `LMSTUDIO_URL` for the LM Studio API
(default `http://localhost:1234/v1`).

## Ports (Open WebUI bridges)

| Port | Server |
|---|---|
| 8088 | filesystem (Documents, Desktop, Downloads) |
| 8089 | web fetch |
| 8090 | memory |
| **8091** | **local-docs-mcp** (this repo) |
| 8092 | browser automation (Playwright MCP, Edge) |

## Setup

1. Python 3.12 venv with `mcp`, `pypdf`, `pypdfium2`, `Pillow`.
2. Worker deps (separate venv or same): `rapidocr_onnxruntime`, `numpy`,
   `pypdfium2`, `Pillow`.
3. LM Studio running with at least one VLM; models listed in `/api/v0/models`.
4. Register the bridge in Open WebUI → Admin → Connections as an MCP server:
   URL `http://localhost:8091`.
5. `scripts\owui-mcp.cmd` starts everything; it skips ports already listening.

## License

MIT — see [LICENSE](LICENSE).
