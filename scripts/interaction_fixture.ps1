# Standard WPF controls: native UIA Invoke events are supplied by WPF itself.
# Launch with Windows PowerShell: powershell.exe -NoProfile -STA -File <this file>
# No input injection, custom UIA events, external messages or application actions.
$ErrorActionPreference = 'Stop'
if ([Threading.Thread]::CurrentThread.ApartmentState -ne 'STA') {
    throw 'Run this fixture with Windows PowerShell -STA.'
}
Add-Type -AssemblyName PresentationFramework
$fixtureRoot = Split-Path -Parent $PSScriptRoot
$fixturePath = Join-Path $fixtureRoot 'data\integration\interaction_fixture_wpf.json'
$fixtureEvents = [System.Collections.Generic.List[object]]::new()
$fixtureWindow = [System.Windows.Window]::new()
$fixtureWindow.Title = 'Onyx Interaction Fixture WPF'
$fixtureWindow.Width = 720
$fixtureWindow.Height = 420
$fixtureWindow.WindowStartupLocation = 'CenterScreen'
$fixturePanel = [System.Windows.Controls.StackPanel]::new()
$fixturePanel.Margin = [System.Windows.Thickness]::new(18)
$fixtureWindow.Content = $fixturePanel
$fixtureHeading = [System.Windows.Controls.TextBlock]::new()
$fixtureHeading.Text = 'Локальная тестовая заметка'
$fixtureHeading.FontSize = 18
$fixturePanel.Children.Add($fixtureHeading) | Out-Null
$fixtureEditor = [System.Windows.Controls.TextBox]::new()
$fixtureEditor.Name = 'fixture_note'
$fixtureEditor.AcceptsReturn = $true
$fixtureEditor.Height = 220
$fixtureEditor.FontSize = 18
$fixtureEditor.Margin = [System.Windows.Thickness]::new(0, 12, 0, 12)
[System.Windows.Automation.AutomationProperties]::SetName($fixtureEditor, 'Тестовая заметка')
$fixturePanel.Children.Add($fixtureEditor) | Out-Null
$fixtureButton = [System.Windows.Controls.Button]::new()
$fixtureButton.Name = 'fixture_confirm'
$fixtureButton.Content = 'Подтвердить тестовую заметку'
$fixtureButton.Height = 40
[System.Windows.Automation.AutomationProperties]::SetName($fixtureButton, 'Подтвердить тестовую заметку')
$fixturePanel.Children.Add($fixtureButton) | Out-Null
$fixtureStatus = [System.Windows.Controls.TextBlock]::new()
$fixtureStatus.Text = 'Ожидает подтверждения'
$fixtureStatus.Margin = [System.Windows.Thickness]::new(0, 12, 0, 0)
$fixturePanel.Children.Add($fixtureStatus) | Out-Null
$fixtureButton.Add_Click({
    $fixtureEvents.Add([ordered]@{
        timestamp = [DateTime]::UtcNow.ToString('o')
        text = $fixtureEditor.Text
        event = 'fixture_note_confirmed'
        source = 'own_fixture_wpf_button'
    })
    if ($fixtureEvents.Count -gt 20) { $fixtureEvents.RemoveAt(0) }
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($fixturePath)) | Out-Null
    $fixturePayload = [ordered]@{ events = @($fixtureEvents.ToArray()) } | ConvertTo-Json -Depth 4
    [IO.File]::WriteAllText($fixturePath + '.tmp', $fixturePayload, [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath ($fixturePath + '.tmp') -Destination $fixturePath -Force
    $fixtureStatus.Text = 'Заметка подтверждена. Подтверждений: ' + $fixtureEvents.Count
})
$fixtureTimer = [System.Windows.Threading.DispatcherTimer]::new()
$fixtureTimer.Interval = [TimeSpan]::FromMinutes(30)
$fixtureTimer.Add_Tick({ $fixtureWindow.Close() })
$fixtureTimer.Start()
try { $fixtureWindow.ShowDialog() | Out-Null }
finally { $fixtureTimer.Stop() }
