#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"

export AUDIO_WORKBENCH_SILERO_VAD_MODEL="$DIR/models/vad/silero_vad.int8.onnx"
export AUDIO_WORKBENCH_YAMNET_MODEL="$DIR/models/yamnet/yamnet.tflite"
export AUDIO_WORKBENCH_YAMNET_CLASS_MAP="$DIR/models/yamnet/yamnet_class_map.csv"
export AUDIO_WORKBENCH_WHISPER_MODEL="$DIR/models/asr/ggml-tiny.bin"
export AUDIO_WORKBENCH_WHISPER_CLI="$DIR/whisper-cli"
export AUDIO_WORKBENCH_DIARIZATION_SEGMENTATION_MODEL="$DIR/models/speakers/model.int8.onnx"
export AUDIO_WORKBENCH_DIARIZATION_EMBEDDING_MODEL="$DIR/models/speakers/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
export AUDIO_WORKBENCH_BIRDNET=1

exec "$DIR/venv/bin/python3" "$DIR/server.py" "$@"
