# Audio Workbench v0.2

Ein bewusst kleiner, lokaler Prototyp für die geplante Audio-Analyse-Workbench.
Die Aufnahme kommt zunächst immer von außen, etwa vom Diktiergerät, Smartphone,
Google Recorder oder einer Smartwatch. Die App enthält keinen Recorder.

## Installation

### Automatisches Setup (empfohlen)

```bash
./setup.sh
```

Das Skript lädt automatisch:
- Silero VAD Model
- YAMNet Sound Classification
- Whisper.cpp tiny Model
- Whisper.cpp Binary (für ARM64 wird es kompiliert)

**Hinweis:** Pyannote Diarization-Models erfordern manuellen Download von HuggingFace aufgrund der Lizenz.

### Manuelle Installation

Voraussetzungen:

- Python 3.10 oder neuer
- FFmpeg im `PATH`

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
# Models in models/ ablegen (siehe setup.sh für URLs)
```

## Start

Mit allen Analyzern (empfohlen):

```bash
./start.sh --host 0.0.0.0
```

Oder manuell ohne ML-Modelle:

```bash
python3 server.py --host 0.0.0.0
```

Danach im Browser `http://127.0.0.1:8787` öffnen.

Der Default ist absichtlich `127.0.0.1`. Bei `0.0.0.0` sollte der Port nur im
vertrauenswürdigen Heimnetz erreichbar sein. Es gibt im Prototyp noch keine
Benutzerverwaltung und keine Verschlüsselung.

## Was der Prototyp bereits zeigt

1. Import beliebiger FFmpeg-kompatibler Audioformate
2. Unverändertes Original und separate normalisierte Arbeitskopie
3. SQLite-Metadaten und lokale Dateiablage unter `data/recordings/<id>/`
4. In-Process-Job-Queue mit drei Baseline-Analyzern und optionalen ML-Plugins
5. Audio-Profil mit Dauer, RMS, Peak und Crest Factor
6. Waveform, Spektrogramm und klickbare Audio-Timeline
7. Energie-basierte Sprachaktivität als austauschbare VAD-Baseline
8. Analyse-Kacheln für spätere Module: Transkript, Sprecher, Sound Events,
   Ort/Szene, BirdNET sowie Spatial Audio
9. gekapselte Adapter für Silero VAD, sherpa-Diarisierung, YAMNet und whisper.cpp

## Architekturentscheidung

Die Audioaufnahme ist das Primärobjekt. Ein Analyzer bekommt den Pfad zur
normalisierten Arbeitskopie und schreibt ein JSON-Ergebnis. Die UI kennt die
konkreten Berechnungen nur über `analyzer_id`, Status und Payload.

Die drei Baseline-Analyzer sitzen zunächst in `server.py` in `ANALYZERS`:

```python
"vad_energy": {
    "name": "Sprachaktivität",
    "run": vad_energy,
}
```

Ein späterer Analyzer kann dieselbe Schnittstelle verwenden:

```python
def run_new_analyzer(normalized_path: Path) -> dict:
    return {
        "type": "timeline",
        "events": [
            {"start": 12.4, "end": 15.8, "label": "bird", "confidence": 0.91}
        ],
    }
```

Die optionalen ML-Adapter liegen in `analyzers/` und werden erst durch einen
lokalen Modellpfad aktiviert. Ohne diesen Pfad startet die Workbench weiter
ohne ML-Abhängigkeiten:

```bash
export AUDIO_WORKBENCH_SILERO_VAD_MODEL="$PWD/models/vad/silero_vad.int8.onnx"
export AUDIO_WORKBENCH_YAMNET_MODEL="$PWD/models/yamnet/yamnet.tflite"
export AUDIO_WORKBENCH_YAMNET_CLASS_MAP="$PWD/models/yamnet/yamnet_label_list.txt"
export AUDIO_WORKBENCH_WHISPER_MODEL="$PWD/models/asr/ggml-tiny.bin"
python3 server.py
```

Für die eigentliche ASR-/Sprecher-Iteration beschreibt
`models/README.md` die Kandidaten und die Reihenfolge. Der entscheidende
Vertrag bleibt gleich: normalisierte WAV hinein, JSON mit Zeitsegmenten,
Labels und Metadaten heraus. Ein Modul kann damit später ohne Umbau der UI in
einen eigenen Worker oder Container verschoben werden.
