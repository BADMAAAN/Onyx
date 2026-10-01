$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$repo = Split-Path $PSScriptRoot -Parent
$output = Join-Path $repo 'data\voice_diagnostics\real_speech'
$manifest = Get-Content -LiteralPath (Join-Path $output 'replay_manifest_v2.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $synth.SelectVoice('Microsoft Irina Desktop')
    foreach ($case in $manifest) {
        # All Unicode comes from explicitly decoded JSON. Keep this script ASCII
        # so Windows PowerShell 5 cannot corrupt a BOM-less Cyrillic wake prefix.
        $destination = Join-Path $output $case.wav_name
        if (Test-Path -LiteralPath $destination) { throw "Fixture already exists: $destination" }
        $synth.SetOutputToWaveFile($destination)
        $synth.Speak([string]$case.source_text)
        $synth.SetOutputToNull()
    }
} finally { $synth.Dispose() }
