"""SQLite implementation of Store. data/skillpulse.db is the source of truth."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from skillpulse.models import LevelResult, RawPosting
from skillpulse.util.text import description_hash, normalise_url

SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    url_norm         TEXT NOT NULL UNIQUE,
    desc_hash        TEXT NOT NULL,
    source           TEXT NOT NULL,
    url              TEXT NOT NULL,
    title            TEXT NOT NULL,
    company          TEXT,
    location         TEXT,
    city             TEXT,
    work_mode        TEXT NOT NULL DEFAULT 'unknown',
    employment_type  TEXT NOT NULL DEFAULT 'unknown',
    posted_date      TEXT,
    description_text TEXT,
    search_title     TEXT,
    role_family      TEXT,
    level            TEXT,
    level_confidence TEXT,
    years_min        REAL,
    years_max        REAL,
    level_signals    TEXT,              -- JSON list
    level_ambiguous  INTEGER DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'new',
        -- new | classified | extracted | skipped_level | skipped_employment | skipped_budget | extract_failed
    first_seen       TEXT NOT NULL,     -- YYYY-MM-DD
    last_seen        TEXT NOT NULL,
    times_seen       INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_postings_hash ON postings(desc_hash);
CREATE INDEX IF NOT EXISTS ix_postings_first_seen ON postings(first_seen);

CREATE TABLE IF NOT EXISTS extractions (
    posting_id    INTEGER PRIMARY KEY REFERENCES postings(id),
    desc_hash     TEXT NOT NULL,
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    data_json     TEXT NOT NULL,
    input_tokens  INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cost_usd      REAL DEFAULT 0,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_extractions_hash ON extractions(desc_hash);

CREATE TABLE IF NOT EXISTS runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date         TEXT NOT NULL,
    dry_run          INTEGER NOT NULL DEFAULT 0,
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    status           TEXT NOT NULL DEFAULT 'running',   -- running | success | failed
    error            TEXT,
    postings_fetched INTEGER DEFAULT 0,
    postings_new     INTEGER DEFAULT 0,
    postings_cached  INTEGER DEFAULT 0,
    extracted        INTEGER DEFAULT 0,
    llm_calls        INTEGER DEFAULT 0,
    input_tokens     INTEGER DEFAULT 0,
    output_tokens    INTEGER DEFAULT 0,
    cost_usd         REAL DEFAULT 0,
    source_status    TEXT                               -- JSON list of SourceStatus
);

CREATE TABLE IF NOT EXISTS daily_aggregates (
    day         TEXT NOT NULL,
    family      TEXT NOT NULL,
    level       TEXT NOT NULL,
    category    TEXT NOT NULL,   -- skill | must | nice | cert | expectation
    item        TEXT NOT NULL,
    count       INTEGER NOT NULL,
    n_postings  INTEGER NOT NULL,
    share       REAL NOT NULL,   -- 0..100
    PRIMARY KEY (day, family, level, category, item)
);

CREATE TABLE IF NOT EXISTS search_log (
    source    TEXT NOT NULL,
    query_key TEXT NOT NULL,
    ran_at    TEXT NOT NULL,
    results   INTEGER DEFAULT 0,
    PRIMARY KEY (source, query_key)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SqliteStore:
    def __init__(self, path: Path | str):
        self.path = Path(path) if str(path) != ":memory:" else path
        if isinstance(self.path, Path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------------ postings
    def upsert_posting(self, p: RawPosting, seen_on: str) -> tuple[int, bool]:
        url_norm = normalise_url(p.url)
        h = description_hash(p.description_text)
        row = self.conn.execute(
            "SELECT id FROM postings WHERE url_norm = ? OR (desc_hash = ? AND length(description_text) > 200) "
            "ORDER BY (url_norm = ?) DESC LIMIT 1",
            (url_norm, h, url_norm),
        ).fetchone()
        if row:
            self.conn.execute(
                "UPDATE postings SET last_seen = MAX(last_seen, ?), times_seen = times_seen + 1 WHERE id = ?",
                (seen_on, row["id"]),
            )
            self.conn.commit()
            return int(row["id"]), False
        cur = self.conn.execute(
            """INSERT INTO postings (url_norm, desc_hash, source, url, title, company, location, city,
                   work_mode, employment_type, posted_date, description_text, search_title, role_family,
                   first_seen, last_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (url_norm, h, p.source, p.url, p.title, p.company, p.location, p.city, p.work_mode,
             p.employment_type, p.posted_date, p.description_text, p.search_title, p.role_family,
             seen_on, seen_on),
        )
        self.conn.commit()
        return int(cur.lastrowid), True

    def get_posting(self, posting_id: int) -> dict[str, Any] | None:
        r = self.conn.execute("SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone()
        return dict(r) if r else None

    def set_level(self, posting_id: int, lvl: LevelResult) -> None:
        self.conn.execute(
            """UPDATE postings SET level=?, level_confidence=?, years_min=?, years_max=?, level_signals=?,
                   level_ambiguous=?, status = CASE WHEN status='new' THEN 'classified' ELSE status END
               WHERE id=?""",
            (lvl.level, lvl.confidence, lvl.years_min, lvl.years_max, json.dumps(lvl.signals_used),
             int(lvl.ambiguous), posting_id),
        )
        self.conn.commit()

    def set_status(self, posting_id: int, status: str) -> None:
        self.conn.execute("UPDATE postings SET status=? WHERE id=?", (status, posting_id))
        self.conn.commit()

    def postings_needing_level(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM postings WHERE level IS NULL ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def postings_needing_extraction(self, levels: list[str]) -> list[dict[str, Any]]:
        q = ",".join("?" * len(levels))
        rows = self.conn.execute(
            f"""SELECT p.* FROM postings p LEFT JOIN extractions e ON e.posting_id = p.id
                WHERE e.posting_id IS NULL AND p.level IN ({q})
                  AND p.status IN ('classified','skipped_budget','extract_failed')
                ORDER BY (lower(coalesce(p.city,'')) = 'hyderabad') DESC, p.first_seen DESC, p.id""",
            levels,
        ).fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- extractions
    def get_extraction(self, posting_id: int) -> dict[str, Any] | None:
        r = self.conn.execute("SELECT * FROM extractions WHERE posting_id=?", (posting_id,)).fetchone()
        return self._ext(r)

    def get_extraction_by_hash(self, desc_hash: str) -> dict[str, Any] | None:
        r = self.conn.execute("SELECT * FROM extractions WHERE desc_hash=? LIMIT 1", (desc_hash,)).fetchone()
        return self._ext(r)

    @staticmethod
    def _ext(r: sqlite3.Row | None) -> dict[str, Any] | None:
        if not r:
            return None
        d = dict(r)
        d["data"] = json.loads(d.pop("data_json"))
        return d

    def save_extraction(self, posting_id: int, desc_hash: str, provider: str, model: str,
                        data: dict[str, Any], input_tokens: int, output_tokens: int, cost_usd: float) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO extractions
               (posting_id, desc_hash, provider, model, data_json, input_tokens, output_tokens, cost_usd, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (posting_id, desc_hash, provider, model, json.dumps(data, ensure_ascii=False),
             input_tokens, output_tokens, cost_usd, _now()),
        )
        self.conn.execute("UPDATE postings SET status='extracted' WHERE id=?", (posting_id,))
        self.conn.commit()

    # ------------------------------------------------------------------ snapshot
    def snapshot(self, as_of: str, ttl_days: int) -> list[dict[str, Any]]:
        cutoff = (date.fromisoformat(as_of) - timedelta(days=ttl_days - 1)).isoformat()
        rows = self.conn.execute(
            """SELECT p.*, e.data_json AS extraction_json FROM postings p
               LEFT JOIN extractions e ON e.posting_id = p.id
               WHERE p.first_seen >= ? AND p.first_seen <= ?
               ORDER BY p.id""",
            (cutoff, as_of),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            ej = d.pop("extraction_json")
            d["extraction"] = json.loads(ej) if ej else None
            d["level_signals"] = json.loads(d["level_signals"]) if d.get("level_signals") else []
            out.append(d)
        return out

    # -------------------------------------------------------------- search cache
    def search_done_recently(self, source: str, query_key: str, within_hours: int) -> bool:
        r = self.conn.execute(
            "SELECT ran_at FROM search_log WHERE source=? AND query_key=?", (source, query_key)
        ).fetchone()
        if not r:
            return False
        ran = datetime.fromisoformat(r["ran_at"])
        return datetime.now(timezone.utc) - ran < timedelta(hours=within_hours)

    def record_search(self, source: str, query_key: str, results: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO search_log (source, query_key, ran_at, results) VALUES (?,?,?,?)",
            (source, query_key, _now(), results),
        )
        self.conn.commit()

    # ---------------------------------------------------------------------- runs
    def start_run(self, run_date: str, dry_run: bool) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (run_date, dry_run, started_at) VALUES (?,?,?)", (run_date, int(dry_run), _now())
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, **fields: Any) -> None:
        fields.setdefault("finished_at", _now())
        if "source_status" in fields and not isinstance(fields["source_status"], str):
            fields["source_status"] = json.dumps(fields["source_status"], default=lambda o: o.__dict__)
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE runs SET {cols} WHERE id=?", (*fields.values(), run_id))
        self.conn.commit()

    def last_successful_run(self) -> dict[str, Any] | None:
        r = self.conn.execute(
            "SELECT * FROM runs WHERE status='success' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(r) if r else None

    def recent_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ----------------------------------------------------------------- aggregates
    def save_daily_aggregates(self, day: str, rows: list[dict[str, Any]]) -> None:
        self.conn.execute("DELETE FROM daily_aggregates WHERE day=?", (day,))
        self.conn.executemany(
            """INSERT INTO daily_aggregates (day, family, level, category, item, count, n_postings, share)
               VALUES (:day, :family, :level, :category, :item, :count, :n_postings, :share)""",
            [{**r, "day": day} for r in rows],
        )
        self.conn.commit()

    def get_aggregates(self, date_from: str, date_to: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM daily_aggregates WHERE day >= ? AND day <= ?", (date_from, date_to)
        ).fetchall()
        return [dict(r) for r in rows]

    def aggregate_dates(self, date_from: str, date_to: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT day FROM daily_aggregates WHERE day >= ? AND day <= ? ORDER BY day",
            (date_from, date_to),
        ).fetchall()
        return [r["day"] for r in rows]

    # ----------------------------------------------------------------------- misc
    def stats(self) -> dict[str, Any]:
        one = lambda q, *a: self.conn.execute(q, a).fetchone()[0]  # noqa: E731
        by_status = {r[0]: r[1] for r in self.conn.execute("SELECT status, COUNT(*) FROM postings GROUP BY status")}
        by_level = {r[0] or "unclassified": r[1] for r in
                    self.conn.execute("SELECT level, COUNT(*) FROM postings GROUP BY level ORDER BY level")}
        by_source = {r[0]: r[1] for r in self.conn.execute("SELECT source, COUNT(*) FROM postings GROUP BY source")}
        seen_total = one("SELECT COALESCE(SUM(times_seen),0) FROM postings")
        uniq = one("SELECT COUNT(*) FROM postings")
        return {
            "postings": uniq,
            "extractions": one("SELECT COUNT(*) FROM extractions"),
            "times_seen_total": seen_total,
            "dedupe_hit_rate": round(100 * (seen_total - uniq) / seen_total, 1) if seen_total else 0.0,
            "by_status": by_status,
            "by_level": by_level,
            "by_source": by_source,
            "runs": one("SELECT COUNT(*) FROM runs"),
            "llm_cost_usd_total": round(one("SELECT COALESCE(SUM(cost_usd),0) FROM runs"), 4),
            "searches_logged": one("SELECT COUNT(*) FROM search_log"),
        }

    def backup(self, dest_dir: Path, keep: int = 14) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        dest = dest_dir / f"skillpulse-{stamp}.db"
        with sqlite3.connect(dest) as out:
            self.conn.backup(out)
        backups = sorted(dest_dir.glob("skillpulse-*.db"))
        for old in backups[:-keep] if keep > 0 else []:
            old.unlink()
        return dest

    def close(self) -> None:
        self.conn.close()
