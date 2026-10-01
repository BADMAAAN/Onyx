param([Parameter(Mandatory=$true)][string]$Bundle,
      [string]$InstallRoot = (Join-Path $env:USERPROFILE 'Onyx\Client'))
$ErrorActionPreference = 'Stop'
$source = (Resolve-Path -LiteralPath $Bundle).Path
$manifest = Get-Content -LiteralPath (Join-Path $source 'manifest.json') -Raw | ConvertFrom-Json
if ($manifest.build_id -notmatch '^[a-zA-Z0-9._-]+$') { throw 'Invalid build ID' }
function Test-Bundle([string]$folder) {
    foreach ($entry in $manifest.files.PSObject.Properties) {
        if ($entry.Name -match '(^|/)\.\.(/|$)|:|^[/\\]') { throw 'Invalid manifest path' }
        $file = Join-Path $folder $entry.Name
        if (!(Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing bundle file: $($entry.Name)" }
        if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $entry.Value) { throw "Hash mismatch: $($entry.Name)" }
    }
    foreach ($required in @('Onyx.exe','python312.dll','qt6quick.dll','PySide6/qt-plugins/platforms/qwindows.dll')) {
        if (!$manifest.files.PSObject.Properties[$required]) { throw "Missing runtime in manifest: $required" }
    }
}
Test-Bundle $source
$destination = Join-Path $InstallRoot $manifest.build_id
if (!(Test-Path -LiteralPath $destination)) {
    New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $destination -Recurse
}
# An existing version is checked, never overwritten (including running files).
Test-Bundle $destination
# Verify the copied executable before switching the shortcut. This process has
# no backend/data access, no Python environment, and an unrelated working dir.
$smokeDir = Join-Path $InstallRoot ('verification-' + $manifest.build_id)
New-Item -ItemType Directory -Path $smokeDir -Force | Out-Null
$shot = Join-Path $smokeDir 'window.png'
$info = New-Object System.Diagnostics.ProcessStartInfo
$info.FileName = Join-Path $destination 'Onyx.exe'
$info.Arguments = '--visual-test --smoke-seconds 4 --screenshot "' + $shot + '"'
$info.WorkingDirectory = [Environment]::GetFolderPath('CommonDocuments')
$info.UseShellExecute = $false
$info.CreateNoWindow = $true
$info.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
$info.EnvironmentVariables.Remove('PYTHONHOME')
$info.EnvironmentVariables.Remove('PYTHONPATH')
$info.EnvironmentVariables['PATH'] = "$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\Wbem"
$smoke = [System.Diagnostics.Process]::Start($info)
if (!$smoke.WaitForExit(20000)) { $smoke.Kill(); throw 'Copied client smoke test timed out; shortcut not changed' }
if ($smoke.ExitCode -ne 0 -or !(Test-Path -LiteralPath $shot)) { throw 'Copied client failed its loader/QML smoke test; shortcut not changed' }
$shell = New-Object -ComObject WScript.Shell
$linkPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Onyx Desktop.lnk'
$link = $shell.CreateShortcut($linkPath)
$link.TargetPath = Join-Path $destination 'Onyx.exe'
$link.Arguments = ''
$link.WorkingDirectory = $destination
$link.IconLocation = $link.TargetPath + ',0'
$link.Description = 'Onyx — local desktop client'
$link.Save()
$record = @{ executable=$link.TargetPath; build_id=$manifest.build_id; shortcut=$linkPath }
$pointer = Join-Path $InstallRoot 'current.json'
$record | ConvertTo-Json | Set-Content -LiteralPath ($pointer+'.tmp') -Encoding UTF8
Move-Item -LiteralPath ($pointer+'.tmp') -Destination $pointer -Force
$record | ConvertTo-Json
