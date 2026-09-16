#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -x "$DIR/.venv/bin/python" ]; then
    PYTHON="$DIR/.venv/bin/python"
elif [ -x "$DIR/venv/bin/python3" ]; then
    # Backward compatibility with environments created by older revisions.
    PYTHON="$DIR/venv/bin/python3"
else
    echo "Kein virtuelles Environment gefunden. Bitte setup.md mit einem Agenten ausführen." >&2
    exit 1
fi

set_model_env() {
    local variable="$1"
    local relative_path="$2"
    local absolute_path="$DIR/$relative_path"
    if [ -f "$absolute_path" ]; then
        export "$variable=$absolute_path"
    else
        unset "$variable" || true
    fi
}

set_model_env AUDIO_WORKBENCH_SILERO_VAD_MODEL "models/vad/silero_vad.onnx"
set_model_env AUDIO_WORKBENCH_YAMNET_MODEL "models/yamnet/yamnet.tflite"
set_model_env AUDIO_WORKBENCH_YAMNET_CLASS_MAP "models/yamnet/yamnet_class_map.csv"
set_model_env AUDIO_WORKBENCH_WHISPER_MODEL "models/asr/ggml-tiny.bin"

unset AUDIO_WORKBENCH_WHISPER_CLI || true
for candidate in \
    "$DIR/tools/whisper/whisper-cli" \
    "$DIR/whisper-cli" \
    "$DIR/tools/whisper/whisper-cli.exe" \
    "$DIR/whisper-cli.exe"; do
    if [ -f "$candidate" ]; then
        export AUDIO_WORKBENCH_WHISPER_CLI="$candidate"
        break
    fi
done

if [ -f "$DIR/models/speakers/segmentation/model.onnx" ] \
    && [ -f "$DIR/models/speakers/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx" ]; then
    export AUDIO_WORKBENCH_DIARIZATION_SEGMENTATION_MODEL="$DIR/models/speakers/segmentation/model.onnx"
    export AUDIO_WORKBENCH_DIARIZATION_EMBEDDING_MODEL="$DIR/models/speakers/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
else
    unset AUDIO_WORKBENCH_DIARIZATION_SEGMENTATION_MODEL || true
    unset AUDIO_WORKBENCH_DIARIZATION_EMBEDDING_MODEL || true
fi

if "$PYTHON" -c "import birdnetlib" >/dev/null 2>&1; then
    export AUDIO_WORKBENCH_BIRDNET=1
else
    unset AUDIO_WORKBENCH_BIRDNET || true
fi

exec "$PYTHON" "$DIR/server.py" "$@"
