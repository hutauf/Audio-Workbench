# Audio Workbench v0.2

Ein bewusst kleiner, lokaler Prototyp für die geplante Audio-Analyse-Workbench.
Die Aufnahme kommt zunächst immer von außen, etwa vom Diktiergerät, Smartphone,
Google Recorder oder einer Smartwatch. Die App enthält keinen Recorder.

## Installation

### Agenten-Setup (empfohlen)

Die Einrichtung ist absichtlich kein Shell-Skript mehr. Lies
[setup.md](setup.md) und kopiere den kompletten Inhalt in einen Coding-Agenten,
der Zugriff auf den Projektordner hat. Der Agent erkennt Windows, Linux oder
macOS, legt ein `.venv` an, installiert die Python-Abhängigkeiten, lädt die
öffentlichen Modelle und prüft das Ergebnis. Er verwendet dabei den passenden
Python-/Pfadstil des Betriebssystems.

Der Setup-Prompt fragt nach einem passenden lokalen CPU-Profil. Ohne Antwort
bleibt es bei der Baseline ohne Modelldownloads. Nach deiner Wahl richtet er ein:

- Silero VAD, YAMNet und Whisper.cpp in der gewünschten Größe (tiny bis large-v3)
- die passenden optionalen Python-Laufzeiten
- optional BirdNET, sofern gewünscht und auf der Plattform verfügbar
- eine passende `whisper-cli`-Binary für das Betriebssystem

Die Sprecher-Diarisierung bleibt standardmäßig deaktiviert. Sie benötigt zwei
zusätzliche ONNX-Dateien; der direkte Hugging-Face-Download von
`pyannote/segmentation-3.0/pytorch_model.bin` ist dafür weder freigeschaltet
noch das passende Format für den vorhandenen sherpa-onnx-Adapter. Der Agent
kann die öffentliche, konvertierte sherpa-onnx-Variante einrichten, wenn du das
ausdrücklich möchtest.

`setup.sh` wird nicht benötigt und ist bewusst entfernt: Die frühere Datei war
Linux-/Bash-spezifisch, installierte nicht alle ML-Laufzeiten und meldete
teilweise fehlerhafte Modelldownloads als erfolgreich.

### Manuelle Installation

Voraussetzungen:

- Python 3.10 oder neuer
- FFmpeg im `PATH`

Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip wheel
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe server.py
```

Linux/macOS:

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip wheel
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python server.py
```

Die manuelle Minimalinstallation startet die Baseline. Für die optionalen
Analyzer müssen zusätzlich die in [setup.md](setup.md) beschriebenen
Laufzeiten, Modelle und Umgebungsvariablen vorhanden sein.

## Start

Die mitgelieferten Starthelfer erkennen vorhandene optionale Modelle und
registrieren keinen Analyzer für Dateien, die fehlen.

Windows (PowerShell):

```powershell
.\start.ps1
```

Linux/macOS:

```bash
./start.sh
```

Ohne Starthelfer kann der Server direkt über den jeweiligen `.venv`-Interpreter
gestartet werden; dann werden optionale Modelle nur über zuvor gesetzte
Umgebungsvariablen oder die lokale Whisper-Konfiguration aktiviert.
Die Modellwahl, Sprache und CPU-Threads werden in `settings.local.json`
gespeichert; Format und Speicherhinweise stehen in [setup.md](setup.md).

Danach im Browser `http://127.0.0.1:8787` öffnen. Der Default ist absichtlich
`127.0.0.1`. Bei `0.0.0.0` sollte der Port nur im vertrauenswürdigen Heimnetz
erreichbar sein. Es gibt im Prototyp noch keine Benutzerverwaltung und keine
Verschlüsselung.

## Was der Prototyp bereits zeigt

1. Import beliebiger FFmpeg-kompatibler Audioformate
2. Unverändertes Original und separate normalisierte Arbeitskopie
3. SQLite-Metadaten und lokale Dateiablage unter `data/recordings/<id>/`
4. Persistente SQLite-Queue mit einem lokalen Worker, Neustart-Wiederaufnahme und optionalen ML-Plugins
5. Audio-Profil mit Dauer, RMS, Peak und Crest Factor
6. Waveform/Spektrogramm über die ganze Aufnahme plus verschiebbare 5–60-Sekunden-Detailfenster
7. Energie-basierte Sprachaktivität als austauschbare VAD-Baseline
8. Mono-Analyse mit Transkript, Sprechern, Sound Events und optional BirdNET; keine räumliche Mehrkanalanalyse
9. gekapselte Adapter für Silero VAD, sherpa-Diarisierung, YAMNet und whisper.cpp
10. Bibliothek mit 50 Aufnahmen pro Seite, Mehrfachimport, Tags, Datums-/Statusfiltern und Sortierung
11. Lokale Volltextsuche in Dateinamen, Transkripten, Tags und Geräuschklassen mit Zeitstempel-Treffern

## Große Sammlungen und lange Aufnahmen

Beispiel: links „Katze“ eingeben, Quelle „Geräusche / Tierstimmen“ wählen und
einen Treffer anklicken. Die Suche berücksichtigt auch YAMNet-Labels Cat,
Meow und Purr. Voraussetzung sind vorhandene Analyseergebnisse; die Suche
selbst benötigt kein Modell. Transkriptwörter und Dateinamen funktionieren
ebenso. Tags werden rechts an der geöffneten Aufnahme vergeben.

Statt 10.000 Aufnahmen auf einmal zu laden, liefert der Server nur die aktuelle
Seite. Lange Audios lassen sich über Übersicht, Zeitfenster und Abspielposition
navigieren. Die ML-Eingabe wird für Whisper, YAMNet, Silero und BirdNET in
5-Minuten-Abschnitte aufgeteilt. Alle aktuellen Arbeitskopien sind Mono/16 kHz;
Originale bleiben erhalten. Mehrkanalige Eingaben werden heruntergemischt.

Die Suche ist **noch keine semantische Embedding-Suche**. Ebenfalls offen:
Streaming-Uploads über 512 MiB, segmentweise Ergebnis-APIs für extrem lange
Transkripte und separate höher abgetastete Mono-Kopien für Vogelstimmen.
Die ausführliche Begründung, Modellgrenzen und nächsten Schritte stehen in
[DESIGN.md](DESIGN.md).

Tests ohne Setup-Ausführung oder Modelldownload:

```text
python -m unittest discover -s tests -v
```

Im eingerichteten Projekt dafür den jeweiligen `.venv`-Interpreter verwenden.

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
ohne ML-Abhängigkeiten. Die vollständige Konfiguration beschreibt
[setup.md](setup.md); insbesondere müssen fehlende Modelle nicht per
Umgebungsvariable vorgetäuscht werden.

Der entscheidende Vertrag bleibt gleich: normalisierte WAV hinein, JSON mit
Zeitsegmenten, Labels und Metadaten heraus. Ein Modul kann damit später ohne
Umbau der UI in einen eigenen Worker oder Container verschoben werden.
