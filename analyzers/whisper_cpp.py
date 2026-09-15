"""Process-isolated whisper.cpp adapter.

whisper.cpp is intentionally invoked as an external executable. That keeps
the main web process free of a large ML dependency and makes the exact same
adapter usable on a Raspberry Pi, N100 or GPU worker.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .contracts import AnalyzerManifest, AnalyzerUnavailable


MANIFEST = AnalyzerManifest(
    id="whisper_cpp",
    name="Transkript / whisper.cpp",
    description="Lokale Speech-to-Text-Analyse in einem separaten Prozess",
    technology="whisper.cpp",
)


def _timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        # whisper.cpp JSON offsets are milliseconds; accepting seconds as a
        # fallback makes the adapter tolerant of alternate exporters.
        return float(value) / 1000.0 if float(value) > 100 else float(value)
    if not isinstance(value, str):
        return None
    text = value.strip().replace(",", ".")
    pieces = text.split(":")
    try:
        if len(pieces) == 3:
            hours, minutes, seconds = pieces
            return float(hours) * 3600 + float(minutes) * 60 + float(seconds)
        return float(text)
    except ValueError:
        return None


def _segment_time(segment: dict[str, Any], side: str) -> float | None:
    timestamps = segment.get("timestamps") or {}
    offsets = segment.get("offsets") or {}
    return _timestamp(timestamps.get(side)) or _timestamp(offsets.get(side))


def _read_transcription(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("transcription") or payload.get("segments") or []
    if isinstance(rows, dict):
        rows = rows.get("segments") or []
    segments: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        start = _segment_time(row, "from")
        end = _segment_time(row, "to")
        if start is None:
            start = _segment_time(row, "start")
        if end is None:
            end = _segment_time(row, "end")
        segments.append(
            {
                "start": round(max(0.0, start or 0.0), 3),
                "end": round(max(start or 0.0, end or start or 0.0), 3),
                "text": text,
            }
        )
    return segments


class WhisperCppAnalyzer:
    manifest = MANIFEST

    def __init__(
        self,
        executable: str | Path | None = None,
        model_path: str | Path | None = None,
        language: str = "de",
    ) -> None:
        self.executable = str(executable or os.environ.get("AUDIO_WORKBENCH_WHISPER_CLI", "whisper-cli"))
        self.model_path = Path(model_path or os.environ.get("AUDIO_WORKBENCH_WHISPER_MODEL", ""))
        self.language = language

    def analyze(self, normalized_path: Path) -> dict[str, Any]:
        if not self.model_path.is_file():
            raise AnalyzerUnavailable(
                "Whisper-Modell fehlt. Setze AUDIO_WORKBENCH_WHISPER_MODEL auf eine ggml-*.bin-Datei."
            )
        with tempfile.TemporaryDirectory(prefix="audio-workbench-whisper-") as temp_dir:
            output_prefix = Path(temp_dir) / "transcript"
            command = [
                self.executable,
                "-m",
                str(self.model_path),
                "-f",
                str(normalized_path),
                "-l",
                self.language,
                "-oj",
                "-of",
                str(output_prefix),
            ]
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=6 * 60 * 60,
                    check=False,
                )
            except FileNotFoundError as exc:
                raise AnalyzerUnavailable(
                    f"whisper.cpp wurde nicht gefunden: {self.executable}"
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("Whisper-Analyse hat das Zeitlimit überschritten.") from exc
            if completed.returncode != 0:
                detail = completed.stderr.strip().splitlines()[-1:] or ["unbekannter whisper.cpp-Fehler"]
                raise RuntimeError("whisper.cpp konnte die Datei nicht lesen: " + detail[0])
            json_path = output_prefix.with_suffix(".json")
            if not json_path.is_file():
                raise RuntimeError("whisper.cpp hat keine JSON-Ausgabe erzeugt.")
            segments = _read_transcription(json_path)

        return {
            "type": "transcript",
            "method": "whisper.cpp",
            "model": self.model_path.name,
            "language": self.language,
            "text": " ".join(segment["text"] for segment in segments),
            "segments": segments,
            "analysis_note": "Transkriptionsadapter; Sprecherzuordnung kommt aus dem separaten Diarisierungsjob.",
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Audio Workbench whisper.cpp adapter")
    parser.add_argument("wav", type=Path)
    parser.add_argument("--model", type=Path, default=None)
    parser.add_argument("--executable", default=None)
    parser.add_argument("--language", default="de")
    args = parser.parse_args()
    try:
        analyzer = WhisperCppAnalyzer(args.executable, args.model, args.language)
        print(json.dumps(analyzer.analyze(args.wav), ensure_ascii=False, indent=2))
    except (AnalyzerUnavailable, RuntimeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
