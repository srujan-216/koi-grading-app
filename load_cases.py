"""Load (or incrementally add to) the cohort in the DB.

Reads cohort_manifest.csv under KOA_IMAGES, derives montage frames by grouping
(patient_id, frame suffix). On FIRST run, inserts all manifest rows with a
shuffled display_id ordering. On LATER runs, inserts only the patient_ids that
aren't in the DB yet, appending them with the next-available display_ids —
existing rows are never modified, so grades stay valid and in-progress
radiologists are not disturbed.

Safe to run while grading is live.
"""
import csv, os, random, re, sys
from collections import defaultdict
import db

BASE = os.environ.get("KOA_IMAGES", os.path.join(os.path.dirname(__file__), "COHORT_VIS"))
MAN = os.path.join(BASE, "cohort_manifest.csv")
IMG_REL = "images"
MONT_REL = "patient_montages"

_FRAME_RE = re.compile(r"^(?P<pid>[A-Za-z]+-\d+)_(?P<suffix>f\d+)$")


def _rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            yield r


def _collect():
    """Return [(patient_id, source, [frame_suffixes...])] grouped from the manifest."""
    frames_by_pid = defaultdict(list)
    source_by_pid = {}
    for r in _rows(MAN):
        img_id = r["image_id"].strip()
        pid = r["patient_id"].strip()
        src = r["source"].strip()
        source_by_pid[pid] = src
        m = _FRAME_RE.match(img_id)
        if m:
            frames_by_pid[pid].append(m.group("suffix"))
        else:
            frames_by_pid[pid]  # ensure key exists
    return [(pid, source_by_pid.get(pid, "Unknown"), sorted(set(frames)))
            for pid, frames in frames_by_pid.items()]


def _max_display_num(c):
    """Highest 'Case NNNN' number already in the DB (0 if none)."""
    rows = c.execute("SELECT display_id FROM cases").fetchall()
    best = 0
    for r in rows:
        did = r["display_id"] if isinstance(r, dict) else r[0]
        try:
            n = int(str(did).split()[-1])
            if n > best:
                best = n
        except Exception:
            pass
    return best


def run():
    if not os.path.exists(MAN):
        print(f"[load_cases] manifest not found at {MAN}", file=sys.stderr)
        print("[load_cases] set KOA_IMAGES to the folder containing cohort_manifest.csv", file=sys.stderr)
        sys.exit(1)

    c = db.conn()
    try:
        existing_rows = c.execute("SELECT patient_id FROM cases").fetchall()
        existing_pids = set()
        for r in existing_rows:
            pid = r["patient_id"] if isinstance(r, dict) else r[0]
            existing_pids.add(pid)

        patients = _collect()
        new_patients = [p for p in patients if p[0] not in existing_pids]

        if not new_patients:
            print(f"[load_cases] no new cases to add; {len(existing_pids)} already loaded.")
            return

        # Deterministic shuffle of the new batch only. Seed depends on how many
        # cases already exist, so the second batch gets its own shuffle without
        # touching the first batch's ordering.
        random.Random(42 + len(existing_pids)).shuffle(new_patients)

        start = _max_display_num(c) + 1
        inserted = 0
        for i, (pid, source, frames) in enumerate(new_patients):
            disp = f"Case {start + i:04d}"
            if not frames:
                main_image = f"{IMG_REL}/{pid}.png"
                db.insert_case(c, disp, source, pid, "single", main_image, "", "")
            else:
                montage_image = f"{MONT_REL}/{pid}.png"
                db.insert_case(c, disp, source, pid, "montage", "", montage_image, ";".join(frames))
            inserted += 1

        db.commit(c)
        total = db.execute_scalar(c, "SELECT COUNT(*) FROM cases")
        action = "inserted" if not existing_pids else "ADDED"
        print(f"[load_cases] {action} {inserted} new cases; total in DB: {total}")
    finally:
        db.close(c)


if __name__ == "__main__":
    run()
