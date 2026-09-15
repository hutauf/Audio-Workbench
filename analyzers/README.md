# Analyzer-Module

Jedes optionale ML-Modul nimmt eine normalisierte `16 kHz / mono / 16-bit
PCM-WAV` entgegen und gibt ein JSON-kompatibles Objekt zurück. Es darf seine
schweren Abhängigkeiten erst in `analyze()` laden. Dadurch bleibt die
Workbench ohne Modellinstallation startfähig und ein Worker kann ein Modul
später in einem eigenen Prozess oder Container ausführen.

Enthaltene Adapter:

- `sherpa_vad.py`: Silero VAD über sherpa-onnx. Aktivierung über
  `AUDIO_WORKBENCH_SILERO_VAD_MODEL`.
- `yamnet.py`: lokale YAMNet-Installation über TFLite/LiteRT oder alternativ
  TensorFlow Hub. Aktivierung über `AUDIO_WORKBENCH_YAMNET_MODEL` und
  optional `AUDIO_WORKBENCH_YAMNET_CLASS_MAP`.
- `whisper_cpp.py`: Prozessadapter für `whisper-cli` mit JSON-Ergebnis.
  Aktivierung über `AUDIO_WORKBENCH_WHISPER_MODEL`, optional ergänzt um
  `AUDIO_WORKBENCH_WHISPER_CLI` und `AUDIO_WORKBENCH_WHISPER_LANGUAGE`.
- `sherpa_diarization.py`: Offline-Diarisierung mit einer
  Segmentierungs- und einer Embedding-Datei. Aktivierung über
  `AUDIO_WORKBENCH_DIARIZATION_SEGMENTATION_MODEL` und
  `AUDIO_WORKBENCH_DIARIZATION_EMBEDDING_MODEL`.

Die Adapter sind absichtlich noch nicht Bestandteil der Standard-Queue. Ohne
die passenden Modell-Dateien und runtimes wäre jeder Import ein vorhersehbarer
Fehler. Sobald die Umgebungsvariable gesetzt ist, nimmt `server.py` den
Adapter automatisch in die lokale, sequentielle Queue auf.

Ein neues Modul braucht im Kern nur:

```python
from pathlib import Path
from typing import Any

def analyze(normalized_path: Path) -> dict[str, Any]:
    return {
        "type": "timeline",
        "events": [],
    }
```

`analyzer_id`, Modellversion, Laufzeit und Worker-ID sollten bei der nächsten
Iteration zusätzlich als Metadaten gespeichert werden.
