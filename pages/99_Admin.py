"""Hidden admin page. Shows per-reader progress and offers on-demand CSV
exports and a full raw-grades dump. Gated by ADMIN_TOKEN env var.

Users without the token see only a login prompt — they can't reach any data.
Streamlit auto-registers this file under `pages/` in the sidebar, so to
truly hide it from radiologists, either:
  - set ADMIN_TOKEN to a long random string (link stays, data stays locked), or
  - deploy an app config that hides the sidebar nav for non-admins.
"""
import csv
import io
import os
import zipfile
from collections import Counter

import streamlit as st

import db
import export as export_mod  # reuses the write-to-disk helpers

st.set_page_config(page_title="Admin — KOA Grading", layout="wide")
st.title("Admin — KOA Grading")

TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()

if not TOKEN:
    st.error(
        "ADMIN_TOKEN env var is not set on this deployment. Set it to a long "
        "random value in the Space secrets, then reload."
    )
    st.stop()

if "admin_ok" not in st.session_state:
    st.session_state.admin_ok = False

if not st.session_state.admin_ok:
    tok = st.text_input("Admin token", type="password")
    if st.button("Unlock"):
        if tok == TOKEN:
            st.session_state.admin_ok = True
            st.rerun()
        else:
            st.error("Wrong token.")
    st.stop()


# ---------- live progress ----------
total = db.total_cases()
readers = db.reader_progress()
grades = db.all_grades()

c1, c2, c3 = st.columns(3)
c1.metric("Total cases", total)
c2.metric("Total readers", len(readers))
c3.metric("Total grades written", len(grades))

st.subheader("Per-reader progress")
rows = []
for r in readers:
    pct = (r["graded"] / total * 100) if total else 0
    rows.append({
        "reader_id": r["reader_id"],
        "name": r["name"],
        "seniority": r.get("seniority") if isinstance(r, dict) else r["seniority"],
        "graded": r["graded"],
        "pct": f"{pct:.1f}%",
        "last_at": r["last_at"],
    })
st.dataframe(rows, use_container_width=True)

# ---------- grading-value distribution sanity check ----------
st.subheader("Grade distribution")
klr = Counter(g["kl_right"] for g in grades)
kll = Counter(g["kl_left"] for g in grades)
usable = Counter(g["usable"] for g in grades)
st.write("KL right:", dict(klr))
st.write("KL left:", dict(kll))
st.write("Usable:", dict(usable))


# ---------- on-demand exports ----------
st.subheader("Downloads")


def _csv_bytes(header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for row in rows:
        w.writerow(row)
    return buf.getvalue().encode("utf-8")


def _all_exports_zip():
    cases = {c["case_id"]: c for c in db.all_cases()}
    reader_map = {r["reader_id"]: r["name"] for r in db.all_readers()}

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # per-reader CSVs
        for rid, name in reader_map.items():
            rows = [g for g in grades if g["reader_id"] == rid]
            safe = "".join(ch if ch.isalnum() else "_" for ch in name).strip("_") or f"reader_{rid}"
            data = _csv_bytes(
                ["display_id", "source", "patient_id", "chosen_frame",
                 "kl_right", "kl_left", "confidence", "usable",
                 "notes", "seconds", "timestamp"],
                [[cases[g["case_id"]]["display_id"], cases[g["case_id"]]["source"],
                  cases[g["case_id"]]["patient_id"], g["chosen_frame"],
                  g["kl_right"], g["kl_left"], g["confidence"], g["usable"],
                  g["notes"], g["seconds_spent"], g["timestamp"]] for g in rows],
            )
            z.writestr(f"reader_{safe}.csv", data)

        # model-ready wide
        header = ["display_id", "source", "patient_id", "chosen_frame"]
        for rid in reader_map:
            header += [f"{reader_map[rid]}_R", f"{reader_map[rid]}_L", f"{reader_map[rid]}_conf"]
        header += ["consensus_R", "consensus_L", "n_readers"]
        wide_rows = []
        for cid, cs in cases.items():
            gs = {g["reader_id"]: g for g in grades if g["case_id"] == cid}
            if not gs: continue
            frame = next(iter(gs.values()))["chosen_frame"]
            row = [cs["display_id"], cs["source"], cs["patient_id"], frame]
            for rid in reader_map:
                g = gs.get(rid)
                row += [g["kl_right"] if g else "", g["kl_left"] if g else "",
                        g["confidence"] if g else ""]
            row += [
                export_mod._consensus([gs[r]["kl_right"] for r in gs]),
                export_mod._consensus([gs[r]["kl_left"] for r in gs]),
                len(gs),
            ]
            wide_rows.append(row)
        z.writestr("model_ready.csv", _csv_bytes(header, wide_rows))

        # publishable
        pub_rows = []
        cmap = {}
        for g in grades:
            cs = cases[g["case_id"]]
            centre = cmap.setdefault(cs["source"], f"Centre{len(cmap) + 1}")
            pub_rows.append([cs["display_id"], centre, f"Reader{g['reader_id']}",
                             g["kl_right"], g["kl_left"], g["confidence"], g["usable"]])
        z.writestr("publishable.csv", _csv_bytes(
            ["case_id", "centre", "reader", "kl_right", "kl_left", "confidence", "usable"],
            pub_rows,
        ))

        # raw grades + readers + cases for full backup
        z.writestr("raw_grades.csv", _csv_bytes(
            list(grades[0].keys()) if grades else ["grade_id"],
            [list(g.values()) if isinstance(g, dict) else list(g) for g in grades],
        ))
        z.writestr("raw_readers.csv", _csv_bytes(
            ["reader_id", "name", "seniority", "created_at"],
            [[r["reader_id"], r["name"], r.get("seniority") if isinstance(r, dict) else r["seniority"],
              r.get("created_at") if isinstance(r, dict) else r["created_at"]]
             for r in db.all_readers()],
        ))
    return buf.getvalue()


st.download_button(
    "Download EVERYTHING (zip: per-reader + model-ready + publishable + raw)",
    data=_all_exports_zip(),
    file_name="koa_grading_export.zip",
    mime="application/zip",
    type="primary",
)

st.caption(
    "This export is a point-in-time snapshot generated from the live database. "
    "The hosted Postgres also has daily backups via Supabase."
)
