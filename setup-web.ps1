$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$webPython = Join-Path $PSScriptRoot '.venv-web\Scripts\python.exe'
if (!(Test-Path -LiteralPath $webPython)) {
    & (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') -m venv (Join-Path $PSScriptRoot '.venv-web')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create isolated web environment' }
}
& $webPython -m pip install -r (Join-Path $PSScriptRoot 'requirements-web-lock.txt')
if ($LASTEXITCODE -ne 0) { throw 'Web dependency install failed' }
& $webPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Web dependency check failed' }
