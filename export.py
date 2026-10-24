"""Export grades three ways: per-reader CSVs, model-ready wide table, publishable table.
Backend is picked up from env vars by db.py (SQLite or Postgres).

Usage:
  python export.py                           # writes to ./exports/
  python export.py /path/to/outdir           # writes there instead
"""
import csv
import os
import sys
from collections import Counter

import db

OUT = sys.argv[1] if len(sys.argv) > 1 else "exports"
os.makedirs(OUT, exist_ok=True)


def _consensus(vals):
    vals = [v for v in vals if v not in (None, "", "NA")]
    if not vals:
        return ""
    return Counter(vals).most_common(1)[0][0]


def main():
    readers_rows = db.all_readers()
    cases_rows = db.all_cases()
    grades_rows = db.all_grades()

    readers = {r["reader_id"]: r["name"] for r in readers_rows}
    cases = {r["case_id"]: r for r in cases_rows}

    # 1) per-reader CSVs
    for rid, name in readers.items():
        rows = [g for g in grades_rows if g["reader_id"] == rid]
        safe = "".join(ch if ch.isalnum() else "_" for ch in name).strip("_") or f"reader_{rid}"
        path = os.path.join(OUT, f"reader_{safe}.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                "display_id", "source", "patient_id", "chosen_frame",
                "kl_right", "kl_left", "confidence", "usable",
                "notes", "seconds", "timestamp",
            ])
            for g in rows:
                cs = cases[g["case_id"]]
                w.writerow([
                    cs["display_id"], cs["source"], cs["patient_id"], g["chosen_frame"],
                    g["kl_right"], g["kl_left"], g["confidence"], g["usable"],
                    g["notes"], g["seconds_spent"], g["timestamp"],
                ])

    # 2) model-ready WIDE: one row per case, each reader's grades + simple consensus
    model_path = os.path.join(OUT, "model_ready.csv")
    with open(model_path, "w", newline="", encoding="utf-8") as f:
        cols = ["display_id", "source", "patient_id", "chosen_frame"]
        for rid in readers:
            cols += [f"{readers[rid]}_R", f"{readers[rid]}_L", f"{readers[rid]}_conf"]
        cols += ["consensus_R", "consensus_L", "n_readers"]
        w = csv.writer(f)
        w.writerow(cols)
        for cid, cs in cases.items():
            gs = {g["reader_id"]: g for g in grades_rows if g["case_id"] == cid}
            if not gs:
                continue
            frame = next(iter(gs.values()))["chosen_frame"]
            row = [cs["display_id"], cs["source"], cs["patient_id"], frame]
            for rid in readers:
                g = gs.get(rid)
                row += [
                    g["kl_right"] if g else "",
                    g["kl_left"] if g else "",
                    g["confidence"] if g else "",
                ]
            row += [
                _consensus([gs[r]["kl_right"] for r in gs]),
                _consensus([gs[r]["kl_left"] for r in gs]),
                len(gs),
            ]
            w.writerow(row)

    # 3) publishable (no names; source re-mapped to Centre1/Centre2/...)
    pub_path = os.path.join(OUT, "publishable.csv")
    with open(pub_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "centre", "reader", "kl_right", "kl_left", "confidence", "usable"])
        cmap = {}
        for g in grades_rows:
            cs = cases[g["case_id"]]
            centre = cmap.setdefault(cs["source"], f"Centre{len(cmap) + 1}")
            w.writerow([
                cs["display_id"], centre, f"Reader{g['reader_id']}",
                g["kl_right"], g["kl_left"], g["confidence"], g["usable"],
            ])

    print(f"exports written to {OUT}/ :", sorted(os.listdir(OUT)))


if __name__ == "__main__":
    main()
