"""Silero VAD adapter using the optional sherpa-onnx Python package.

This file deliberately contains no import of sherpa-onnx at module import time.
The base prototype therefore remains usable without ML dependencies, while a
Pi can activate this analyzer by setting ``AUDIO_WORKBENCH_SILERO_VAD_MODEL``.
"""

from __future__ import annotations

import argparse
import json
import os
import wave
from pathlib import Path
from typing import Any

from .contracts import AnalyzerManifest, AnalyzerUnavailable


MANIFEST = AnalyzerManifest(
    id="vad_silero",
    name="Sprachaktivität / Silero",
    description="ML-basierte Sprachsegmente mit Silero VAD über sherpa-onnx",
    technology="sherpa-onnx + Silero VAD",
)


def _load_normalized_wav(path: Path) -> tuple[Any, int]:
    try:
        import numpy as np
    except ImportError as exc:
        raise AnalyzerUnavailable("Silero VAD benötigt NumPy.") from exc

    with wave.open(str(path), "rb") as wav:
        sample_rate = wav.getframerate()
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        frames = wav.readframes(wav.getnframes())
    if sample_width != 2:
        raise RuntimeError("Die Arbeitskopie ist nicht 16-bit PCM.")
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, sample_rate


def _append_segments(detector: Any, sample_rate: int, events: list[dict[str, Any]]) -> None:
    while not detector.empty():
        segment = detector.front
        start = float(segment.start) / sample_rate
        end = float(segment.start + len(segment.samples)) / sample_rate
        if end > start:
            events.append(
                {
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "label": "Sprache",
                    # The sherpa VAD binding exposes segment boundaries, not a
                    # per-segment probability. The UI renders this as "Modell".
                    "confidence": None,
                }
            )
        detector.pop()


def analyze(
    normalized_path: Path,
    model_path: str | Path | None = None,
    threshold: float = 0.5,
    min_speech_duration: float = 0.25,
    min_silence_duration: float = 0.5,
) -> dict[str, Any]:
    """Run Silero VAD on a normalized 16 kHz mono WAV file."""

    model = Path(model_path or os.environ.get("AUDIO_WORKBENCH_SILERO_VAD_MODEL", ""))
    if not model.is_file():
        raise AnalyzerUnavailable(
            "Silero-Modell fehlt. Setze AUDIO_WORKBENCH_SILERO_VAD_MODEL auf silero_vad.onnx."
        )
    try:
        import numpy as np
        import sherpa_onnx
    except ImportError as exc:
        raise AnalyzerUnavailable(
            "Silero VAD benötigt die optionalen Pakete 'sherpa-onnx' und 'numpy'."
        ) from exc

    samples, sample_rate = _load_normalized_wav(normalized_path)
    if sample_rate != MANIFEST.input_sample_rate:
        raise RuntimeError(f"Silero VAD erwartet 16000 Hz, erhalten: {sample_rate} Hz.")

    config = sherpa_onnx.VadModelConfig()
    config.silero_vad.model = str(model)
    config.silero_vad.threshold = float(threshold)
    config.silero_vad.min_speech_duration = float(min_speech_duration)
    config.silero_vad.min_silence_duration = float(min_silence_duration)
    config.silero_vad.max_speech_duration = 30.0
    config.silero_vad.window_size = 512
    config.sample_rate = sample_rate
    config.num_threads = 1
    config.provider = "cpu"
    detector = sherpa_onnx.VoiceActivityDetector(config, 60)

    events: list[dict[str, Any]] = []
    window_size = 512
    for start in range(0, len(samples), window_size):
        chunk = samples[start : start + window_size]
        if len(chunk) < window_size:
            chunk = np.pad(chunk, (0, window_size - len(chunk)))
        detector.accept_waveform(chunk.tolist())
        _append_segments(detector, sample_rate, events)
    detector.flush()
    _append_segments(detector, sample_rate, events)

    duration = len(samples) / sample_rate if sample_rate else 0.0
    active_seconds = sum(event["end"] - event["start"] for event in events)
    return {
        "type": "vad",
        "method": "sherpa-onnx/silero-vad",
        "model": model.name,
        "sample_rate": sample_rate,
        "active_seconds": round(active_seconds, 2),
        "activity_ratio": round(active_seconds / max(duration, 1e-9), 3),
        "events": events,
        "analysis_note": "Silero VAD; segment confidence is not exposed by the binding.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Audio Workbench Silero VAD adapter")
    parser.add_argument("wav", type=Path)
    parser.add_argument("--model", type=Path, default=None)
    args = parser.parse_args()
    try:
        print(json.dumps(analyze(args.wav, args.model), ensure_ascii=False, indent=2))
    except (AnalyzerUnavailable, RuntimeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
