@echo off
setlocal
cd /d "%~dp0"
set "DSQ_NODE=node"
if exist "runtime\node\node.exe" set "DSQ_NODE=%~dp0runtime\node\node.exe"
if exist "portable-node\node.exe" set "DSQ_NODE=%~dp0portable-node\node.exe"
"%DSQ_NODE%" "%~dp0harness\setup-key.cjs"
if errorlevel 1 echo Setup failed. Run harness\install.cmd first if dependencies are missing.
pause
