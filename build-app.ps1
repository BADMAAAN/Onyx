param([switch]$InstallDependencies)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
# Keep compiler extraction outside MSIX's long redirected LocalAppData path.
$env:NUITKA_CACHE_DIR = Join-Path $PSScriptRoot 'build\nuitka-cache'
$buildPython = Join-Path $PSScriptRoot '.venv-app-build\Scripts\python.exe'
Push-Location -LiteralPath $PSScriptRoot
try {
    if (!(Test-Path -LiteralPath $buildPython)) {
        & (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') -m venv (Join-Path $PSScriptRoot '.venv-app-build')
        if ($LASTEXITCODE -ne 0) { throw 'Could not create isolated build environment' }
        $InstallDependencies = $true
    }
    if ($InstallDependencies) {
        & $buildPython -m pip install -r (Join-Path $PSScriptRoot 'requirements-app-build.txt')
        if ($LASTEXITCODE -ne 0) { throw 'Build dependencies could not be installed' }
    }
    & $buildPython (Join-Path $PSScriptRoot 'scripts\prepare_desktop_build.py')
    if ($LASTEXITCODE -ne 0) { throw 'Build resources failed' }
    $revision = (& git rev-parse HEAD).Trim()
    $buildId = '0.5.0-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $revision.Substring(0,8)
    $output = Join-Path $PSScriptRoot ('dist\' + $buildId)
    & $buildPython -c "import psutil; assert psutil.virtual_memory().available >= 8*1024**3, 'Build requires 8 GiB available RAM'"
    if ($LASTEXITCODE -ne 0) { throw 'Insufficient memory reserve' }
    & $buildPython -m nuitka --mode=standalone --enable-plugin=pyside6 `
        --include-qt-plugins=platforms,imageformats,iconengines,qml `
        --noinclude-qt-plugins=webengine --nofollow-import-to=kk,torch,numpy,scipy,sounddevice,faster_whisper `
        --include-data-dir=onyx_desktop/qml=onyx_desktop/qml `
        --include-data-dir=onyx_desktop/assets=onyx_desktop/assets `
        --include-data-dir=build/desktop-notices=notices `
        --include-data-files=onyx_desktop/NOTICES.md=onyx_desktop/NOTICES.md `
        --include-data-files=onyx_desktop/backend.json=onyx_desktop/backend.json `
        --windows-console-mode=disable --windows-icon-from-ico=build/onyx.ico `
        --product-name=Onyx --file-description='Onyx Desktop' --file-version=0.5.0.0 `
        "--output-dir=$output" --output-filename=Onyx.exe --report=build/desktop-compilation.xml `
        --assume-yes-for-downloads --jobs=2 onyx_desktop/Onyx.py
    if ($LASTEXITCODE -ne 0) { throw 'Nuitka build failed; see build/desktop-compilation.xml' }
    $bundle = Join-Path $output 'Onyx.dist'
    & $buildPython scripts\verify_desktop_bundle.py $bundle --build-id $buildId --revision $revision
    if ($LASTEXITCODE -ne 0) { throw 'Standalone validation failed; do not install' }
    Write-Host $bundle
} finally { Pop-Location }
