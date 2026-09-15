"""Offline speaker diarization adapter using sherpa-onnx.

The output intentionally uses local labels (``SPEAKER_00`` ...). A later
speaker-profile job can attach persistent identities by comparing embeddings;
diarization alone must not pretend that a local cluster is a real person.
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
    id="diarization",
    name="Sprecher / Diarisierung",
    description="Wer spricht wann innerhalb einer Aufnahme",
    technology="sherpa-onnx + pyannote segmentation + speaker embeddings",
)


def _load_normalized_wav(path: Path) -> tuple[Any, int]:
    try:
        import numpy as np
    except ImportError as exc:
        raise AnalyzerUnavailable("Diarisierung benötigt NumPy.") from exc

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


class SherpaDiarizationAnalyzer:
    manifest = MANIFEST

    def __init__(
        self,
        segmentation_model: str | Path | None = None,
        embedding_model: str | Path | None = None,
        num_speakers: int = -1,
        cluster_threshold: float = 0.5,
    ) -> None:
        raw_segmentation = segmentation_model or os.environ.get("AUDIO_WORKBENCH_DIARIZATION_SEGMENTATION_MODEL")
        raw_embedding = embedding_model or os.environ.get("AUDIO_WORKBENCH_DIARIZATION_EMBEDDING_MODEL")
        self.segmentation_model = Path(raw_segmentation) if raw_segmentation else None
        self.embedding_model = Path(raw_embedding) if raw_embedding else None
        self.num_speakers = num_speakers
        self.cluster_threshold = cluster_threshold

    def analyze(self, normalized_path: Path) -> dict[str, Any]:
        if self.segmentation_model is None or not self.segmentation_model.is_file():
            raise AnalyzerUnavailable(
                "Diarisierungs-Segmentierungsmodell fehlt. Setze AUDIO_WORKBENCH_DIARIZATION_SEGMENTATION_MODEL."
            )
        if self.embedding_model is None or not self.embedding_model.is_file():
            raise AnalyzerUnavailable(
                "Speaker-Embedding-Modell fehlt. Setze AUDIO_WORKBENCH_DIARIZATION_EMBEDDING_MODEL."
            )
        try:
            import sherpa_onnx
        except ImportError as exc:
            raise AnalyzerUnavailable("Diarisierung benötigt das optionale Paket 'sherpa-onnx'.") from exc

        samples, sample_rate = _load_normalized_wav(normalized_path)
        config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=str(self.segmentation_model),
                    window_shift_ratio=0.1,
                ),
            ),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(self.embedding_model),
            ),
            clustering=sherpa_onnx.FastClusteringConfig(
                num_clusters=self.num_speakers,
                threshold=self.cluster_threshold,
            ),
            min_duration_on=0.3,
            min_duration_off=0.5,
        )
        if not config.validate():
            raise RuntimeError("Die sherpa-onnx-Diarisierungskonfiguration ist ungültig.")
        diarizer = sherpa_onnx.OfflineSpeakerDiarization(config)
        if sample_rate != diarizer.sample_rate:
            raise RuntimeError(
                f"Diarisierung erwartet {diarizer.sample_rate} Hz, erhalten: {sample_rate} Hz."
            )
        result = diarizer.process(samples).sort_by_start_time()
        events = [
            {
                "start": round(float(segment.start), 3),
                "end": round(float(segment.end), 3),
                "label": f"SPEAKER_{int(segment.speaker):02}",
                "speaker_index": int(segment.speaker),
                "confidence": None,
            }
            for segment in result
            if float(segment.end) > float(segment.start)
        ]
        return {
            "type": "diarization",
            "method": "sherpa-onnx/offline",
            "segmentation_model": self.segmentation_model.name,
            "embedding_model": self.embedding_model.name,
            "num_speakers": self.num_speakers,
            "cluster_threshold": self.cluster_threshold,
            "events": events,
            "analysis_note": "SPEAKER_XX ist nur eine lokale Cluster-ID; globale Namen benötigen Enrollment und Speaker-ID.",
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Audio Workbench sherpa diarization adapter")
    parser.add_argument("wav", type=Path)
    parser.add_argument("--segmentation-model", type=Path, required=True)
    parser.add_argument("--embedding-model", type=Path, required=True)
    parser.add_argument("--num-speakers", type=int, default=-1)
    parser.add_argument("--cluster-threshold", type=float, default=0.5)
    args = parser.parse_args()
    try:
        analyzer = SherpaDiarizationAnalyzer(
            args.segmentation_model,
            args.embedding_model,
            args.num_speakers,
            args.cluster_threshold,
        )
        print(json.dumps(analyzer.analyze(args.wav), ensure_ascii=False, indent=2))
    except (AnalyzerUnavailable, RuntimeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
