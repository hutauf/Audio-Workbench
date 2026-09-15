"""YAMNet adapter with lazy TensorFlow imports.

The adapter expects a local TensorFlow Hub SavedModel directory and a local
class map, or a SavedModel that exposes YAMNet's ``class_map_path``. It never
downloads a model at runtime, which keeps the workbench local-first.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import wave
from pathlib import Path
from typing import Any

from .contracts import AnalyzerManifest, AnalyzerUnavailable


MANIFEST = AnalyzerManifest(
    id="yamnet",
    name="Sound Events / YAMNet",
    description="Allgemeine Geräuschklassifikation auf Zeitfenstern",
    technology="YAMNet / AudioSet",
)


def _load_normalized_wav(path: Path) -> tuple[Any, int]:
    try:
        import numpy as np
    except ImportError as exc:
        raise AnalyzerUnavailable("YAMNet benötigt NumPy.") from exc

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


def _as_path(value: Any) -> Path | None:
    if hasattr(value, "numpy"):
        value = value.numpy()
    if isinstance(value, bytes):
        value = value.decode()
    if value is None:
        return None
    candidate = Path(str(value))
    return candidate if candidate.is_file() else None


def _load_class_names(model: Any | None, class_map_path: str | Path | None) -> list[str]:
    candidate = Path(class_map_path) if class_map_path else None
    if candidate is None:
        if model is not None:
            method = getattr(model, "class_map_path", None)
            if callable(method):
                candidate = _as_path(method())
    if candidate is None or not candidate.is_file():
        raise AnalyzerUnavailable(
            "YAMNet class map fehlt. Setze AUDIO_WORKBENCH_YAMNET_CLASS_MAP auf class_map.csv."
        )
    names: list[str] = []
    if candidate.suffix.lower() == ".csv":
        with candidate.open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            for row in reader:
                names.append(row.get("display_name") or row.get("label") or row.get("name") or "unknown")
    else:
        names = [line.strip() for line in candidate.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not names:
        raise RuntimeError(f"YAMNet class map ist leer: {candidate}")
    return names


def _tensor_to_numpy(value: Any) -> Any:
    return value.numpy() if hasattr(value, "numpy") else value


def _run_tflite(model_path: Path, samples: Any) -> Any:
    """Run the fixed-input YAMNet classification TFLite model."""

    try:
        from ai_edge_litert.interpreter import Interpreter
    except ImportError:
        try:
            from tflite_runtime.interpreter import Interpreter
        except ImportError:
            try:
                import tensorflow as tf

                Interpreter = tf.lite.Interpreter
            except ImportError as exc:
                raise AnalyzerUnavailable(
                    "YAMNet-TFLite benötigt 'ai-edge-litert', 'tflite-runtime' oder TensorFlow."
                ) from exc
    try:
        import numpy as np
    except ImportError as exc:
        raise AnalyzerUnavailable("YAMNet benötigt NumPy.") from exc

    interpreter = Interpreter(model_path=str(model_path), num_threads=1)
    interpreter.allocate_tensors()
    input_info = interpreter.get_input_details()[0]
    output_info = interpreter.get_output_details()[0]
    input_length = 15_600
    input_shape = tuple(int(value) for value in input_info["shape"])
    if input_shape[-1] != input_length:
        input_length = input_shape[-1]
    hop = input_length // 2
    frame_scores = []
    for start in range(0, max(1, len(samples)), hop):
        frame = samples[start : start + input_length]
        if len(frame) == 0:
            break
        if len(frame) < input_length:
            frame = np.pad(frame, (0, input_length - len(frame)))
        frame = np.asarray(frame, dtype=np.float32)
        if input_info["dtype"] != np.float32:
            scale, zero_point = input_info.get("quantization", (0.0, 0))
            if scale:
                frame = np.round(frame / scale + zero_point).astype(input_info["dtype"])
        frame = frame.reshape(input_shape)
        interpreter.set_tensor(input_info["index"], frame)
        interpreter.invoke()
        scores = interpreter.get_tensor(output_info["index"])
        scores = np.asarray(scores).reshape(-1)
        if output_info["dtype"] != np.float32:
            scale, zero_point = output_info.get("quantization", (0.0, 0))
            if scale:
                scores = (scores.astype(np.float32) - zero_point) * scale
        frame_scores.append(scores)
    if not frame_scores:
        return np.empty((0, 521), dtype=np.float32)
    return np.asarray(frame_scores, dtype=np.float32)


def _merge_events(events: list[dict[str, Any]], gap: float = 0.5) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for event in events:
        if merged and merged[-1]["label"] == event["label"] and event["start"] <= merged[-1]["end"] + gap:
            merged[-1]["end"] = max(merged[-1]["end"], event["end"])
            merged[-1]["confidence"] = max(merged[-1]["confidence"], event["confidence"])
        else:
            merged.append(event)
    return merged


class YAMNetAnalyzer:
    """A single-file YAMNet analyzer suitable for the workbench queue."""

    manifest = MANIFEST

    def __init__(
        self,
        model_path: str | Path | None = None,
        class_map_path: str | Path | None = None,
        threshold: float = 0.35,
        top_k: int = 5,
    ) -> None:
        raw_model_path = model_path or os.environ.get("AUDIO_WORKBENCH_YAMNET_MODEL")
        self.model_path = Path(raw_model_path) if raw_model_path else None
        self.class_map_path = class_map_path or os.environ.get("AUDIO_WORKBENCH_YAMNET_CLASS_MAP")
        self.threshold = threshold
        self.top_k = max(1, top_k)
        self._model: Any = None
        self._class_names: list[str] | None = None

    def analyze(self, normalized_path: Path) -> dict[str, Any]:
        if self.model_path is None or not self.model_path.is_dir() and not self.model_path.is_file():
            raise AnalyzerUnavailable(
                "YAMNet-Modell fehlt. Setze AUDIO_WORKBENCH_YAMNET_MODEL auf ein lokales TFLite-Modell oder SavedModel."
            )
        samples, sample_rate = _load_normalized_wav(normalized_path)
        if sample_rate != MANIFEST.input_sample_rate:
            raise RuntimeError(f"YAMNet erwartet 16000 Hz, erhalten: {sample_rate} Hz.")
        try:
            import numpy as np
        except ImportError as exc:
            raise AnalyzerUnavailable("YAMNet benötigt NumPy.") from exc

        if self.model_path.suffix.lower() == ".tflite":
            if self._class_names is None:
                label_path = self.class_map_path
                if label_path is None:
                    for candidate in (self.model_path.with_name("yamnet_label_list.txt"), self.model_path.with_name("yamnet_class_map.csv")):
                        if candidate.is_file():
                            label_path = candidate
                            break
                self._class_names = _load_class_names(None, label_path)
            scores = _run_tflite(self.model_path, samples)
            frame_duration = 15_600 / sample_rate
            frame_hop = frame_duration / 2
            backend = "tflite"
        else:
            try:
                import tensorflow_hub as hub
                import numpy as np
            except ImportError as exc:
                raise AnalyzerUnavailable(
                    "YAMNet-SavedModel benötigt die optionalen Pakete 'tensorflow' und 'tensorflow-hub'."
                ) from exc
            if self._model is None:
                self._model = hub.load(str(self.model_path))
                self._class_names = _load_class_names(self._model, self.class_map_path)
            scores, _, _ = self._model(samples)
            scores = np.asarray(_tensor_to_numpy(scores))
            frame_duration = 0.96
            frame_hop = 0.48
            backend = "tensorflow-hub"
        names = self._class_names or []
        raw_events: list[dict[str, Any]] = []
        best_scores: dict[str, float] = {}
        for frame_index, frame in enumerate(scores):
            top_indices = np.argsort(frame)[-self.top_k :][::-1]
            for class_index in top_indices:
                score = float(frame[class_index])
                if score < self.threshold or int(class_index) >= len(names):
                    continue
                label = names[int(class_index)]
                best_scores[label] = max(best_scores.get(label, 0.0), score)
                raw_events.append(
                    {
                        "start": round(frame_index * frame_hop, 3),
                        "end": round(frame_index * frame_hop + frame_duration, 3),
                        "label": label,
                        "confidence": round(score, 3),
                    }
                )
        events = _merge_events(sorted(raw_events, key=lambda item: (item["start"], item["label"])))
        duration = len(samples) / sample_rate if sample_rate else 0.0
        for event in events:
            event["end"] = round(min(event["end"], duration), 3)
        top_labels = [
            {"label": label, "confidence": round(score, 3)}
            for label, score in sorted(best_scores.items(), key=lambda pair: pair[1], reverse=True)[:20]
        ]
        return {
            "type": "audio_events",
            "method": "yamnet",
            "model": self.model_path.name,
            "backend": backend,
            "sample_rate": sample_rate,
            "frame_duration_seconds": frame_duration,
            "frame_hop_seconds": frame_hop,
            "threshold": self.threshold,
            "top_labels": top_labels,
            "events": [event for event in events if event["end"] > event["start"]],
            "analysis_note": "YAMNet liefert AudioSet-Klassen; Ortsklassifikation ist ein eigener Analyzer.",
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Audio Workbench YAMNet adapter")
    parser.add_argument("wav", type=Path)
    parser.add_argument("--model", type=Path, default=None)
    parser.add_argument("--class-map", type=Path, default=None)
    args = parser.parse_args()
    try:
        print(json.dumps(YAMNetAnalyzer(args.model, args.class_map).analyze(args.wav), ensure_ascii=False, indent=2))
    except (AnalyzerUnavailable, RuntimeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
