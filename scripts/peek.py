"""Print a quick summary of what's in the DB — use it to verify data is landing
correctly during local testing.

Usage (same shell where you set KOA_DB):
  python scripts\peek.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db

print(f"backend: {db.BACKEND}  |  db: {os.environ.get('KOA_DB') or os.environ.get('KOA_PG_URL','?')[:40]+'…'}")
print(f"total cases: {db.total_cases()}")

readers = db.all_readers()
print(f"\nreaders: {len(readers)}")
for r in readers:
    print(f"  id={r['reader_id']}  name={r['name']!r}  seniority={r['seniority']!r}")

prog = db.reader_progress()
print(f"\nper-reader progress:")
for p in prog:
    print(f"  {p['name']!r}: graded={p['graded']}  last_at={p['last_at']}")

grades = db.all_grades()
print(f"\ntotal grades written: {len(grades)}")
if grades:
    cases = {c["case_id"]: c for c in db.all_cases()}
    readers_by_id = {r["reader_id"]: r["name"] for r in readers}
    print("last 10 grades:")
    for g in grades[-10:]:
        cs = cases[g["case_id"]]
        print(
            f"  [{g['timestamp']}] {readers_by_id[g['reader_id']]!r} graded "
            f"{cs['display_id']}  frame={g['chosen_frame']}  "
            f"R={g['kl_right']}  L={g['kl_left']}  conf={g['confidence']}  "
            f"usable={g['usable']}  secs={g['seconds_spent']}  "
            f"notes={g['notes']!r}"
        )
