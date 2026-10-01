$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$items = [System.Collections.Generic.List[object]]::new()
Get-StartApps | ForEach-Object { $items.Add(@{name=$_.Name; kind='aumid'; target=$_.AppID; executable=''}) }
$shell = New-Object -ComObject Shell.Application
$shell.Namespace('shell:AppsFolder').Items() | ForEach-Object {
    $items.Add(@{name=$_.Name; kind='aumid'; target=$_.Path; executable=''})
}
Get-AppxPackage -Name '*OpenAI.Codex*' | ForEach-Object {
    $package = $_
    $manifest = Get-AppxPackageManifest -Package $package.PackageFullName
    $manifest.Package.Applications.Application | Where-Object Id -EQ 'App' | ForEach-Object {
        $items.Add(@{name='OpenAI Codex'; kind='aumid'; target=($package.PackageFamilyName+'!'+$_.Id); executable=(Join-Path $package.InstallLocation $_.Executable)})
    }
}
$wsh = New-Object -ComObject WScript.Shell
$roots = @([Environment]::GetFolderPath('StartMenu'), [Environment]::GetFolderPath('CommonStartMenu'), [Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('CommonDesktopDirectory'))
foreach ($root in $roots) {
    # Start Menu hierarchy is bounded; Desktop is shallow. Never follow
    # reparse-point directories or classify a folder shortcut as an app.
    $maxDepth = if ($root -eq [Environment]::GetFolderPath('Desktop') -or $root -eq [Environment]::GetFolderPath('CommonDesktopDirectory')) { 0 } else { 4 }
    $pending = [System.Collections.Generic.Queue[object]]::new()
    $visited = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $pending.Enqueue(@{Path=$root; Depth=0})
    $linksSeen = 0
    while ($pending.Count -gt 0 -and $linksSeen -lt 2000 -and $visited.Count -lt 2000) {
        $directory = $pending.Dequeue()
        if (-not $visited.Add([string]$directory.Path)) { continue }
        foreach ($child in (Get-ChildItem -LiteralPath $directory.Path -Force -ErrorAction SilentlyContinue)) {
            if ($child.PSIsContainer) {
                # Test attributes BEFORE enqueue: no dependence on a shell
                # version's recursive junction/symlink traversal semantics.
                if ($directory.Depth -lt $maxDepth -and -not ($child.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                    $pending.Enqueue(@{Path=$child.FullName; Depth=($directory.Depth + 1)})
                }
                continue
            }
            if ($child.Extension -ine '.lnk') { continue }
            $linksSeen++
            $link = $wsh.CreateShortcut($child.FullName)
            if ($link.TargetPath -and [IO.Path]::GetExtension($link.TargetPath) -ieq '.exe' -and (Test-Path -LiteralPath $link.TargetPath -PathType Leaf)) {
                $items.Add(@{name=$child.BaseName; kind='shortcut'; target=$child.FullName; executable=$link.TargetPath})
            }
            if ($linksSeen -ge 2000) { break }
        }
    }
}
foreach ($root in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\App Paths','HKLM:\Software\Microsoft\Windows\CurrentVersion\App Paths','HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths')) {
    Get-ChildItem $root | ForEach-Object {
        $target = $_.GetValue('')
        if ($target) { $items.Add(@{name=$_.PSChildName; kind='executable'; target=$target.Trim('"'); executable=$target.Trim('"')}) }
    }
}
$prog = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice').ProgId
if ($prog) {
    $key = [Microsoft.Win32.Registry]::ClassesRoot.OpenSubKey("$prog\shell\open\command")
    $command = if ($key) { $key.GetValue('') }
    if ($command -match '^"([^"]+\.exe)"') { $items.Add(@{name='browser'; kind='executable'; target=$matches[1]; executable=$matches[1]}) }
}
ConvertTo-Json -InputObject @($items.ToArray()) -Compress -Depth 3
