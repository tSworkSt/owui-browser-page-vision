---
name: web-page-interaction
description: Interact with live web pages — navigate, click, fill forms, extract text and screenshots — using the `browser` tool server. Use when the user wants something done on a website or when dynamic content (JS-rendered pages, logins, forms) is needed instead of a plain fetch.
---

# Web Page Interaction

The `browser` tool server (Playwright MCP) gives you a real browser. It opens a
visible Chromium window on the user's machine so they can watch what you do.

## When to use `browser_*` tools vs the `web` (fetch) tool server

- Use **browser tools** when the page is interactive: forms, logins, search
  results that need scrolling, dynamically rendered content, or when the user
  asks you to *do* something on a site (fill a form, click a button, download).
- Use the **fetch tool** (web server, `fetch` tool) for simple read-only
  retrieval of a URL's text when speed matters and no interaction is needed.

## Typical workflow

1. `browser_navigate` to the URL. Wait for the page with `browser_wait`.
2. `browser_snapshot` to see the accessibility tree (links, buttons, inputs,
   landmarks). Never guess selectors from the URL — always snapshot first.
3. To act: `browser_click` / `browser_fill` / `browser_select_option` /
   `browser_press_key` using the element references from the snapshot. These
   take a `target` argument: paste the exact ref from your latest snapshot
   (e.g. `f1e127`) or a CSS selector — never an empty string, and never the
   element's visible text (a label like `Etap 3 - 2018/2019` is not a
   selector and crashes the tool). If you only know the label, snapshot first
   and use the ref next to it. Same after navigating or a "ref not found"
   error: take a fresh `browser_snapshot` first.
4. To read content: use `browser_snapshot` text; for visual verification or
   when the user wants to see something, take `browser_screenshot`.
5. To save a page as a document, use `browser_pdf` (prints the current page).
6. Finish browsing with `browser_close` when the task is done so memory is
   freed.

## Rules

- Do not navigate to sites the user is logged into with their personal account
  unless the user explicitly asked you to operate there.
- If a login is required, never guess credentials: ask the user.
- After filling forms, verify the result (snapshot or screenshot) before
  reporting success. A 200/201 response is not proof the form worked.
- If a page blocks automation (captcha, anti-bot), stop and tell the user;
  do not try to bypass it.
- HTTPS-first: if http:// fails or looks wrong, retry with https://.
- `browser_snapshot`'s optional `filename` writes the snapshot to disk instead
  of returning text. Use a bare relative name (e.g. `page.txt`) — it lands in
  `%USERPROFILE%\.open-webui-data`. Never pass `/mnt/uploads/...`: that's the
  Pyodide sandbox path, and the browser server rejects any path outside its
  two allowed roots with a 500. Most of the time omit `filename` and use the
  returned text directly.
- The snapshot already includes `/url:` values for every link — to save or
  read a linked file (PDF, CSV, image, ZIP, anything), call `download_url`
  from the `local-docs-mcp` server with that URL, then read the returned `path`
  with `read_pdf_text` / `extract_pdf` / `file_info`. No need to click
  through the page, and never point the `fetch` tool at a binary file (it
  returns raw bytes as text).
