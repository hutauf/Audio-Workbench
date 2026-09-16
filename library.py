"""SQLite library and segment search. No model or network required."""
import json
import re
from datetime import date


ALIASES = {
    "cat": "Katze Katzen Katzengeräusche", "meow": "Miauen Katze Katzen Katzengeräusche",
    "purr": "Schnurren Katze Katzen Katzengeräusche", "dog": "Hund Hunde Hundegeräusche",
    "bark": "Bellen Hund Hunde", "bird": "Vogel Vögel Vogelstimmen",
    "bird vocalization, bird call, bird song": "Vogel Vögel Vogelstimmen Zwitschern",
    "speech": "Sprache Sprechen Gespräch", "music": "Musik", "rain": "Regen",
    "wind": "Wind", "engine": "Motor Motorgeräusch", "water": "Wasser",
}


def index_result(conn, recording_id, analyzer_id, payload):
    conn.execute("DELETE FROM search_documents WHERE recording_id=? AND source=?", (recording_id, analyzer_id))
    rows = payload.get("segments") if payload.get("type") == "transcript" else payload.get("events")
    entries = []
    for row in rows or []:
        text = str(row.get("text") or row.get("label") or "").strip()
        if not text:
            continue
        start = max(0, float(row.get("start", 0)))
        end = max(start, float(row.get("end", start)))
        aliases = ALIASES.get(text.casefold(), "") if analyzer_id in ("yamnet", "birdnet") else ""
        entries.append((recording_id, analyzer_id, start, end, row.get("confidence"), text, aliases))
    if not entries and payload.get("text"):
        entries.append((recording_id, analyzer_id, 0, 0, None, str(payload["text"]), ""))
    conn.executemany("INSERT INTO search_documents(recording_id,source,start,end,confidence,text,aliases) VALUES(?,?,?,?,?,?,?)", entries)


def initialize(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(recordings)")}
    if "file_modified_at" not in columns:
        conn.execute("ALTER TABLE recordings ADD COLUMN file_modified_at TEXT")
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS recordings_created ON recordings(created_at, id);
        CREATE INDEX IF NOT EXISTS recordings_name ON recordings(original_name COLLATE NOCASE, id);
        CREATE INDEX IF NOT EXISTS recordings_duration ON recordings(duration, id);
        CREATE INDEX IF NOT EXISTS recordings_modified ON recordings(file_modified_at, id);
        CREATE INDEX IF NOT EXISTS recordings_status ON recordings(status, created_at);
        CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, recording_id);
        CREATE TABLE IF NOT EXISTS recording_tags (
            recording_id TEXT NOT NULL, tag TEXT NOT NULL,
            PRIMARY KEY(recording_id, tag)
        );
        CREATE INDEX IF NOT EXISTS tags_name ON recording_tags(tag, recording_id);
        CREATE TABLE IF NOT EXISTS library_meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS search_documents (
            id INTEGER PRIMARY KEY, recording_id TEXT NOT NULL, source TEXT NOT NULL,
            start REAL, end REAL, confidence REAL, text TEXT NOT NULL, aliases TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS documents_recording ON search_documents(recording_id, source);
        CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
            text, aliases, content='search_documents', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
        );
        CREATE TRIGGER IF NOT EXISTS documents_insert AFTER INSERT ON search_documents BEGIN
            INSERT INTO search_fts(rowid,text,aliases) VALUES(new.id,new.text,new.aliases);
        END;
        CREATE TRIGGER IF NOT EXISTS documents_delete AFTER DELETE ON search_documents BEGIN
            INSERT INTO search_fts(search_fts,rowid,text,aliases) VALUES('delete',old.id,old.text,old.aliases);
        END;
        CREATE TRIGGER IF NOT EXISTS recording_search_insert AFTER INSERT ON recordings BEGIN
            INSERT INTO search_documents(recording_id,source,text) VALUES(new.id,'filename',new.original_name);
        END;
    """)
    if not conn.execute("SELECT 1 FROM library_meta WHERE key='index-v1'").fetchone():
        conn.execute("DELETE FROM search_documents")
        conn.execute("INSERT INTO search_documents(recording_id,source,text) SELECT id,'filename',original_name FROM recordings")
        conn.execute("INSERT INTO search_documents(recording_id,source,text) SELECT recording_id,'tags',group_concat(tag,' ') FROM recording_tags GROUP BY recording_id")
        # Stream old payloads one at a time; preserve the original results.
        for row in conn.execute("SELECT recording_id,analyzer_id,payload_json FROM results"):
            try:
                index_result(conn, row[0], row[1], json.loads(row[2]))
            except (ValueError, TypeError):
                continue
        conn.execute("INSERT INTO library_meta VALUES('index-v1','1')")


def query_options(query):
    def get(key, default=""):
        return query.get(key, [default])[0]
    q = get("q").strip()[:300]
    tokens = re.findall(r"\w+", q, re.UNICODE)[:20]
    match = " AND ".join('"' + token + '"*' for token in tokens)
    conditions, args = [], []
    if q and not tokens:
        conditions.append("0")
    source = get("source")
    sources = {"filename": ["filename"], "transcript": ["whisper_cpp"], "sound": ["yamnet", "birdnet"], "tags": ["tags"]}
    doc_conditions, doc_args = [], []
    if source in sources:
        selected = sources[source]
        doc_conditions.append("d.source IN (" + ",".join("?" for _ in selected) + ")")
        doc_args.extend(selected)
    confidence = float(get("confidence", "0"))
    if not 0 <= confidence <= 1:
        raise ValueError("Konfidenz muss zwischen 0 und 1 liegen.")
    if confidence:
        doc_conditions.append("d.confidence >= ?")
        doc_args.append(confidence)
    doc_filter = (" AND " + " AND ".join(doc_conditions)) if doc_conditions else ""
    if match:
        conditions.append("r.id IN (SELECT d.recording_id FROM search_fts JOIN search_documents d ON d.id=search_fts.rowid WHERE search_fts MATCH ?" + doc_filter + ")")
        args.extend([match, *doc_args])
    elif doc_conditions:
        conditions.append("r.id IN (SELECT d.recording_id FROM search_documents d WHERE 1=1" + doc_filter + ")")
        args.extend(doc_args)
    tag = get("tag").strip().casefold()
    if tag:
        conditions.append("r.id IN (SELECT recording_id FROM recording_tags WHERE tag=?)")
        args.append(tag)
    status = get("status")
    if status:
        if status not in {"queued", "normalizing", "processing", "ready", "partial", "failed"}:
            raise ValueError("Unbekannter Status.")
        conditions.append("r.status=?")
        args.append(status)
    for key, operator, suffix in [("after", ">=", "T00:00:00"), ("before", "<=", "T23:59:59.999999Z")]:
        if get(key):
            day = date.fromisoformat(get(key)).isoformat()
            conditions.append("r.created_at " + operator + " ?")
            args.append(day + suffix)
    sort = {"newest": "r.created_at DESC", "oldest": "r.created_at ASC", "name": "r.original_name COLLATE NOCASE ASC",
            "longest": "r.duration DESC", "shortest": "r.duration ASC", "modified": "r.file_modified_at DESC"}.get(get("sort"), "r.created_at DESC")
    return {"where": " AND ".join(conditions) or "1=1", "args": args, "sort": sort,
            "limit": max(1, min(100, int(get("limit", "50")))), "offset": max(0, int(get("offset", "0"))),
            "match": match, "doc_filter": doc_filter, "doc_args": doc_args}


def list_recordings(conn, query):
    options = query_options(query)
    where, args = options["where"], options["args"]
    total = conn.execute("SELECT count(*) FROM recordings r WHERE " + where, args).fetchone()[0]
    limit = options["limit"]
    offset = min(options["offset"], max(0, (total - 1) // limit * limit))
    rows = conn.execute("SELECT r.id,r.original_name,r.duration,r.created_at,r.file_modified_at,r.status,r.size_bytes FROM recordings r WHERE " + where + " ORDER BY " + options["sort"] + ",r.id LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
    items = [dict(row) for row in rows]
    ids = [row["id"] for row in items]
    if ids:
        placeholders = ",".join("?" for _ in ids)
        tags = {}
        for row in conn.execute("SELECT recording_id,tag FROM recording_tags WHERE recording_id IN (" + placeholders + ") ORDER BY tag", ids):
            tags.setdefault(row[0], []).append(row[1])
        matches = {}
        if options["match"]:
            sql = """SELECT * FROM (SELECT d.recording_id,d.source,d.start,d.end,d.confidence,
                substr(d.text,1,240) AS text,
                row_number() OVER(PARTITION BY d.recording_id ORDER BY d.start,d.id) AS rn
                FROM search_fts JOIN search_documents d ON d.id=search_fts.rowid
                WHERE search_fts MATCH ? AND d.recording_id IN (""" + placeholders + ")" + options["doc_filter"] + ") WHERE rn<=3"
            for row in conn.execute(sql, [options["match"], *ids, *options["doc_args"]]):
                matches.setdefault(row["recording_id"], []).append(dict(row))
        for item in items:
            item["tags"] = tags.get(item["id"], [])
            item["matches"] = matches.get(item["id"], [])
    pending = conn.execute("SELECT count(*) FROM recordings WHERE status IN ('queued','normalizing','processing')").fetchone()[0]
    return {"items": items, "total": total, "offset": offset, "limit": limit, "pending": pending}


def set_tags(conn, recording_id, tags):
    if not isinstance(tags, list) or len(tags) > 30 or any(not isinstance(t, str) or len(t) > 60 for t in tags):
        raise ValueError("Maximal 30 Tags mit jeweils 60 Zeichen.")
    normalized = sorted({t.strip().casefold() for t in tags if t.strip()})
    conn.execute("DELETE FROM recording_tags WHERE recording_id=?", (recording_id,))
    conn.executemany("INSERT INTO recording_tags VALUES(?,?)", [(recording_id, t) for t in normalized])
    conn.execute("DELETE FROM search_documents WHERE recording_id=? AND source='tags'", (recording_id,))
    conn.execute("INSERT INTO search_documents(recording_id,source,text) VALUES(?,'tags',?)", (recording_id, " ".join(normalized)))
    return normalized
