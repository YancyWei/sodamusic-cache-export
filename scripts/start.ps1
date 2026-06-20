$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

if (Get-Command py -ErrorAction SilentlyContinue) {
  & py -3 src/start_sodamusic_export.py @args
  exit $LASTEXITCODE
}

if (Get-Command python -ErrorAction SilentlyContinue) {
  & python src/start_sodamusic_export.py @args
  exit $LASTEXITCODE
}

Write-Error "未找到 Python 3，请先安装 Python。"
exit 1
