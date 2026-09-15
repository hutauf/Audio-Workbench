"""BirdNET adapter using birdnetlib (BirdNET v2.4, bundled model).

Loaded only when AUDIO_WORKBENCH_BIRDNET=1 is set. The model weights ship
inside the birdnetlib package and are never downloaded at runtime.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .contracts import AnalyzerManifest, AnalyzerUnavailable


MANIFEST = AnalyzerManifest(
    id="birdnet",
    name="Vogelstimmen / BirdNET",
    description="Artenerkennung auf Zeitfenstern mit BirdNET v2.4",
    technology="BirdNET v2.4 / birdnetlib",
)


class BirdNetAnalyzer:
    manifest = MANIFEST

    def __init__(
        self,
        min_confidence: float = 0.25,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> None:
        self.min_confidence = min_confidence
        self.latitude = latitude
        self.longitude = longitude

    def analyze(self, normalized_path: Path) -> dict[str, Any]:
        try:
            from birdnetlib import Recording
            from birdnetlib.analyzer import Analyzer
        except ImportError as exc:
            raise AnalyzerUnavailable(
                "BirdNET benötigt das optionale Paket 'birdnetlib'."
            ) from exc

        # Ensure file is accessible and readable
        if not normalized_path.exists():
            raise RuntimeError(f"Normalized audio file not found: {normalized_path}")

        analyzer = Analyzer()
        try:
            recording = Recording(
                analyzer,
                str(normalized_path),
                lat=self.latitude,
                lon=self.longitude,
                min_conf=self.min_confidence,
            )
            recording.analyze()
        except Exception as exc:
            # If birdnetlib fails, try reading with soundfile first to verify file integrity
            import soundfile as sf
            try:
                data, samplerate = sf.read(str(normalized_path))
                # File is valid, retry birdnetlib
                recording = Recording(
                    analyzer,
                    str(normalized_path),
                    lat=self.latitude,
                    lon=self.longitude,
                    min_conf=self.min_confidence,
                )
                recording.analyze()
            except Exception:
                raise RuntimeError(f"BirdNET konnte Audio nicht laden: {exc}") from exc

        events: list[dict[str, Any]] = []
        best_scores: dict[str, float] = {}
        for det in recording.detections:
            label = f"{det['common_name']} ({det['scientific_name']})"
            confidence = round(float(det["confidence"]), 3)
            start = round(float(det["start_time"]), 3)
            end = round(float(det["end_time"]), 3)
            best_scores[label] = max(best_scores.get(label, 0.0), confidence)
            events.append(
                {
                    "start": start,
                    "end": end,
                    "label": label,
                    "confidence": confidence,
                }
            )

        top_labels = [
            {"label": label, "confidence": score}
            for label, score in sorted(best_scores.items(), key=lambda p: p[1], reverse=True)[:20]
        ]
        return {
            "type": "audio_events",
            "method": "birdnet",
            "model": "BirdNET_GLOBAL_6K_V2.4",
            "min_confidence": self.min_confidence,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "top_labels": top_labels,
            "events": events,
            "analysis_note": "BirdNET erkennt Vogelarten in 3-Sekunden-Fenstern.",
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Audio Workbench BirdNET adapter")
    parser.add_argument("wav", type=Path)
    parser.add_argument("--min-confidence", type=float, default=0.25)
    parser.add_argument("--lat", type=float, default=None)
    parser.add_argument("--lon", type=float, default=None)
    args = parser.parse_args()
    try:
        print(json.dumps(
            BirdNetAnalyzer(args.min_confidence, args.lat, args.lon).analyze(args.wav),
            ensure_ascii=False, indent=2,
        ))
    except (AnalyzerUnavailable, RuntimeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
