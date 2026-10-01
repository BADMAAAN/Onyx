$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
Push-Location -LiteralPath $PSScriptRoot
try {
    & (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') -m onyx_desktop --backend-root $PSScriptRoot @args
    exit $LASTEXITCODE
} finally { Pop-Location }
