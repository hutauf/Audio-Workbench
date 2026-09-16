$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    $Python = Join-Path $Root "venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Kein virtuelles Environment gefunden. Bitte setup.md mit einem Agenten ausführen."
}

function Set-ModelEnvironment {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$RelativePath
    )

    $Path = Join-Path $Root $RelativePath
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        Set-Item -Path ("Env:{0}" -f $Name) -Value $Path
    } else {
        Remove-Item -Path ("Env:{0}" -f $Name) -ErrorAction SilentlyContinue
    }
}

Set-ModelEnvironment "AUDIO_WORKBENCH_SILERO_VAD_MODEL" "models\vad\silero_vad.onnx"
Set-ModelEnvironment "AUDIO_WORKBENCH_YAMNET_MODEL" "models\yamnet\yamnet.tflite"
Set-ModelEnvironment "AUDIO_WORKBENCH_YAMNET_CLASS_MAP" "models\yamnet\yamnet_class_map.csv"
Set-ModelEnvironment "AUDIO_WORKBENCH_WHISPER_MODEL" "models\asr\ggml-tiny.bin"

$WhisperCandidates = @(
    (Join-Path $Root "tools\whisper\whisper-cli.exe"),
    (Join-Path $Root "whisper-cli.exe"),
    (Join-Path $Root "tools\whisper\whisper-cli"),
    (Join-Path $Root "whisper-cli")
)
$Whisper = $WhisperCandidates | Where-Object {
    Test-Path -LiteralPath $_ -PathType Leaf
} | Select-Object -First 1
if ($Whisper) {
    Set-Item -Path "Env:AUDIO_WORKBENCH_WHISPER_CLI" -Value $Whisper
} else {
    Remove-Item -Path "Env:AUDIO_WORKBENCH_WHISPER_CLI" -ErrorAction SilentlyContinue
}

$Segmentation = Join-Path $Root "models\speakers\segmentation\model.onnx"
$Embedding = Join-Path $Root "models\speakers\3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
if ((Test-Path -LiteralPath $Segmentation -PathType Leaf) -and
    (Test-Path -LiteralPath $Embedding -PathType Leaf)) {
    Set-Item -Path "Env:AUDIO_WORKBENCH_DIARIZATION_SEGMENTATION_MODEL" -Value $Segmentation
    Set-Item -Path "Env:AUDIO_WORKBENCH_DIARIZATION_EMBEDDING_MODEL" -Value $Embedding
} else {
    Remove-Item -Path "Env:AUDIO_WORKBENCH_DIARIZATION_SEGMENTATION_MODEL" -ErrorAction SilentlyContinue
    Remove-Item -Path "Env:AUDIO_WORKBENCH_DIARIZATION_EMBEDDING_MODEL" -ErrorAction SilentlyContinue
}

& $Python -c "import birdnetlib" *> $null
if ($LASTEXITCODE -eq 0) {
    Set-Item -Path "Env:AUDIO_WORKBENCH_BIRDNET" -Value "1"
} else {
    Remove-Item -Path "Env:AUDIO_WORKBENCH_BIRDNET" -ErrorAction SilentlyContinue
}

& $Python (Join-Path $Root "server.py") @args
exit $LASTEXITCODE
