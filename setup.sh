#!/usr/bin/env bash
set -euo pipefail

echo "=== Audio Workbench Setup ==="
echo ""

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 1. Check prerequisites
echo "Checking prerequisites..."
command -v python3 >/dev/null 2>&1 || { echo "Error: python3 not found. Install Python 3.10+"; exit 1; }
command -v ffmpeg >/dev/null 2>&1 || { echo "Error: ffmpeg not found. Install FFmpeg"; exit 1; }
echo -e "${GREEN}✓${NC} Python3 and FFmpeg found"
echo ""

# 2. Create virtual environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
    echo -e "${GREEN}✓${NC} Virtual environment created"
else
    echo -e "${YELLOW}⊙${NC} Virtual environment already exists"
fi
echo ""

# 3. Install Python dependencies
echo "Installing Python dependencies..."
./venv/bin/pip install --upgrade pip wheel
./venv/bin/pip install -r requirements.txt
echo -e "${GREEN}✓${NC} Dependencies installed"
echo ""

# 4. Create model directories
echo "Creating model directories..."
mkdir -p models/vad
mkdir -p models/yamnet
mkdir -p models/asr
mkdir -p models/speakers
echo -e "${GREEN}✓${NC} Model directories created"
echo ""

# 5. Download models
echo "Downloading models..."
echo ""

# Silero VAD
if [ ! -f "models/vad/silero_vad.int8.onnx" ]; then
    echo "Downloading Silero VAD model..."
    curl -L "https://github.com/snakers4/silero-vad/raw/master/files/silero_vad.onnx" \
        -o "models/vad/silero_vad.int8.onnx"
    echo -e "${GREEN}✓${NC} Silero VAD downloaded"
else
    echo -e "${YELLOW}⊙${NC} Silero VAD already exists"
fi
echo ""

# YAMNet
if [ ! -f "models/yamnet/yamnet.tflite" ]; then
    echo "Downloading YAMNet model..."
    curl -L "https://storage.googleapis.com/tfhub-lite-models/google/yamnet/tflite/1/default/1.tflite" \
        -o "models/yamnet/yamnet.tflite"
    echo -e "${GREEN}✓${NC} YAMNet model downloaded"
else
    echo -e "${YELLOW}⊙${NC} YAMNet model already exists"
fi

if [ ! -f "models/yamnet/yamnet_class_map.csv" ]; then
    echo "Downloading YAMNet class map..."
    curl -L "https://raw.githubusercontent.com/tensorflow/models/master/research/audioset/yamnet/yamnet_class_map.csv" \
        -o "models/yamnet/yamnet_class_map.csv"
    echo -e "${GREEN}✓${NC} YAMNet class map downloaded"
else
    echo -e "${YELLOW}⊙${NC} YAMNet class map already exists"
fi
echo ""

# Whisper.cpp model
if [ ! -f "models/asr/ggml-tiny.bin" ]; then
    echo "Downloading Whisper tiny model..."
    curl -L "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin" \
        -o "models/asr/ggml-tiny.bin"
    echo -e "${GREEN}✓${NC} Whisper tiny model downloaded"
else
    echo -e "${YELLOW}⊙${NC} Whisper model already exists"
fi
echo ""

# Whisper.cpp binary
if [ ! -f "whisper-cli" ]; then
    echo "Downloading whisper.cpp binary..."
    ARCH=$(uname -m)
    if [ "$ARCH" = "aarch64" ]; then
        echo "Building whisper.cpp from source for ARM64..."
        if [ ! -d "whisper.cpp" ]; then
            git clone https://github.com/ggerganov/whisper.cpp.git
        fi
        cd whisper.cpp
        make -j$(nproc)
        cp main ../whisper-cli
        cd ..
        echo -e "${GREEN}✓${NC} Whisper.cpp built from source"
    elif [ "$ARCH" = "x86_64" ]; then
        echo "For x86_64, please download pre-built binary from:"
        echo "https://github.com/ggerganov/whisper.cpp/releases"
        echo "and place it as 'whisper-cli' in this directory."
        echo -e "${YELLOW}⊙${NC} Manual download required"
    else
        echo "Unknown architecture: $ARCH"
        echo -e "${YELLOW}⊙${NC} Manual installation required"
    fi
else
    echo -e "${YELLOW}⊙${NC} whisper-cli already exists"
fi
echo ""

# Pyannote diarization models
echo "Downloading Pyannote diarization models..."
if [ ! -f "models/speakers/model.int8.onnx" ]; then
    echo "Downloading segmentation model..."
    curl -L "https://huggingface.co/pyannote/segmentation-3.0/resolve/main/pytorch_model.bin" \
        -o "models/speakers/model.int8.onnx" 2>/dev/null || \
    echo -e "${YELLOW}⚠${NC} Pyannote segmentation model requires manual download from HuggingFace"
fi

if [ ! -f "models/speakers/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx" ]; then
    echo "Downloading speaker embedding model..."
    echo -e "${YELLOW}⚠${NC} Speaker embedding model requires manual download"
    echo "Download from: https://www.modelscope.cn/models/damo/speech_eres2net_base_sv_zh-cn_3dspeaker_16k"
fi
echo ""

# 6. Create data directory
echo "Creating data directory..."
mkdir -p data/recordings
echo -e "${GREEN}✓${NC} Data directory created"
echo ""

# 7. Set permissions
chmod +x start.sh
[ -f "whisper-cli" ] && chmod +x whisper-cli

echo "==================================="
echo -e "${GREEN}Setup complete!${NC}"
echo ""
echo "To start the server:"
echo "  ./start.sh --host 0.0.0.0"
echo ""
echo "Then open: http://localhost:8787"
echo ""
echo -e "${YELLOW}Note:${NC} Some models (Pyannote diarization) may require manual download"
echo "      from HuggingFace due to licensing requirements."
echo "==================================="
