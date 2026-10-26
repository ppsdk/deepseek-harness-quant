@echo off
cd /d "%~dp0"
node -e "if (Number(process.versions.node.split('.')[0]) < 24) { console.error('DSHQuant requires Node.js 24 or newer.'); process.exit(1); }"
if errorlevel 1 (
  echo Install Node.js 24 LTS, then run this script again.
  pause
  exit /b 1
)
echo Installing the DSH version pinned in package.json and package-lock.json...
if exist package-lock.json (
  call npm ci --no-audit --no-fund
) else (
  call npm install --no-audit --no-fund
)
if errorlevel 1 (
  echo Installation failed. Check Node.js, npm, and network access.
  pause
  exit /b 1
)
node node_modules\@deepseek-ai\dsh\lib\bin.js --version
echo Ready. Configure the API key with the repository setup script.
echo Then run: python launcher.py
pause
