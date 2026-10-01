$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:HF_HUB_DISABLE_TELEMETRY = '1'
$env:DO_NOT_TRACK = '1'

$python  = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$pythonw = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'

if ($args -contains '--background') {
    $onyxArgs = @('-m', 'app.main') + $args

    Start-Process `
        -FilePath $pythonw `
        -ArgumentList $onyxArgs `
        -WorkingDirectory $PSScriptRoot `
        -WindowStyle Hidden

    Write-Host 'Запрошен запуск Onyx в background/tray. Статус — в tray и logs.'
    exit 0
}

& $python -m app.main @args
exit $LASTEXITCODE
