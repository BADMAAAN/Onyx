param([ValidateSet('desktop','explorer')][string]$Mode)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$shell = New-Object -ComObject Shell.Application
$items = [System.Collections.Generic.List[object]]::new()
$limitations = [System.Collections.Generic.List[string]]::new()
$complete = 'complete'
if ($Mode -eq 'desktop') {
    $wsh = New-Object -ComObject WScript.Shell
    $desktop = $shell.Namespace(0)
    foreach ($item in $desktop.Items()) {
        if ($items.Count -ge 500) { $limitations.Add('500 direct Desktop items limit'); break }
        try {
            $path = [string]$item.Path
            $kind = if ($item.IsFileSystem) { if ($item.IsFolder) { 'folder' } else { 'file' } } else { 'virtual_shell_item' }
            $target = $path
            $shortcut = $path.EndsWith('.lnk',[StringComparison]::OrdinalIgnoreCase)
            if ($shortcut) {
                $target = [string]$wsh.CreateShortcut($path).TargetPath
                if ($target -and (Test-Path -LiteralPath $target -PathType Container)) { $kind = 'folder' }
                elseif ($target -and (Test-Path -LiteralPath $target -PathType Leaf)) { $kind = 'file' }
                else { $kind = 'virtual_shell_item' }
            }
            $items.Add(@{name=[string]$item.Name; type=$kind; path=$path; target_path=$target; is_shortcut=$shortcut; source='Shell Desktop'; verified_at=[DateTime]::UtcNow.ToString('o')})
        } catch { $limitations.Add($_.Exception.Message) }
    }
} else {
    # HWND + Document.Folder.Self is evidence of a specific location. Window
    # titles are never used as directories or proof of hidden tab coverage.
    $complete = 'partial'
    $limitations.Add('Shell.Windows exposes observed Explorer locations; hidden tabs are not guaranteed')
    foreach ($window in $shell.Windows()) {
        try {
            if ([IO.Path]::GetFileName([string]$window.FullName) -ine 'explorer.exe') { continue }
            $folder = $window.Document.Folder.Self
            if ($null -eq $folder) { continue }
            $items.Add(@{hwnd=[long]$window.HWND; name=[string]$folder.Name; path=[string]$folder.Path; url=[string]$window.LocationURL; source='Shell.Windows.Document.Folder'; verified_at=[DateTime]::UtcNow.ToString('o')})
        } catch { $limitations.Add($_.Exception.Message) }
    }
}
if ($limitations.Count -and $complete -eq 'complete') { $complete = 'partial' }
ConvertTo-Json -InputObject @{items=@($items.ToArray()); completeness=$complete; limitations=@($limitations.ToArray())} -Depth 5 -Compress
