# Lokale Audio-Bibliothek: Konzept und Grenzen

## Entscheidung

SQLite, lokale Dateien und ein sequenzieller Worker sind für eine persönliche
Sammlung mit 10.000 Aufnahmen ein sinnvoller Ausgangspunkt. Die Dateianzahl
darf nicht die Anzahl geladener DOM-Elemente oder Python-Jobs bestimmen.
Analysezeit und Speicherbedarf hängen dagegen von der gesamten Audiodauer ab.
„Beliebig viele, beliebig lange Dateien“ ist keine realistische Garantie.

Das Original bleibt unverändert. Alle aktuellen Analyzer arbeiten mit einer
**Mono-Arbeitskopie mit 16 kHz / 16-bit PCM**. Mehrkanalige Eingaben werden von
FFmpeg heruntergemischt; getrennte Kanäle, räumliche Analyse und Beamforming
sind ausdrücklich nicht Teil dieser Version. Es gibt kein Cloud-Fallback.
Pakete/Modelle müssen einmal eingerichtet und für Offline-Betrieb verfügbar sein.

## 10.000 Aufnahmen wiederfinden

Die Bibliothek liefert serverseitig 50 Einträge je Seite (API maximal 100),
ohne Ergebnisse und Jobs jeder Datei mitzuladen. SQL-Indizes unterstützen
Importdatum, Dateiname, Dauer, Status und Dateiänderungsdatum. Seiten haben
eine stabile ID als zweites Sortierkriterium; bei gleichzeitigem Import können
sich Offset-Seiten verschieben, sie sind kein eingefrorener Snapshot.

Tags statt fester Ordner: dieselbe Aufnahme kann „garten“, „tiere“ und
„urlaub“ tragen. Originalpfade bleiben unabhängig von dieser Organisation.
Es gibt bis zu 30 manuelle Tags je Aufnahme, einen exakten Tagfilter und
Vorschläge aus den ersten 500 alphabetischen Tags der Sammlung.

Lokale SQLite-FTS5-Suche indiziert Dateinamen, Transkriptsegmente,
YAMNet-/BirdNET-Ereignisse und Tags. Wörter werden als UND-verknüpfte
Präfixe gesucht; keine Suchsyntax oder externe API nötig.
Treffer enthalten bis zu drei Textstellen mit Zeitposition pro Aufnahme.
Beispiel: Quelle „Geräusche / Tierstimmen“, „Katze“, mindestens 50 %
findet indizierte Cat-/Meow-/Purr-Ereignisse mit entsprechender Konfidenz.
Nur einige häufige Geräuschklassen haben deutsche Suchsynonyme; ansonsten
gelten die Modell-Labels. Modell-Scores sind keine kalibrierten Wahrscheinlichkeiten.

Importzeit und Dateiänderungszeit sind getrennt. Die Dateizeit stammt aus
dem Browser-Upload und ist **kein verifiziertes Aufnahmedatum**. Importfilter
verwenden UTC-Tage. Ältere Aufnahmen ohne Dateizeit behalten einen leeren Wert.

Vorhandene Ergebnisse werden beim ersten Start einmal lokal nachindiziert.
Das kann bei vielen langen Transkripten dauern. Ergebnisse und Originale
bleiben erhalten; vor Versionswechseln empfiehlt sich eine Sicherung von data/.
Neue Analyseergebnisse und Suchindex werden gemeinsam in einer Transaktion
ersetzt. Während eines erneuten Jobs bleibt das bisherige Ergebnis durchsuchbar.

## Große Bibliothek ist nicht gleich hohe Analysegeschwindigkeit

Mehrfach-Uploads laufen im Browser nacheinander. Die Datenbank hält die Queue;
ein einzelner Worker nimmt jeweils eine Aufnahme. Es entstehen keine 10.000
wartenden Python-Futures. Import und Rerun veröffentlichen ihre Jobs atomar.
Unterbrochene Jobs werden beim Neustart wieder eingereiht. Fertige Ergebnisse
müssen nicht bei jedem Öffnen neu berechnet werden.

Ein Pi kann eine Bibliothek verwalten, obwohl die Erstverarbeitung sehr lange
dauert. Nur ein Serverprozess darf denselben Datenordner bearbeiten; verteilte
Worker, Mehrbenutzerbetrieb, Priorisierung und Abbruch laufender Jobs sind
nicht implementiert. Der HTTP-Dienst ist nur für einen vertrauenswürdigen
lokalen Rechner gedacht, ohne Authentifizierung.

## Kurze und lange Aufnahmen

- Profil und Waveform lesen PCM in begrenzten Blöcken.
- Die Übersicht hat höchstens 1.200 Waveform-Bins und 800 Spektralspalten
  über die **gesamte** Aufnahme, nicht nur die ersten Sekunden.
- Für Details gibt es 5/15/30/60-Sekunden-Fenster mit Schieberegler,
  Vor/Zurück und Sprung zur Abspielposition. Die API berechnet nur das
  gewählte Fenster. Eine ganze Stunde wird nicht auf ein Detailbild gequetscht.
- Das Übersichtsspektrogramm ist zeitlich abgetastet und kann kurze Ereignisse
  zwischen Spektralfenstern übersehen. Auch längere Detailfenster sind
  abgetastet; zum Prüfen kurzer Ereignisse weiter hineinzoomen.
- Audio wird per HTTP-Byte-Range abgespielt; der Player verwendet
  preload=metadata. Ein Seek erfordert keinen vollständigen Dateidownload.
- Whisper, YAMNet, Silero und BirdNET erhalten bei langen Dateien
  5-Minuten-Blöcke mit 2 Sekunden Kontext an jeder Grenze.
  Zeitstempel werden auf die gesamte Aufnahme zurückgerechnet; Ereignisse
  werden auf die Kernfenster begrenzt, Transkriptsegmente per Mittelpunkt
  einem Fenster zugeordnet. An Grenzen bleibt Erkennungsunsicherheit.
- Diarisierung wird bewusst nicht unabhängig pro Block ausgeführt:
  SPEAKER_00 hätte sonst in jedem Block eine andere Identität. Für sehr lange
  Dateien ist diese optionale, nicht speicherbegrenzte Analyse auszuschalten.
- Leere/kurze PCM-Dateien bringen die Baseline nicht zum Absturz. Das ist
  keine Garantie sinnvoller ML-Ergebnisse: YAMNet benötigt ungefähr
  0,96 Sekunden Kontext; Padding erzeugt keine fehlende Information.
  Whisper kann bei Geräuschen oder Stille Text halluzinieren. Die Energie-VAD
  ist kein zuverlässiger Sprachdetektor.

## Modelle: vorhandene Adapter zuerst

Silero für Sprachaktivität, Whisper für Sprache und YAMNet mit 521 AudioSet-
Klassen decken verschiedene Aufgaben ab. YAMNet ist ein allgemeiner
Klassifikator, keine Erkennung aller denkbaren Geräusche. BirdNET ist eine
optionale Spezialisierung für Vogelarten. Es braucht kein weiteres großes
Sprachmodell, nur um nach bereits erkannten Katzen zu suchen.

Das Setup fragt nach Hardwareprofil, Whisper-Größe und Sprache; tiny/base
für sparsame Geräte, small als möglicher Desktop-Kompromiss, medium/large-v3
nur nach bewusster Wahl. Größen und Speicherhinweise stehen in setup.md.
settings.local.json speichert die Wahl betriebssystemübergreifend.
Qwen-ASR wäre eine eigene Integration mit eigenen Laufzeit-/RAM-Anforderungen,
kein einfaches Austauschen einer whisper.cpp-Modelldatei.

Wichtige Qualitätsgrenze: 16 kHz entfernt Frequenzen oberhalb 8 kHz.
Das genügt dem YAMNet-/Whisper-Eingabevertrag, ist aber für manche Tierstimmen
ungeeignet. BirdNET kann die verlorenen Frequenzen nicht durch Hochsamplen
zurückholen. Eine spätere 48-kHz-Mono-Arbeitskopie speziell für BirdNET ist
sinnvoll; die unveränderten Originale ermöglichen diese Erweiterung.

## Noch keine semantische Suche

Die eingebaute Suche ist Volltext plus explizite Synonyme, nicht Embedding-
Suche. „Ein Tier miaut neben einer Straße“ wird nicht automatisch als
semantische Audioanfrage verstanden. Nächster separater Ausbauschritt wäre
ein optionaler lokaler, mehrsprachiger Text-Embedding-Index für Transkript-
und Labelsegmente mit Zeitstempeln. Er findet keine Geräusche, die nie
klassifiziert oder beschrieben wurden. Dafür wäre ein Text-Audio-Modell
mit eigenen Audio-Embeddings erforderlich, inklusive Qualitäts- und
Speichertests. Auf dem Pi bleibt Volltext die kostengünstige Basis.

## Verbleibende Skalierungsgrenzen

- Uploads sind auf 512 MiB je Multipart-Anfrage begrenzt und werden noch im
  RAM gepuffert. 2-GB-Dateien/Streaming-Import sind ausdrücklich nicht gelöst.
- Resultate einer Aufnahme sind weiterhin vollständige JSON-Payloads.
  Sehr viele Ereignisse/Transkriptsegmente können Detailansicht, Suchindex-
  Aufbau und Antwortgröße belasten; segmentweise Ergebnis-APIs wären der
  nächste Schritt. Chunking begrenzt die PCM-Eingabe, nicht alle Ergebnisdaten.
- Waveform-Übersichten müssen einmal die ganze Aufnahme lesen.
  Ihre Berechnung ist linear in der Audiodauer. Anzeigegröße ist begrenzt.
- Gleichzeitige große Uploads mehrerer Clients, RAM-Spitzen optionaler
  ML-Laufzeiten und freier Plattenplatz sind nicht automatisch budgetiert.
- SQLite-FTS5 muss im Python-Build verfügbar sein. Der Test prüft dies durch
  Anlegen der echten Datenbank. Bestehende Modelle werden nicht beim Appstart
  heruntergeladen; „ready“ in der Analyzer-Liste bezeichnet Konfiguration,
  keinen bestandenen Inferenztest.

## Verifikation

python -m unittest discover -s tests -v verwendet temporäre Datenbanken und
synthetische Mono-Aufnahmen, keine Modelldownloads. Der 10.000-Einträge-Test
prüft begrenzte Antwortgröße und konstante SQL-Abfragezahl. Das ist ein
Metadaten-Benchmark, kein Pi-Test und kein Test von 10.000 echten Analysen.
Zusätzlich werden Zeitfenster, Chunk-Zeitstempel, Suchindex-Migration,
Tags, Byte-Ranges und HTTP-Abläufe geprüft.

## Technische Quellen

- [SQLite FTS5](https://sqlite.org/fts5.html): lokaler Volltextindex.
- [YAMNet](https://www.tensorflow.org/hub/tutorials/yamnet):
  Klassen und Audioeingabevertrag.
- [whisper.cpp](https://github.com/ggml-org/whisper.cpp):
  Modellgrößen, Speicherbedarf und Plattformen.
