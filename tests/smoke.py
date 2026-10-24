"""Local smoke test. Runs against whichever backend db.py selects.

Usage:
  # SQLite (default): isolates to a temp DB
  python tests/smoke.py

  # Postgres: set KOA_PG_URL to a *scratch* database and
  #           set KOA_TEST_ALLOW_PG=1 to allow truncating tables there.
  KOA_PG_URL=postgres://... KOA_TEST_ALLOW_PG=1 python tests/smoke.py
"""
import os
import random
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# For SQLite: isolate test DB BEFORE importing db
if not os.environ.get("KOA_PG_URL"):
    _tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    _tmp_db.close()
    os.environ["KOA_DB"] = _tmp_db.name

import db  # noqa: E402


def _reset_pg():
    if os.environ.get("KOA_TEST_ALLOW_PG") != "1":
        print("Refusing to truncate Postgres tables without KOA_TEST_ALLOW_PG=1")
        sys.exit(2)
    c = db.conn()
    try:
        c.execute("TRUNCATE grades, cases, readers RESTART IDENTITY CASCADE")
        c.commit()
    finally:
        db.close(c)


def _seed_cases(n=50):
    """Insert n synthetic cases directly (bypasses load_cases to keep the test
    independent of the manifest's shape)."""
    c = db.conn()
    try:
        for i in range(1, n + 1):
            db.insert_case(
                c, f"Case {i:04d}", "SourceA" if i % 2 else "SourceB",
                f"TEST-{i:04d}", "montage", "", f"patient_montages/TEST-{i:04d}.png",
                "f01;f02",
            )
        db.commit(c)
    finally:
        db.close(c)


# ---------- the test ----------
def main():
    if db.BACKEND == "postgres":
        _reset_pg()

    _seed_cases(50)
    assert db.total_cases() == 50, "total_cases mismatch"
    print("[ok] seeded 50 cases")

    # 1) Race-safe reader creation under concurrent login with the same name
    results = []
    errors = []

    def _login(name):
        try:
            results.append(db.get_or_create_reader(name, "PG / Resident"))
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=_login, args=("Dr. Race",)) for _ in range(10)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert not errors, f"concurrent login raised: {errors}"
    assert len(set(results)) == 1, f"expected one reader_id, got {set(results)}"
    print("[ok] concurrent login resolved to a single reader_id")

    # 2) Create three real readers
    r1 = db.get_or_create_reader("Reader One", "Radiologist")
    r2 = db.get_or_create_reader("Reader Two", "Consultant/Senior Radiologist")
    r3 = db.get_or_create_reader("Reader Three", "PG / Resident")
    assert len({r1, r2, r3}) == 3

    # 3) Concurrent writes: 10 threads save grades for different reader+case combos
    def _writer(rid, start, stop):
        for cid in range(start, stop + 1):
            db.save_grade(rid, cid, f"TEST-{cid:04d}_f01", "2", "3", 80, "yes", "", 1.0)

    threads = [
        threading.Thread(target=_writer, args=(r1, 1, 20)),
        threading.Thread(target=_writer, args=(r2, 1, 20)),
        threading.Thread(target=_writer, args=(r3, 1, 20)),
    ]
    for t in threads: t.start()
    for t in threads: t.join()
    assert db.graded_count(r1) == 20
    assert db.graded_count(r2) == 20
    assert db.graded_count(r3) == 20
    print("[ok] three readers each wrote 20 grades concurrently")

    # 4) Resume: next_case must return case_id 21 (first ungraded) for r1
    nxt = db.next_case(r1)
    assert nxt is not None and nxt["case_id"] == 21, f"resume wrong: {dict(nxt) if nxt else None}"
    print(f"[ok] resume returns next ungraded case (case_id={nxt['case_id']})")

    # 5) Re-grade: saving the SAME (reader, case) must update, not insert
    before = db.all_grades()
    db.save_grade(r1, 1, "TEST-0001_f02", "4", "4", 95, "yes", "updated", 2.5)
    after = db.all_grades()
    assert len(before) == len(after), "re-grade must not create a new row"
    row = [g for g in after if g["reader_id"] == r1 and g["case_id"] == 1][0]
    assert row["kl_right"] == "4" and row["notes"] == "updated"
    print("[ok] re-grading updates the same row (no duplicate)")

    # 6) UNIQUE enforcement sanity (lower-level): same (reader, case) under threads = still 1 row
    def _spam():
        for _ in range(20):
            db.save_grade(r2, 2, "x", str(random.randint(0, 4)), "1", 50, "yes", "", 0.1)
    threads = [threading.Thread(target=_spam) for _ in range(5)]
    for t in threads: t.start()
    for t in threads: t.join()
    all_g = db.all_grades()
    r2c2 = [g for g in all_g if g["reader_id"] == r2 and g["case_id"] == 2]
    assert len(r2c2) == 1, f"expected 1 row for (r2, case 2), got {len(r2c2)}"
    print("[ok] upsert stays atomic under heavy concurrent re-grading")

    # 7) reader_progress rolls up correctly
    rp = {r["name"]: r["graded"] for r in db.reader_progress()}
    assert rp.get("Reader One") == 20
    assert rp.get("Reader Two") == 20
    assert rp.get("Reader Three") == 20
    print("[ok] reader_progress rollup correct")

    # 8) Source stays out of the UI: app.py never dereferences case['source']
    app_py = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert "case['source']" not in app_py and 'case["source"]' not in app_py, \
        "app.py appears to render case['source'] — remove it"
    print("[ok] app.py does not reference case['source']")

    print("\nALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()
