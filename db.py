"""Database layer. Same API for SQLite (local) and Postgres (hosted).

Backend selection:
  - If KOA_PG_URL env var is set, use Postgres via psycopg2.
  - Else, use SQLite at KOA_DB (default: koa_grading.db).

Public functions used by app.py / load_cases.py / export.py:
  conn, close, commit, execute_scalar, insert_case,
  get_or_create_reader, total_cases, graded_count,
  next_case, get_case, existing_grade, save_grade,
  all_readers, reader_progress, all_grades, all_cases.

Schema is identical across backends: readers, cases, grades, with a UNIQUE
(reader_id, case_id) constraint on grades so re-grading upserts in-place.
"""
import os, datetime, threading, time

_PG_URL = os.environ.get("KOA_PG_URL", "").strip()
_SQLITE_PATH = os.environ.get("KOA_DB", "koa_grading.db")
BACKEND = "postgres" if _PG_URL else "sqlite"

if BACKEND == "sqlite":
    import sqlite3
    _init_lock = threading.Lock()
    _schema_installed = False

    _SCHEMA_SQLITE = """
    CREATE TABLE IF NOT EXISTS readers(
      reader_id   INTEGER PRIMARY KEY AUTOINCREMENT,
      name        TEXT UNIQUE NOT NULL,
      seniority   TEXT,
      created_at  TEXT
    );
    CREATE TABLE IF NOT EXISTS cases(
      case_id        INTEGER PRIMARY KEY AUTOINCREMENT,
      display_id     TEXT UNIQUE NOT NULL,
      source         TEXT,
      patient_id     TEXT UNIQUE NOT NULL,
      kind           TEXT,
      main_image     TEXT,
      montage_image  TEXT,
      frames         TEXT
    );
    CREATE TABLE IF NOT EXISTS grades(
      grade_id        INTEGER PRIMARY KEY AUTOINCREMENT,
      reader_id       INTEGER NOT NULL,
      case_id         INTEGER NOT NULL,
      chosen_frame    TEXT,
      kl_right        TEXT,
      kl_left         TEXT,
      confidence      INTEGER,
      usable          TEXT,
      notes           TEXT,
      seconds_spent   REAL,
      timestamp       TEXT,
      UNIQUE(reader_id, case_id)
    );
    """

    def conn():
        global _schema_installed
        c = sqlite3.connect(_SQLITE_PATH, check_same_thread=False, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA busy_timeout=10000")
        if not _schema_installed:
            with _init_lock:
                if not _schema_installed:
                    c.executescript(_SCHEMA_SQLITE)
                    c.commit()
                    _schema_installed = True
        return c

    def _q(sql):  # placeholder passthrough
        return sql

    def _row_to_dict(row):
        return dict(row) if row is not None else None

else:
    import psycopg2
    import psycopg2.extras
    import psycopg2.pool

    _SCHEMA_PG = """
    CREATE TABLE IF NOT EXISTS readers(
      reader_id   SERIAL PRIMARY KEY,
      name        TEXT UNIQUE NOT NULL,
      seniority   TEXT,
      created_at  TEXT
    );
    CREATE TABLE IF NOT EXISTS cases(
      case_id        SERIAL PRIMARY KEY,
      display_id     TEXT UNIQUE NOT NULL,
      source         TEXT,
      patient_id     TEXT UNIQUE NOT NULL,
      kind           TEXT,
      main_image     TEXT,
      montage_image  TEXT,
      frames         TEXT
    );
    CREATE TABLE IF NOT EXISTS grades(
      grade_id        SERIAL PRIMARY KEY,
      reader_id       INTEGER NOT NULL REFERENCES readers(reader_id),
      case_id         INTEGER NOT NULL REFERENCES cases(case_id),
      chosen_frame    TEXT,
      kl_right        TEXT,
      kl_left         TEXT,
      confidence      INTEGER,
      usable          TEXT,
      notes           TEXT,
      seconds_spent   DOUBLE PRECISION,
      timestamp       TEXT,
      UNIQUE(reader_id, case_id)
    );
    """

    _init_lock = threading.Lock()
    _schema_installed = False
    _pool = None

    def _get_pool():
        global _pool
        if _pool is None:
            with _init_lock:
                if _pool is None:
                    _pool = psycopg2.pool.ThreadedConnectionPool(
                        minconn=1, maxconn=10, dsn=_PG_URL
                    )
        return _pool

    class _PgConn:
        """Thin wrapper so callers can use the same c.execute / c.close pattern.
        close() returns the connection to the pool rather than tearing it down."""
        def __init__(self, raw):
            self.raw = raw

        def execute(self, sql, params=()):
            cur = self.raw.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(_q(sql), params)
            return _PgCursor(cur)

        def commit(self):
            self.raw.commit()

        def rollback(self):
            self.raw.rollback()

        def close(self):
            try:
                self.raw.rollback()
            except Exception:
                pass
            _get_pool().putconn(self.raw)

    class _PgCursor:
        def __init__(self, cur):
            self.cur = cur
        def fetchone(self):
            return self.cur.fetchone()
        def fetchall(self):
            return self.cur.fetchall()
        def __iter__(self):
            return iter(self.cur.fetchall())

    def conn():
        global _schema_installed
        pool = _get_pool()
        raw = pool.getconn()
        c = _PgConn(raw)
        if not _schema_installed:
            with _init_lock:
                if not _schema_installed:
                    cur = raw.cursor()
                    cur.execute(_SCHEMA_PG)
                    raw.commit()
                    cur.close()
                    _schema_installed = True
        return c

    def _q(sql):
        return sql.replace("?", "%s")

    def _row_to_dict(row):
        return dict(row) if row is not None else None


# ---------- shared helpers used by load_cases.py / export.py ----------

def close(c):
    c.close()

def commit(c):
    c.commit()

def execute_scalar(c, sql, params=()):
    row = c.execute(sql, params).fetchone()
    if row is None:
        return None
    if isinstance(row, dict):
        return list(row.values())[0]
    return row[0]

def insert_case(c, display_id, source, patient_id, kind, main_image, montage_image, frames):
    """Insert, or no-op if this patient_id is already in the DB.
    Lets load_cases.py be safely re-run while grading is live."""
    c.execute(
        "INSERT INTO cases(display_id,source,patient_id,kind,main_image,montage_image,frames) "
        "VALUES(?,?,?,?,?,?,?) ON CONFLICT (patient_id) DO NOTHING",
        (display_id, source, patient_id, kind, main_image, montage_image, frames),
    )


# ---------- app-level API ----------

def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def get_or_create_reader(name, seniority=""):
    """Race-safe: INSERT ... ON CONFLICT DO NOTHING, then SELECT.
    Both branches never create a duplicate under concurrent login."""
    c = conn()
    try:
        c.execute(
            "INSERT INTO readers(name, seniority, created_at) VALUES(?,?,?) "
            "ON CONFLICT (name) DO NOTHING",
            (name, seniority, _now_iso()),
        )
        c.commit()
        row = c.execute("SELECT reader_id FROM readers WHERE name=?", (name,)).fetchone()
        row = _row_to_dict(row) if BACKEND == "sqlite" else row
        return row["reader_id"]
    finally:
        close(c)

def total_cases():
    c = conn()
    try:
        return execute_scalar(c, "SELECT COUNT(*) FROM cases")
    finally:
        close(c)

def graded_count(reader_id):
    c = conn()
    try:
        return execute_scalar(c, "SELECT COUNT(*) FROM grades WHERE reader_id=?", (reader_id,))
    finally:
        close(c)

def next_case(reader_id):
    """First case this reader has NOT graded yet, by case_id order (resume point)."""
    c = conn()
    try:
        row = c.execute(
            "SELECT * FROM cases WHERE case_id NOT IN "
            "(SELECT case_id FROM grades WHERE reader_id=?) "
            "ORDER BY case_id LIMIT 1",
            (reader_id,),
        ).fetchone()
        return _row_to_dict(row) if BACKEND == "sqlite" else row
    finally:
        close(c)

def get_case(case_id):
    c = conn()
    try:
        row = c.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
        return _row_to_dict(row) if BACKEND == "sqlite" else row
    finally:
        close(c)

def existing_grade(reader_id, case_id):
    c = conn()
    try:
        row = c.execute(
            "SELECT * FROM grades WHERE reader_id=? AND case_id=?",
            (reader_id, case_id),
        ).fetchone()
        return _row_to_dict(row) if BACKEND == "sqlite" else row
    finally:
        close(c)

def _save_grade_once(reader_id, case_id, chosen_frame, kl_right, kl_left,
                     confidence, usable, notes, seconds, now):
    c = conn()
    try:
        c.execute(
            "INSERT INTO grades(reader_id,case_id,chosen_frame,kl_right,kl_left,"
            "confidence,usable,notes,seconds_spent,timestamp) "
            "VALUES(?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(reader_id,case_id) DO UPDATE SET "
            "  chosen_frame=EXCLUDED.chosen_frame, kl_right=EXCLUDED.kl_right, "
            "  kl_left=EXCLUDED.kl_left, confidence=EXCLUDED.confidence, "
            "  usable=EXCLUDED.usable, notes=EXCLUDED.notes, "
            "  seconds_spent=EXCLUDED.seconds_spent, timestamp=EXCLUDED.timestamp",
            (reader_id, case_id, chosen_frame, kl_right, kl_left,
             confidence, usable, notes, seconds, now),
        )
        c.commit()
    finally:
        close(c)


def save_grade(reader_id, case_id, chosen_frame, kl_right, kl_left,
               confidence, usable, notes, seconds):
    """Atomic upsert on (reader_id, case_id). Re-grading updates in place.

    Retries up to 3 times on transient failures (network blip, pooler reset)
    so a single hiccup never swallows a grade. Raises the last exception if
    every attempt fails; the caller must keep the user on the same case."""
    now = _now_iso()
    last = None
    for attempt in range(4):
        try:
            _save_grade_once(reader_id, case_id, chosen_frame, kl_right, kl_left,
                             confidence, usable, notes, seconds, now)
            return
        except Exception as e:
            last = e
            if attempt < 3:
                time.sleep(0.2 * (2 ** attempt))  # 0.2s, 0.4s, 0.8s
    raise last


# ---------- admin / export helpers ----------

def all_readers():
    c = conn()
    try:
        rows = c.execute("SELECT * FROM readers ORDER BY reader_id").fetchall()
        return [_row_to_dict(r) if BACKEND == "sqlite" else r for r in rows]
    finally:
        close(c)

def all_cases():
    c = conn()
    try:
        rows = c.execute("SELECT * FROM cases ORDER BY case_id").fetchall()
        return [_row_to_dict(r) if BACKEND == "sqlite" else r for r in rows]
    finally:
        close(c)

def all_grades():
    c = conn()
    try:
        rows = c.execute("SELECT * FROM grades ORDER BY grade_id").fetchall()
        return [_row_to_dict(r) if BACKEND == "sqlite" else r for r in rows]
    finally:
        close(c)

def reader_progress():
    """[(reader_id, name, seniority, graded_count, last_timestamp)]"""
    c = conn()
    try:
        rows = c.execute(
            "SELECT r.reader_id, r.name, r.seniority, "
            "       COUNT(g.grade_id) AS graded, "
            "       MAX(g.timestamp) AS last_at "
            "FROM readers r LEFT JOIN grades g ON g.reader_id = r.reader_id "
            "GROUP BY r.reader_id, r.name, r.seniority "
            "ORDER BY graded DESC, r.name"
        ).fetchall()
        return [_row_to_dict(r) if BACKEND == "sqlite" else r for r in rows]
    finally:
        close(c)
