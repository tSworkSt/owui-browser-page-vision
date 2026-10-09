@echo off
rem Starts the five mcpo proxy servers for Open WebUI (filesystem / fetch / memory / local-docs / browser)
rem Skips any server that is already listening.
setlocal
set "DATA=%USERPROFILE%\.open-webui-data"

curl -s -o nul --max-time 2 http://localhost:8088/openapi.json
if errorlevel 1 (
  echo Starting MCP filesystem server on :8088
  set "FS_ROOTS=%USERPROFILE%\Documents %USERPROFILE%\Desktop %USERPROFILE%\Downloads"
  start "" /b cmd /c "cd /d %DATA% && uvx --with mcp==1.30.0 mcpo --port 8088 -- npx -y @modelcontextprotocol/server-filesystem %FS_ROOTS%"
)

curl -s -o nul --max-time 2 http://localhost:8089/openapi.json
if errorlevel 1 (
  echo Starting MCP web-fetch server on :8089
  start "" /b cmd /c "cd /d %DATA% && uvx --with mcp==1.30.0 mcpo --port 8089 -- uvx mcp-server-fetch"
)

curl -s -o nul --max-time 2 http://localhost:8090/openapi.json
if errorlevel 1 (
  echo Starting MCP memory server on :8090
  start "" /b cmd /c "cd /d %DATA% && uvx --with mcp==1.30.0 mcpo --port 8090 -- npx -y @modelcontextprotocol/server-memory"
)

curl -s -o nul --max-time 2 http://localhost:8091/openapi.json
if errorlevel 1 (
  echo Starting MCP local-docs-mcp server on :8091
  set "VENV_PY=%~dp0mcp-helper-venv\Scripts\python.exe"
  start "" /b cmd /c "cd /d %DATA% && uvx --with mcp==1.30.0 mcpo --port 8091 -- %VENV_PY% %~dp0mcp-helpers\local_docs_mcp.py"
)

curl -s -o nul --max-time 2 http://localhost:8092/openapi.json
if errorlevel 1 (
  echo Starting MCP browser server on :8092
  rem --browser msedge: no Google Chrome on this machine; Edge is Chromium-based and always present
  start "" /b cmd /c "cd /d %DATA% && uvx --with mcp==1.30.0 mcpo --port 8092 -- npx -y @playwright/mcp@latest --browser msedge"
)
endlocal