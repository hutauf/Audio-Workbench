#!/usr/bin/env python3
"""Audio Workbench v0.1, a deliberately small local-only prototype.

The server uses only Python's standard library. NumPy is optional and enables
the richer waveform and spectrogram calculation. FFmpeg is used to turn any
format it understands into a stable mono, 16 kHz PCM working copy.
"""

from __future__ import annotations

import argparse
import json
import math
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import uuid
import wave
from array import array
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

try:
    import numpy as np
except ImportError:  # The prototype still starts without NumPy.
    np = None


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DATA_DIR = Path(os.environ.get("AUDIO_WORKBENCH_DATA", ROOT / "data"))
DB_PATH = DATA_DIR / "workbench.sqlite3"
DB_LOCK = threading.Lock()
EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="audio-worker")
MAX_UPLOAD_BYTES = 512 * 1024 * 1024


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "recordings").mkdir(exist_ok=True)
    with DB_LOCK, connect_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS recordings (
                id TEXT PRIMARY KEY,
                original_name TEXT NOT NULL,
                original_path TEXT NOT NULL,
                normalized_path TEXT,
                duration REAL,
                sample_rate INTEGER,
                channels INTEGER,
                sample_width INTEGER,
                size_bytes INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                error TEXT
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                recording_id TEXT NOT NULL,
                analyzer_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                progress INTEGER NOT NULL DEFAULT 0,
                message TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                error TEXT,
                UNIQUE(recording_id, analyzer_id)
            );

            CREATE TABLE IF NOT EXISTS results (
                id TEXT PRIMARY KEY,
                recording_id TEXT NOT NULL,
                analyzer_id TEXT NOT NULL,
                result_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(recording_id, analyzer_id)
            );
            """
        )


def fetch_one(query: str, args: tuple[Any, ...] = ()) -> sqlite3.Row | None:
    with DB_LOCK, connect_db() as conn:
        return conn.execute(query, args).fetchone()


def fetch_all(query: str, args: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    with DB_LOCK, connect_db() as conn:
        return list(conn.execute(query, args).fetchall())


def execute(query: str, args: tuple[Any, ...] = ()) -> None:
    with DB_LOCK, connect_db() as conn:
        conn.execute(query, args)
        conn.commit()


def sanitize_filename(name: str) -> str:
    name = Path(name).name.replace("\x00", "")
    name = re.sub(r"[^\w.() -]+", "_", name, flags=re.UNICODE).strip(" .")
    return name[:180] or "audio.bin"


def parse_multipart(handler: BaseHTTPRequestHandler) -> tuple[str, bytes] | None:
    content_type = handler.headers.get("Content-Type", "")
    match = re.search(r"boundary=(?:\"([^\"]+)\"|([^;]+))", content_type)
    if not match:
        return None
    boundary = (match.group(1) or match.group(2)).strip().encode()
    try:
        length = int(handler.headers.get("Content-Length", "0"))
    except ValueError:
        return None
    if length <= 0 or length > MAX_UPLOAD_BYTES:
        raise ValueError("Die Datei ist zu groß oder der Upload ist leer.")
    body = handler.rfile.read(length)
    marker = b"--" + boundary
    for part in body.split(marker)[1:]:
        if part.startswith(b"--"):
            break
        part = part.lstrip(b"\r\n")
        if not part:
            continue
        try:
            header_bytes, payload = part.split(b"\r\n\r\n", 1)
        except ValueError:
            continue
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        headers = header_bytes.decode("latin-1")
        disposition = re.search(r'Content-Disposition:.*?name="([^"]+)"', headers, re.I)
        if not disposition or disposition.group(1) != "file":
            continue
        filename = re.search(r'filename="([^"]*)"', headers, re.I)
        return sanitize_filename(filename.group(1) if filename else "audio.bin"), payload
    return None


def run_ffmpeg(original: Path, normalized: Path) -> None:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(original),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(normalized),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=6 * 60 * 60)
    except FileNotFoundError as exc:
        raise RuntimeError("FFmpeg wurde nicht gefunden.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Die Normalisierung hat das Zeitlimit überschritten.") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()[-1:] or ["unbekannter FFmpeg-Fehler"]
        raise RuntimeError("FFmpeg konnte die Datei nicht lesen: " + detail[0])


def load_audio(path: Path) -> tuple[Any, int, int]:
    with wave.open(str(path), "rb") as wav:
        sample_rate = wav.getframerate()
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        frames = wav.readframes(wav.getnframes())
    if sample_width != 2:
        raise RuntimeError("Die Arbeitskopie ist nicht 16-bit PCM.")
    if np is not None:
        values = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
        if channels > 1:
            values = values.reshape(-1, channels).mean(axis=1)
        return values, sample_rate, channels
    values = array("h")
    values.frombytes(frames)
    if channels > 1:
        values = array("h", [sum(values[i : i + channels]) // channels for i in range(0, len(values), channels)])
    return [sample / 32768.0 for sample in values], sample_rate, channels


def audio_profile(path: Path) -> dict[str, Any]:
    samples, sample_rate, channels = load_audio(path)
    count = len(samples)
    if np is not None:
        peak = float(np.max(np.abs(samples))) if count else 0.0
        rms = float(np.sqrt(np.mean(samples * samples))) if count else 0.0
        dc = float(np.mean(samples)) if count else 0.0
    else:
        absolute = [abs(value) for value in samples]
        peak = max(absolute, default=0.0)
        rms = math.sqrt(sum(value * value for value in samples) / count) if count else 0.0
        dc = sum(samples) / count if count else 0.0
    duration = count / sample_rate if sample_rate else 0.0
    return {
        "type": "audio_profile",
        "duration_seconds": round(duration, 3),
        "sample_rate": sample_rate,
        "channels": channels,
        "peak_dbfs": round(20 * math.log10(max(peak, 1e-9)), 1),
        "rms_dbfs": round(20 * math.log10(max(rms, 1e-9)), 1),
        "crest_factor": round(peak / max(rms, 1e-9), 2),
        "dc_offset": round(dc, 6),
        "analysis_note": "Grundmetriken aus der normalisierten Arbeitskopie",
    }


def waveform_and_spectrogram(path: Path) -> dict[str, Any]:
    samples, sample_rate, _ = load_audio(path)
    if len(samples) == 0:
        return {"type": "waveform", "sample_rate": sample_rate, "waveform": [], "spectrogram": None}
    if np is None:
        values = samples
        bin_count = min(900, max(120, int(len(values) / max(sample_rate, 1) * 3)))
        waveform = []
        for index in range(bin_count):
            start = int(index * len(values) / bin_count)
            end = max(start + 1, int((index + 1) * len(values) / bin_count))
            chunk = values[start:end]
            waveform.append({"min": round(min(chunk, default=0.0), 4), "max": round(max(chunk, default=0.0), 4)})
        return {"type": "waveform", "sample_rate": sample_rate, "waveform": waveform, "spectrogram": None}

    total = len(samples)
    bin_count = min(1200, max(180, int(total / max(sample_rate, 1) * 4)))
    edges = np.linspace(0, total, bin_count + 1, dtype=np.int64)
    waveform = []
    for start, end in zip(edges[:-1], edges[1:]):
        chunk = samples[start:max(start + 1, end)]
        waveform.append(
            {
                "min": round(float(np.min(chunk)), 4),
                "max": round(float(np.max(chunk)), 4),
                "rms": round(float(np.sqrt(np.mean(chunk * chunk))), 4),
            }
        )

    # Spectrogramm parameters: nperseg = sampling_rate / 10, 80% overlap
    nperseg = max(256, sample_rate // 10)
    hop = max(1, int(nperseg * 0.2))  # 80% overlap = 20% hop
    window = np.hanning(nperseg).astype(np.float32)

    spectra = []
    start = 0
    while start + nperseg <= len(samples):
        frame = samples[start : start + nperseg]
        spectrum = np.abs(np.fft.rfft(frame * window))
        spectra.append(spectrum)
        start += hop
        if len(spectra) >= 400:  # Limit to reasonable resolution
            break

    if not spectra:
        # Fallback for very short audio
        frame = np.pad(samples, (0, max(0, nperseg - len(samples))))[:nperseg]
        spectra.append(np.abs(np.fft.rfft(frame * window)))

    matrix = np.asarray(spectra, dtype=np.float32)
    db = 20 * np.log10(matrix + 1e-6)
    low = float(np.percentile(db, 8))
    high = float(np.percentile(db, 99))
    normalized = np.clip((db - low) / max(high - low, 1e-6), 0.0, 1.0)
    frequency_bins = 96
    frequency_edges = np.linspace(0, normalized.shape[1], frequency_bins + 1, dtype=np.int64)
    compact = []
    for row in normalized:
        compact.append(
            [round(float(np.mean(row[a:b])), 3) for a, b in zip(frequency_edges[:-1], frequency_edges[1:])]
        )

    return {
        "type": "waveform",
        "sample_rate": sample_rate,
        "waveform": waveform,
        "spectrogram": {
            "values": compact,
            "frequency_max": round(sample_rate / 2),
            "frequency_bins": frequency_bins,
            "time_bins": len(compact),
        },
    }


def vad_energy(path: Path) -> dict[str, Any]:
    samples, sample_rate, _ = load_audio(path)
    frame_size = max(1, int(sample_rate * 0.25))
    if np is not None:
        count = len(samples) // frame_size
        if count == 0:
            rms_db = np.asarray([], dtype=np.float32)
        else:
            frames = samples[: count * frame_size].reshape(count, frame_size)
            rms = np.sqrt(np.mean(frames * frames, axis=1))
            rms_db = 20 * np.log10(rms + 1e-7)
        noise_floor = float(np.percentile(rms_db, 20)) if len(rms_db) else -80.0
        threshold = max(-42.0, noise_floor + 8.0)
        active = [bool(value > threshold) for value in rms_db]
        values = [float(value) for value in rms_db]
    else:
        values = []
        active = []
        for start in range(0, len(samples), frame_size):
            frame = samples[start : start + frame_size]
            rms = math.sqrt(sum(value * value for value in frame) / max(len(frame), 1))
            values.append(20 * math.log10(rms + 1e-7))
        noise_floor = sorted(values)[max(0, int(len(values) * 0.2) - 1)] if values else -80.0
        threshold = max(-42.0, noise_floor + 8.0)
        active = [value > threshold for value in values]

    # Join tiny gaps, then discard very short bursts.
    for index in range(1, len(active) - 1):
        if not active[index] and active[index - 1] and active[index + 1]:
            active[index] = True
    events: list[dict[str, Any]] = []
    start_index: int | None = None
    for index, is_active in enumerate(active + [False]):
        if is_active and start_index is None:
            start_index = index
        elif not is_active and start_index is not None:
            start = max(0.0, start_index * frame_size / sample_rate - 0.05)
            end = min(len(samples) / sample_rate, index * frame_size / sample_rate + 0.05)
            if end - start >= 0.30:
                mean_db = sum(values[start_index:index]) / max(index - start_index, 1)
                confidence = min(0.98, max(0.50, 0.60 + (mean_db - threshold) / 40))
                events.append(
                    {
                        "start": round(start, 3),
                        "end": round(end, 3),
                        "label": "Sprache wahrscheinlich",
                        "confidence": round(confidence, 2),
                    }
                )
            start_index = None
    active_seconds = sum(event["end"] - event["start"] for event in events)
    duration = len(samples) / sample_rate if sample_rate else 0.0
    return {
        "type": "vad",
        "method": "energy-baseline",
        "threshold_dbfs": round(threshold, 1),
        "noise_floor_dbfs": round(noise_floor, 1),
        "active_seconds": round(active_seconds, 2),
        "activity_ratio": round(active_seconds / max(duration, 1e-9), 3),
        "events": events,
        "analysis_note": "Vorläufige Pegel-VAD. Später austauschbar gegen Silero oder sherpa-onnx.",
    }


Analyzer = Callable[[Path], dict[str, Any]]
ANALYZERS: dict[str, dict[str, Any]] = {
    "audio_profile": {
        "name": "Audio-Profil",
        "description": "Format, Pegel und Grundmetriken",
        "state": "ready",
        "run": audio_profile,
    },
    "waveform": {
        "name": "Waveform & Spektrum",
        "description": "Zeitverlauf und Spektrogramm",
        "state": "ready",
        "run": waveform_and_spectrogram,
    },
    "vad_energy": {
        "name": "Sprachaktivität",
        "description": "Zeitbereiche mit Sprache, Baseline",
        "state": "ready",
        "run": vad_energy,
    },
}

PLANNED_ANALYZERS = [
    {
        "id": "transcription",
        "name": "Transkript",
        "description": "Lokale Speech-to-Text-Analyse",
        "state": "planned",
        "technology": "whisper.cpp, faster-whisper oder sherpa-onnx",
    },
    {
        "id": "diarization",
        "name": "Sprecher",
        "description": "Diarisierung und Sprecher-Embeddings",
        "state": "planned",
        "technology": "sherpa-onnx oder pyannote",
    },
    {
        "id": "audio_tagging",
        "name": "Sound Events",
        "description": "Allgemeine Geräuschklassifikation",
        "state": "planned",
        "technology": "YAMNet, PANNs oder sherpa-onnx",
    },
    {
        "id": "scene_classification",
        "name": "Ort / Szene",
        "description": "Akustische Umgebung wie Wald, U-Bahn oder Zuhause",
        "state": "planned",
        "technology": "DCASE Acoustic Scene Classification",
    },
    {
        "id": "birdnet",
        "name": "Vogelstimmen",
        "description": "Arten und Zeitbereiche",
        "state": "planned",
        "technology": "BirdNET lokal",
    },
    {
        "id": "spatial_audio",
        "name": "Spatial Audio",
        "description": "DOA, Beamforming und Separation",
        "state": "planned",
        "technology": "ODAS, pyroomacoustics",
    },
]


def register_configured_optional_analyzers() -> None:
    """Add optional ML modules only when their local model is configured.

    Importing the web app must not import TensorFlow or sherpa-onnx. A model
    path is therefore the explicit opt-in that turns an adapter into a queue
    job. The default installation keeps the energy VAD fallback active.
    """

    silero_model = os.environ.get("AUDIO_WORKBENCH_SILERO_VAD_MODEL")
    if silero_model:
        from analyzers.sherpa_vad import analyze as analyze_silero_vad

        ANALYZERS["vad_silero"] = {
            "name": "Sprachaktivität / Silero",
            "description": "ML-Sprachsegmente über sherpa-onnx",
            "technology": "sherpa-onnx + Silero VAD",
            "state": "ready",
            "run": lambda path, model=silero_model: analyze_silero_vad(path, model),
        }

    yamnet_model = os.environ.get("AUDIO_WORKBENCH_YAMNET_MODEL")
    if yamnet_model:
        from analyzers.yamnet import YAMNetAnalyzer

        yamnet = YAMNetAnalyzer(
            model_path=yamnet_model,
            class_map_path=os.environ.get("AUDIO_WORKBENCH_YAMNET_CLASS_MAP"),
        )
        ANALYZERS["yamnet"] = {
            "name": "Sound Events / YAMNet",
            "description": "AudioSet-Klassen auf Zeitfenstern",
            "technology": "YAMNet / AudioSet",
            "state": "ready",
            "run": yamnet.analyze,
        }

    whisper_model = os.environ.get("AUDIO_WORKBENCH_WHISPER_MODEL")
    if whisper_model:
        from analyzers.whisper_cpp import WhisperCppAnalyzer

        whisper = WhisperCppAnalyzer(
            executable=os.environ.get("AUDIO_WORKBENCH_WHISPER_CLI"),
            model_path=whisper_model,
            language=os.environ.get("AUDIO_WORKBENCH_WHISPER_LANGUAGE", "de"),
        )
        ANALYZERS["whisper_cpp"] = {
            "name": "Transkript / whisper.cpp",
            "description": "Lokale Speech-to-Text-Analyse als Prozess",
            "technology": "whisper.cpp",
            "state": "ready",
            "run": whisper.analyze,
        }

    diarization_segmentation = os.environ.get("AUDIO_WORKBENCH_DIARIZATION_SEGMENTATION_MODEL")
    diarization_embedding = os.environ.get("AUDIO_WORKBENCH_DIARIZATION_EMBEDDING_MODEL")
    if diarization_segmentation and diarization_embedding:
        from analyzers.sherpa_diarization import SherpaDiarizationAnalyzer

        diarization = SherpaDiarizationAnalyzer(
            segmentation_model=diarization_segmentation,
            embedding_model=diarization_embedding,
            num_speakers=int(os.environ.get("AUDIO_WORKBENCH_DIARIZATION_NUM_SPEAKERS", "-1")),
            cluster_threshold=float(os.environ.get("AUDIO_WORKBENCH_DIARIZATION_CLUSTER_THRESHOLD", "0.5")),
        )
        ANALYZERS["diarization"] = {
            "name": "Sprecher / Diarisierung",
            "description": "Lokale Sprechersegmente als SPEAKER_XX",
            "technology": "sherpa-onnx + pyannote segmentation",
            "state": "ready",
            "run": diarization.analyze,
        }

    if os.environ.get("AUDIO_WORKBENCH_BIRDNET", "").strip() == "1":
        from analyzers.birdnet import BirdNetAnalyzer

        birdnet = BirdNetAnalyzer(
            min_confidence=float(os.environ.get("AUDIO_WORKBENCH_BIRDNET_MIN_CONFIDENCE", "0.25")),
            latitude=float(os.environ.get("AUDIO_WORKBENCH_BIRDNET_LAT")) if os.environ.get("AUDIO_WORKBENCH_BIRDNET_LAT") else None,
            longitude=float(os.environ.get("AUDIO_WORKBENCH_BIRDNET_LON")) if os.environ.get("AUDIO_WORKBENCH_BIRDNET_LON") else None,
        )
        ANALYZERS["birdnet"] = {
            "name": "Vogelstimmen / BirdNET",
            "description": "Artenerkennung mit BirdNET v2.4",
            "technology": "BirdNET v2.4 / birdnetlib",
            "state": "ready",
            "run": birdnet.analyze,
        }


register_configured_optional_analyzers()


def recording_row(recording_id: str) -> sqlite3.Row:
    row = fetch_one("SELECT * FROM recordings WHERE id = ?", (recording_id,))
    if row is None:
        raise KeyError(recording_id)
    return row


def set_recording_status(recording_id: str, status: str, error: str | None = None) -> None:
    execute("UPDATE recordings SET status = ?, error = ? WHERE id = ?", (status, error, recording_id))


def set_job(recording_id: str, analyzer_id: str, status: str, progress: int, message: str | None = None, error: str | None = None) -> None:
    execute(
        """UPDATE jobs SET status = ?, progress = ?, message = ?, error = ?,
           started_at = CASE WHEN ? = 'running' AND started_at IS NULL THEN ? ELSE started_at END,
           finished_at = CASE WHEN ? IN ('done', 'failed') THEN ? ELSE finished_at END
           WHERE recording_id = ? AND analyzer_id = ?""",
        (status, progress, message, error, status, now_iso(), status, now_iso(), recording_id, analyzer_id),
    )


def save_result(recording_id: str, analyzer_id: str, payload: dict[str, Any]) -> None:
    result_id = uuid.uuid4().hex
    execute(
        """INSERT INTO results (id, recording_id, analyzer_id, result_type, payload_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(recording_id, analyzer_id) DO UPDATE SET
             result_type = excluded.result_type,
             payload_json = excluded.payload_json,
             created_at = excluded.created_at""",
        (result_id, recording_id, analyzer_id, payload.get("type", "generic"), json.dumps(payload, ensure_ascii=False), now_iso()),
    )


def process_recording(recording_id: str) -> None:
    row = recording_row(recording_id)
    original = Path(row["original_path"])
    normalized = Path(row["normalized_path"])

    # Normalize if not yet done
    if not normalized.exists() or row["status"] in ("queued", "normalizing"):
        try:
            set_recording_status(recording_id, "normalizing")
            run_ffmpeg(original, normalized)
            with wave.open(str(normalized), "rb") as wav:
                duration = wav.getnframes() / max(wav.getframerate(), 1)
                sample_rate = wav.getframerate()
                channels = wav.getnchannels()
                sample_width = wav.getsampwidth()
            execute(
                "UPDATE recordings SET duration = ?, sample_rate = ?, channels = ?, sample_width = ?, status = 'processing' WHERE id = ?",
                (duration, sample_rate, channels, sample_width, recording_id),
            )
        except Exception as exc:
            set_recording_status(recording_id, "failed", str(exc))
            return

    # Process only queued jobs
    queued_jobs = fetch_all(
        "SELECT analyzer_id FROM jobs WHERE recording_id = ? AND status = 'queued' ORDER BY created_at",
        (recording_id,),
    )
    if not queued_jobs:
        # No queued jobs, check status
        failed = fetch_one("SELECT COUNT(*) AS count FROM jobs WHERE recording_id = ? AND status = 'failed'", (recording_id,))["count"]
        set_recording_status(recording_id, "ready" if not failed else "partial")
        return

    set_recording_status(recording_id, "processing")
    for job_row in queued_jobs:
        analyzer_id = job_row["analyzer_id"]
        if analyzer_id not in ANALYZERS:
            set_job(recording_id, analyzer_id, "failed", 100, "Analyzer nicht verfügbar", f"Analyzer '{analyzer_id}' ist nicht registriert.")
            continue

        manifest = ANALYZERS[analyzer_id]
        set_job(recording_id, analyzer_id, "running", 5, "Analyse läuft")
        try:
            payload = manifest["run"](normalized)
            save_result(recording_id, analyzer_id, payload)
            set_job(recording_id, analyzer_id, "done", 100, "Fertig")
        except Exception as exc:
            set_job(recording_id, analyzer_id, "failed", 100, "Analyse fehlgeschlagen", str(exc))

    failed = fetch_one("SELECT COUNT(*) AS count FROM jobs WHERE recording_id = ? AND status = 'failed'", (recording_id,))["count"]
    set_recording_status(recording_id, "ready" if not failed else "partial")


def public_recording(row: sqlite3.Row) -> dict[str, Any]:
    jobs = fetch_all("SELECT * FROM jobs WHERE recording_id = ? ORDER BY created_at", (row["id"],))
    return {
        "id": row["id"],
        "original_name": row["original_name"],
        "duration": row["duration"],
        "sample_rate": row["sample_rate"],
        "channels": row["channels"],
        "size_bytes": row["size_bytes"],
        "created_at": row["created_at"],
        "status": row["status"],
        "error": row["error"],
        "jobs": [
            {
                "id": job["id"],
                "analyzer_id": job["analyzer_id"],
                "status": job["status"],
                "progress": job["progress"],
                "message": job["message"],
                "error": job["error"],
            }
            for job in jobs
        ],
    }


def ensure_analyzer_jobs(recording_id: str) -> None:
    """Ensure all currently registered analyzers have a job entry."""
    existing_jobs = {
        job["analyzer_id"]
        for job in fetch_all("SELECT analyzer_id FROM jobs WHERE recording_id = ?", (recording_id,))
    }
    for analyzer_id in ANALYZERS:
        if analyzer_id not in existing_jobs:
            execute(
                """INSERT INTO jobs (id, recording_id, analyzer_id, status, progress, message, created_at)
                   VALUES (?, ?, ?, 'queued', 0, 'Neu hinzugefügter Analyzer', ?)""",
                (uuid.uuid4().hex, recording_id, analyzer_id, now_iso()),
            )


def detail_recording(recording_id: str) -> dict[str, Any]:
    row = recording_row(recording_id)
    ensure_analyzer_jobs(recording_id)
    result_rows = fetch_all("SELECT * FROM results WHERE recording_id = ? ORDER BY created_at", (recording_id,))
    payloads = []
    for result in result_rows:
        try:
            payload = json.loads(result["payload_json"])
        except json.JSONDecodeError:
            payload = {"type": result["result_type"]}
        payloads.append({"analyzer_id": result["analyzer_id"], "result_type": result["result_type"], "payload": payload})
    item = public_recording(row)
    item["audio_url"] = f"/media/{recording_id}/audio"
    item["results"] = payloads
    return item


def read_file_range(path: Path, handler: BaseHTTPRequestHandler) -> None:
    size = path.stat().st_size
    range_header = handler.headers.get("Range")
    start, end = 0, size - 1
    status = HTTPStatus.OK
    if range_header and range_header.startswith("bytes="):
        match = re.match(r"bytes=(\d*)-(\d*)", range_header)
        if match:
            if match.group(1):
                start = int(match.group(1))
            if match.group(2):
                end = int(match.group(2))
            if not match.group(1) and match.group(2):
                start = max(0, size - int(match.group(2)))
            end = min(end, size - 1)
            if start <= end < size:
                status = HTTPStatus.PARTIAL_CONTENT
    length = end - start + 1
    handler.send_response(status)
    handler.send_header("Content-Type", "audio/wav")
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Content-Length", str(length))
    if status == HTTPStatus.PARTIAL_CONTENT:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.end_headers()
    with path.open("rb") as source:
        source.seek(start)
        remaining = length
        while remaining:
            chunk = source.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            handler.wfile.write(chunk)
            remaining -= len(chunk)


class Handler(BaseHTTPRequestHandler):
    server_version = "AudioWorkbench/0.2"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status: HTTPStatus, message: str) -> None:
        self.send_json({"error": message}, status)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path == "/api/analyzers":
                analyzers = [
                    {
                        "id": key,
                        "name": value["name"],
                        "description": value["description"],
                        "technology": value.get("technology", "built-in"),
                        "state": value["state"],
                    }
                    for key, value in ANALYZERS.items()
                ] + [item for item in PLANNED_ANALYZERS if item["id"] not in ANALYZERS]
                return self.send_json(analyzers)
            if path == "/api/recordings":
                rows = fetch_all("SELECT * FROM recordings ORDER BY created_at DESC")
                return self.send_json([public_recording(row) for row in rows])
            match = re.fullmatch(r"/api/recordings/([a-f0-9]+)/?", path)
            if match:
                return self.send_json(detail_recording(match.group(1)))
            match = re.fullmatch(r"/media/([a-f0-9]+)/audio", path)
            if match:
                row = recording_row(match.group(1))
                candidate = Path(row["normalized_path"])
                if not candidate.exists():
                    candidate = Path(row["original_path"])
                return read_file_range(candidate, self)
            if path == "/" or path == "/index.html":
                return self.serve_static("index.html")
            if path.startswith("/static/"):
                return self.serve_static(path.removeprefix("/static/"))
            return self.send_error_json(HTTPStatus.NOT_FOUND, "Nicht gefunden")
        except KeyError:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Aufnahme nicht gefunden")
        except FileNotFoundError:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Datei nicht gefunden")
        except Exception as exc:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def serve_static(self, relative: str) -> None:
        candidate = (STATIC_DIR / relative).resolve()
        if STATIC_DIR not in candidate.parents or not candidate.is_file():
            return self.send_error_json(HTTPStatus.NOT_FOUND, "Nicht gefunden")
        body = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        # Rerun endpoint
        match = re.fullmatch(r"/api/recordings/([a-f0-9]+)/rerun/?", path)
        if match:
            recording_id = match.group(1)
            try:
                from urllib.parse import parse_qs
                query = parse_qs(parsed.query)
                analyzer_ids = query.get("analyzer_id", list(ANALYZERS.keys()))
                if isinstance(analyzer_ids, str):
                    analyzer_ids = [analyzer_ids]

                # Reset requested jobs to queued
                for analyzer_id in analyzer_ids:
                    if analyzer_id not in ANALYZERS:
                        continue
                    existing = fetch_one(
                        "SELECT id FROM jobs WHERE recording_id = ? AND analyzer_id = ?",
                        (recording_id, analyzer_id),
                    )
                    if existing:
                        execute(
                            """UPDATE jobs SET status = 'queued', progress = 0, message = 'Neu gestartet',
                               error = NULL, started_at = NULL, finished_at = NULL
                               WHERE recording_id = ? AND analyzer_id = ?""",
                            (recording_id, analyzer_id),
                        )
                    else:
                        execute(
                            """INSERT INTO jobs (id, recording_id, analyzer_id, status, progress, message, created_at)
                               VALUES (?, ?, ?, 'queued', 0, 'Wartet auf Worker', ?)""",
                            (uuid.uuid4().hex, recording_id, analyzer_id, now_iso()),
                        )

                # Resubmit to queue
                EXECUTOR.submit(process_recording, recording_id)
                return self.send_json(detail_recording(recording_id), HTTPStatus.OK)
            except Exception as exc:
                return self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

        # Import endpoint
        if path != "/api/import":
            return self.send_error_json(HTTPStatus.NOT_FOUND, "Nicht gefunden")
        try:
            uploaded = parse_multipart(self)
            if not uploaded:
                return self.send_error_json(HTTPStatus.BAD_REQUEST, "Bitte eine Datei im Feld 'file' senden.")
            filename, content = uploaded
            recording_id = uuid.uuid4().hex
            recording_dir = DATA_DIR / "recordings" / recording_id
            recording_dir.mkdir(parents=True, exist_ok=False)
            original = recording_dir / f"original{Path(filename).suffix.lower() or '.bin'}"
            normalized = recording_dir / "normalized.wav"
            original.write_bytes(content)
            execute(
                """INSERT INTO recordings
                   (id, original_name, original_path, normalized_path, size_bytes, created_at, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'queued')""",
                (recording_id, filename, str(original), str(normalized), len(content), now_iso()),
            )
            for analyzer_id in ANALYZERS:
                execute(
                    """INSERT INTO jobs (id, recording_id, analyzer_id, status, progress, message, created_at)
                       VALUES (?, ?, ?, 'queued', 0, 'Wartet auf Normalisierung', ?)""",
                    (uuid.uuid4().hex, recording_id, analyzer_id, now_iso()),
                )
            EXECUTOR.submit(process_recording, recording_id)
            return self.send_json(detail_recording(recording_id), HTTPStatus.CREATED)
        except ValueError as exc:
            return self.send_error_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, str(exc))
        except Exception as exc:
            return self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))


def resume_pending_jobs() -> None:
    """Resume recordings that were interrupted during processing."""
    interrupted = fetch_all(
        "SELECT id FROM recordings WHERE status IN ('queued', 'normalizing', 'processing') ORDER BY created_at"
    )
    if interrupted:
        print(f"Setze {len(interrupted)} unterbrochene Aufnahmen fort …")
        for row in interrupted:
            EXECUTOR.submit(process_recording, row["id"])


def main() -> None:
    global DATA_DIR, DB_PATH
    parser = argparse.ArgumentParser(description="Lokale Audio Workbench v0.2")
    parser.add_argument("--host", default="127.0.0.1", help="127.0.0.1 für nur diesen Rechner, 0.0.0.0 für das LAN")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    args = parser.parse_args()
    DATA_DIR = args.data_dir.resolve()
    DB_PATH = DATA_DIR / "workbench.sqlite3"
    init_db()
    resume_pending_jobs()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Audio Workbench läuft auf http://{args.host}:{args.port}")
    print(f"Datenordner: {DATA_DIR}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBeendet.")
    finally:
        server.server_close()
        EXECUTOR.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    main()
