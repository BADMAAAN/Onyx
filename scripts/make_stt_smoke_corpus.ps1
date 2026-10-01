# Synthetic RU/EN smoke fixtures. Never opens the microphone.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$root = Split-Path $PSScriptRoot -Parent
$output = Join-Path $root 'data\onyx_benchmark'
New-Item -ItemType Directory -Force -Path $output | Out-Null
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$cases = @(
    @{ id='mixed_1'; group='mixed'; parts=@(@('ru','Оникс, открой '),@('en','VS Code'),@('ru',' и запусти '),@('en','build.')) },
    @{ id='mixed_2'; group='mixed'; parts=@(@('en','Open Firefox and then '),@('ru','открой '),@('en','YouTube.')) },
    @{ id='mixed_3'; group='mixed'; parts=@(@('ru','Открой '),@('en','PowerShell and run git status.')) },
    @{ id='en_1'; group='en'; parts=,@('en','Onyx, open Firefox and YouTube.') },
    @{ id='ru_1'; group='ru'; parts=,@('ru','Оникс, поставь громкость сто.') },
    @{ id='ru_2'; group='ru'; parts=,@('ru','Оникс, открой телеграм.') }
)
$rows = @()
try {
    foreach ($case in $cases) {
        $ssml = '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="ru-RU">'
        $expected = ''
        foreach ($part in $case.parts) {
            $voice = if ($part[0] -eq 'ru') { 'Microsoft Irina Desktop' } else { 'Microsoft Zira Desktop' }
            $ssml += '<voice name="'+$voice+'">'+[Security.SecurityElement]::Escape($part[1])+'</voice>'
            $expected += $part[1]
        }
        $ssml += '</speak>'
        $synth.SetOutputToWaveFile((Join-Path $output ($case.id+'.wav')))
        $synth.SpeakSsml($ssml)
        $synth.SetOutputToNull()
        $rows += @{ id=$case.id; group=$case.group; source='synthetic'; audio=($case.id+'.wav'); expected=$expected; condition='SAPI Irina/Zira voice switches, not natural human code-switching' }
    }
    $json = ConvertTo-Json -InputObject @($rows) -Depth 5
    [IO.File]::WriteAllText((Join-Path $output 'mixed_manifest.json'),$json,[Text.UTF8Encoding]::new($false))
} finally { $synth.Dispose() }
