---
name: find-and-read-files
description: Locate files on this PC by filename or by the text inside them, then read them (including scanned/image-based PDFs). Uses the `local-docs-mcp` tool server. Use whenever the user asks to find, open, or summarize their local documents.
---

# Find and Read Files

The `local-docs-mcp` tool server gives you direct access to the user's machine.

## Search workflow

1. **By name:** `find_files(name_pattern='*.pdf', root='downloads')`. A plain
   word like `invoice` is treated as `*invoice*`. Roots allowed: `home`,
   `documents`, `downloads`, `desktop`, `tmp`, `data`. Start with `documents`
   / `downloads` / `desktop` — they are the most relevant.
2. **By content:** if you know a phrase or identifier inside the file, pass
   `content_regex` — e.g. `find_files(root='documents', content_regex='PowerPlan')`.
   Only text files are scanned (2 MB cap each).
3. **Peek:** `file_info(path=..., preview_chars=2000)` on the best candidate to
   confirm it is the right one before reading it entirely.
4. **Read it:** if it is a PDF use `read_pdf_text` / `extract_pdf` (see the
   pdf-ocr skill for scanned PDFs); if it is an image with text in it, use
   `ocr_image`. Otherwise `file_info` already returned the
   preview; for longer text files call `file_info` again with a larger
   `preview_chars`.

## Get a file from the web

`download_url(url, dest_root='downloads', subdir='', filename='', max_mb=100)`
saves **any** file type from an http(s) URL to this machine — PDF,
CSV/JSON/TXT, images, ZIP, DOCX/XLSX, HTML, datasets, subtitles — and returns
its absolute `path`.

1. Get the direct file URL (a browser snapshot's `/url:` line, page source).
2. `download_url(url=..., subdir='topic')` — organize with `subdir`;
   the default landing spot is Downloads.
3. Read it with the normal flow: `extract_pdf` / `read_pdf_text` for PDFs,
   `file_info` for everything else.

Never use the `fetch` tool on binary files — it returns raw bytes as text
garbage; `download_url` is the correct tool. Executables/installers (.exe,
.msi, .bat, .ps1, ...) are refused on purpose: those go through the user's
browser. If the saved file turns out to be an HTML page, you used a page URL
instead of the file URL — grab the real file link and retry. Existing files
are never overwritten (` (1)` is appended).

## Rules

- Always confirm a file exists and is a file (not a directory) before reading;
  `find_files` results are trusted paths.
- If the user says "my documents" or "downloads", prefer those exact roots.
- Files uploaded into chats are stored under root `data` → `uploads\`
  (e.g. `find_files(name_pattern='*.xlsx', root='data')`). `/mnt/uploads/`
  is real but is NOT a host path — it exists only inside the code
  interpreter's in-browser (Pyodide) sandbox, so never pass it to host file
  tools; inside `execute_code` it is valid. Host paths like `C:\mnt\...`
  do not exist; resolve unknown locations with `find_files` first.
- Do not read files the user has not asked about beyond what is needed to
  answer; never summarize or quote sensitive content (passwords, private
  keys) back verbatim.
- Never claim you cannot access files the user referenced. If they named a
  file or a previous read produced text, work from that text; if you need the
  file again, call `extract_pdf` / `read_pdf_text` / `ocr_image` / `file_info`
  instead of guessing. If a tool call fails, say exactly which call failed and
  with what error — do not hedge with "I don't have access".
- Windows paths come back with backslashes — report them as-is.
