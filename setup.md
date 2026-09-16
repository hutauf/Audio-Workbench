# Audio Workbench – Setup-Prompt für einen Agenten

> Kopiere den kompletten Inhalt dieses Dokuments in einen Coding-Agenten, der
> Zugriff auf den Projektordner hat. Der Agent soll die Einrichtung prüfen,
> ausführen und am Ende einen kurzen Testbericht liefern. Dieses Dokument ist
> absichtlich eine plattformneutrale Anleitung und kein auszuführendes Skript.

## Auftrag

Du bist der Setup-Agent für dieses Repository. Richte die Audio Workbench im
aktuellen Projektordner so ein, dass sie auf dem vorhandenen Rechner lokal
startet. Unterstütze Windows (PowerShell oder Eingabeaufforderung), Linux und
macOS. Erkenne Betriebssystem, CPU-Architektur, Python-Version und Shell
selbstständig; verwende niemals Linux-Pfade oder Bash-Befehle auf Windows.

Lies vor jeder Änderung zuerst `README.md`, `requirements.txt`, `server.py`,
`analyzers/` und dieses Dokument. Arbeite nur im Repository und lösche keine
vorhandenen Aufnahmen, Modelle oder Umgebungsdateien.

## Rückfragen und Grenzen

Frage den Menschen nur, wenn eine Entscheidung oder Berechtigung wirklich
notwendig ist:

1. Fehlt FFmpeg und müsste dafür ein Systempaket installiert werden? Frage vor
   einer Installation außerhalb des virtuellen Environments nach. Auf Windows
   kann der Mensch FFmpeg z. B. über `winget` installieren; auf Linux/macOS
   soll der passende Paketmanager verwendet werden.
2. Soll die optionale Sprecher-Diarisierung aktiviert werden? Standardmäßig
   überspringen und die Workbench trotzdem startfähig machen. Wenn sie gewünscht
   ist, verwende die unten genannte öffentliche, für sherpa-onnx konvertierte
   Modellversion. Fordere niemals ein Hugging-Face-Token im Chat an; ein Token
   darf nur über die lokale Credential-Konfiguration des Menschen verwendet
   werden.
3. Frage vor Modelldownloads einmal gebündelt nach Profil und Transkriptsprache:
   „Nur Baseline, sparsam (tiny), ausgewogen (small) oder großes Sprachmodell?
   Deutsch oder automatische Spracherkennung?“ Nenne Download, RAM und den
   CPU-Geschwindigkeitskompromiss aus der Tabelle. Erkenne vorhandenen RAM/CPU
   selbst; frage nicht nach Daten, die du lokal lesen kannst.
4. BirdNET ist ein Spezialmodell für Vögel, nicht für allgemeine Tiergeräusche.
   Biete es separat an; der zusätzliche TensorFlow-Stack kann groß sein.

Ohne Antwort nur die Baseline einrichten; keine optionalen Modelldownloads.
Bei bestätigtem sparsamen Profil Silero + YAMNet + Whisper tiny einrichten,
Diarisierung und BirdNET nur auf ausdrücklichen Wunsch. Es gibt kein Cloud-Fallback.

## Modellwahl passend zum Rechner

Alle Größen sind ungefähr. RAM hier bezeichnet **nur das Whisper-Modell**,
nicht Betriebssystem, Python, Browser und weitere Analyzer. Die Empfehlungen
für Gesamt-RAM sind Planungswerte, keine gemessenen Hardwaregarantien.

| Whisper-Modell (mehrsprachig) | Download | Modell-RAM | Einordnung |
|---|---:|---:|---|
| tiny | 75 MiB | 273 MB | Pi/kleine CPU; sparsam, geringere Genauigkeit |
| base | 142 MiB | 388 MB | etwas größer, optionaler Zwischenschritt |
| small | 466 MiB | 852 MB | Desktop-Kompromiss; ca. 4–8 GB Gesamt-RAM einplanen |
| medium | 1,5 GiB | 2,1 GB | eher kräftiger Rechner mit mindestens 8 GB Gesamt-RAM |
| large-v3 | 2,9 GiB | 3,9 GB | eher 16 GB Gesamt-RAM; CPU-Verarbeitung kann sehr langsam sein |

Quelle: [whisper.cpp – Memory usage](https://github.com/ggml-org/whisper.cpp#memory-usage).
Downloadgröße sagt nichts über Downloadzeit ohne bekannte Bandbreite aus.
Für einen 64-bit Raspberry Pi zunächst tiny, 2 Threads und mindestens 4 GB
Gesamt-RAM als vorsichtiger Ausgangspunkt; kein Echtzeitversprechen und
optional schwere Laufzeiten einzeln prüfen. Baseline funktioniert auch ohne ML.
Auf ARM64 niemals x86-64-Binaries installieren. Für Deutsch keine `.en`-Modelle
verwenden. Größere Whisper-Modelle verbessern nicht die YAMNet-Klassifikation.
Qwen oder andere ASR-Familien sind nicht über den vorhandenen whisper.cpp-Adapter
austauschbar und gehören nicht in diesen Setup-Auftrag.

## Virtuelles Environment

Verwende `.venv` im Repository und immer den Interpreter aus diesem Environment
– keine globalen `pip`-Installationen und keine Aktivierung als Voraussetzung.

- Windows: `.venv\Scripts\python.exe`
- Linux/macOS: `.venv/bin/python`

Falls `.venv` bereits existiert, erhalten lassen und zuerst mit genau diesem
Interpreter weiterarbeiten. Erzeuge das Environment mit Python 3.10 oder neuer.
Wenn mehrere Python-Versionen vorhanden sind, bevorzuge eine Version mit
passenden Wheels (auf Windows/Linux x86-64 typischerweise 3.11–3.13).

Führe sinngemäß aus:

1. `python -m venv .venv`
2. Upgrade von `pip` und `wheel` über den `.venv`-Interpreter.
3. `-r requirements.txt` über den `.venv`-Interpreter installieren.
4. Nur für die gewählten Analyzer zusätzlich installieren:
   `sherpa-onnx` (Silero/Diarisierung), `ai-edge-litert` (YAMNet),
   `birdnetlib`, `soundfile` und `librosa` (BirdNET).
5. Für `birdnetlib` prüfen, ob dessen TFLite-Backend importierbar ist. Falls
   die installierte birdnetlib-Version es benötigt, das passende CPU-
   TensorFlow-Paket für die erkannte Python-/Plattform-Kombination installieren.
   Keine CUDA- oder GPU-Pakete ungefragt installieren.

Wenn ein optionales Paket auf der Plattform kein Wheel besitzt, darf die
Baseline trotzdem fertig eingerichtet werden. Das Ergebnis muss klar nennen,
welcher Analyzer deshalb deaktiviert bleibt.

## Modelle und Binärdateien

Lege die Dateien mit genau diesen relativen Pfaden ab. Lade Dateien robust mit
HTTP-Statusprüfung und einer temporären `.part`-Datei; eine Fehlerseite, ein
401/403-Text oder eine HTML-Datei darf niemals als erfolgreiches Modell liegen
bleiben. Bereits vollständige Dateien nicht erneut laden.

### Automatisch einrichtbare Modelle

- Silero VAD für sherpa-onnx
  - URL: `https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx`
  - Ziel: `models/vad/silero_vad.onnx`
- YAMNet TFLite
  - URL: `https://storage.googleapis.com/mediapipe-models/audio_classifier/yamnet/float32/1/yamnet.tflite`
  - Ziel: `models/yamnet/yamnet.tflite`
- YAMNet-Klassenliste
  - URL: `https://raw.githubusercontent.com/tensorflow/models/master/research/audioset/yamnet/yamnet_class_map.csv`
  - Ziel: `models/yamnet/yamnet_class_map.csv`
- Whisper.cpp, ausschließlich die bestätigte Modellgröße
  - URL-Muster: `https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{modell}.bin`
  - Ziel: `models/asr/ggml-{modell}.bin`
  - `{modell}` durch `tiny`, `base`, `small`, `medium` oder `large-v3` ersetzen.
    Vor dem Download Existenz und Größe der ausgewählten offiziellen Datei prüfen.

Für Whisper ist zusätzlich das passende Programm `whisper-cli` nötig. Lade
für Windows x86-64 ein offizielles `whisper-cli.exe`-Release von
`https://github.com/ggml-org/whisper.cpp/releases` und lege die EXE zusammen mit
allen zugehörigen DLLs in einen lokalen Ordner, z. B.
`tools/whisper/whisper-cli.exe`. Für Linux/macOS verwende ein offizielles
Binary, falls vorhanden, oder kompiliere es nur, wenn eine passende Toolchain
vorhanden ist. Keine Datei mit falschem Betriebssystem als Binary umbenennen.
Prüfe die Binary mit `--help` oder `--version` und setze danach
`AUDIO_WORKBENCH_WHISPER_CLI` auf ihren absoluten Pfad.

BirdNET wird über `birdnetlib` geladen. Prüfe beim ersten Import, ob das von
der Bibliothek benötigte BirdNET-2.4-Modell und der TFLite-Interpreter
funktionieren; es soll nicht fälschlich als „fertig“ gemeldet werden, wenn nur
das Python-Paket installiert wurde.

### Sprecher-Diarisierung: bewusst optional

Nicht die Datei
`https://huggingface.co/pyannote/segmentation-3.0/resolve/main/pytorch_model.bin`
als `model.int8.onnx` speichern. Das ist ein lizenzgeschütztes PyTorch-Modell
und außerdem nicht das Dateiformat, das der vorhandene sherpa-onnx-Adapter
erwartet.

Wenn der Mensch Diarisierung ausdrücklich aktiviert, verwende stattdessen die
öffentlichen sherpa-onnx-Assets:

- Segmentierungspaket:
  `https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2`
  entpacken und `model.onnx` nach
  `models/speakers/segmentation/model.onnx` legen.
- Speaker-Embedding:
  `https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx`
  nach `models/speakers/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx`
  laden.

Nur wenn beide Dateien existieren und `sherpa-onnx` importierbar ist, die beiden
Diarisierungs-Umgebungsvariablen setzen. Sonst die Diarisierung nicht
registrieren und als optional/offen ausweisen.

## Konfiguration und Start

Verwende absolute Pfade, die der jeweilige Prozess versteht. Setze für den
vollständigen Stack:

- `AUDIO_WORKBENCH_SILERO_VAD_MODEL` auf `models/vad/silero_vad.onnx`
- `AUDIO_WORKBENCH_YAMNET_MODEL` auf `models/yamnet/yamnet.tflite`
- `AUDIO_WORKBENCH_YAMNET_CLASS_MAP` auf `models/yamnet/yamnet_class_map.csv`
- `AUDIO_WORKBENCH_WHISPER_MODEL` auf die gewählte `models/asr/ggml-*.bin`
- `AUDIO_WORKBENCH_WHISPER_CLI` auf `whisper-cli` bzw. `whisper-cli.exe`
- `AUDIO_WORKBENCH_BIRDNET=1`, wenn birdnetlib erfolgreich geprüft wurde

Speichere die Whisper-Wahl dauerhaft in `settings.local.json` im Projektordner.
Bestehende Einstellungen erhalten. Beispiel für small unter Windows:

```json
{
  "WHISPER_MODEL": "models/asr/ggml-small.bin",
  "WHISPER_CLI": "tools/whisper/whisper-cli.exe",
  "WHISPER_LANGUAGE": "de",
  "WHISPER_THREADS": 2
}
```

Unter Linux/macOS den tatsächlich geprüften CLI-Pfad ohne `.exe` verwenden.
Sprache `auto` für automatische Erkennung. Relative Pfade gelten ab Projektordner.
Diese vier lokalen Einstellungen haben Vorrang vor Umgebungsvariablen und den
tiny-Defaults der Starthelfer. Die Datei ist git-ignoriert; keine Tokens speichern.
Bei Baseline keinen WHISPER_MODEL-Eintrag erzeugen. Vorhandene ML-Konfiguration
nicht stillschweigend löschen; bei Profilwechsel die Aktivierung bewusst prüfen.

Die Variablen für einen fehlenden Analyzer niemals auf einen nicht existierenden
Pfad setzen. Ohne diese Variablen muss die eingebaute Audio-Profil-, Waveform-
und Energie-VAD-Baseline weiterhin starten können.

Erstelle oder aktualisiere bei Bedarf einen kleinen Starthelfer für das erkannte
Betriebssystem, aber keine plattformfremden Annahmen:

- Windows: PowerShell mit `.venv\Scripts\python.exe server.py`
- Linux/macOS: `.venv/bin/python server.py`

Der Standardhost soll `127.0.0.1` bleiben. Öffne keinen Firewall-Port und ändere
nicht ohne Rückfrage auf `0.0.0.0`.

## Abschlussprüfung

Führe nach der Einrichtung nur im Repository und im `.venv` diese Prüfungen aus:

1. Prüfe, dass Python den Projektcode kompiliert (`python -m compileall`).
2. Importiere die installierten Pakete einzeln und melde fehlende optionale
   Pakete verständlich.
3. Prüfe Größe und Inhalt aller vorhandenen Modelldateien; die YAMNet-CSV muss
   521 Klassen enthalten und die Whisper-Datei darf keine kleine HTML-
   Fehlerdatei sein.
4. Prüfe `whisper-cli(.exe) --version` oder `--help`, sofern vorhanden.
5. Führe `python -m unittest discover -s tests -v` aus. Starte den Server kurz
   auf einem freien lokalen Port und rufe `/api/analyzers` und
   `/api/recordings?limit=50` ab. Prüfe SQLite-FTS5-Unterstützung. Ein registrierter
   Analyzer mit Status `ready` bedeutet nur „konfiguriert“, nicht erfolgreich
   getestet: prüfe jeden gewählten Adapter mit einer kurzen synthetischen
   Mono-WAV. Berichte Jobfehler, insbesondere fehlende Laufzeiten, und deaktiviere
   defekte optionale Konfiguration erst nach transparenter Erklärung. Server beenden.
6. Hinterlasse keine `.part`-Dateien, keine temporären Archive und keine
   Zugangsdaten im Repository.

Berichte am Ende in wenigen Zeilen:

- Betriebssystem, Architektur und Python-Version
- verwendeter `.venv`-Interpreter
- FFmpeg gefunden: ja/nein
- Baseline funktioniert: ja/nein
- Silero, YAMNet, Whisper und BirdNET: bereit/deaktiviert mit Grund
- gewähltes Whisper-Modell, Sprache, Threads, Downloadumfang und lokaler Konfigurationspfad
- Diarisierung: bewusst übersprungen oder vollständig eingerichtet
- konkreter Startbefehl für dieses Betriebssystem
- verbleibende manuelle Schritte
