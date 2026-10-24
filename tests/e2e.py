"""Full end-to-end test. Uses Streamlit's AppTest framework to drive the real
app.py through every grading flow, then verifies DB state and exports.

Run (PowerShell):
  $env:KOA_IMAGES = "D:\\downloads\\grading_app\\COHORT_VIS"
  $env:ADMIN_TOKEN = "letmein"
  python tests\e2e.py
"""
import csv
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False); _tmp_db.close()
os.environ.pop("KOA_PG_URL", None)  # force SQLite — never touch the real cloud DB from tests
# Also drop any Supabase Storage config so _image_src() falls back to local files
for _k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_BUCKET"):
    os.environ.pop(_k, None)
os.environ["KOA_DB"] = _tmp_db.name
os.environ["KOA_IMAGES"] = os.path.join(ROOT, "COHORT_VIS")
os.environ["ADMIN_TOKEN"] = "letmein"
os.environ["USERS"] = (
    "dra:jade-cobalt-71:PG / Resident\n"
    "drb:cedar-plum-83:Consultant/Senior Radiologist\n"
    "drc:rapid-noble-29:Consultant/Senior Radiologist\n"
)

import load_cases
load_cases.run()

import db
from streamlit.testing.v1 import AppTest

APP_PATH = os.path.join(ROOT, "app.py")
ADMIN_PATH = os.path.join(ROOT, "pages", "99_Admin.py")

PASS = []; FAIL = []
def ok(msg): PASS.append(msg); print(f"[OK ] {msg}")
def bad(msg): FAIL.append(msg); print(f"[BAD] {msg}")


def login(username, password):
    at = AppTest.from_file(APP_PATH, default_timeout=15).run()
    at.text_input[0].set_value(username)
    at.text_input[1].set_value(password)
    at.button[0].click().run()
    return at


# The grading page uses these radios in this order:
#   [0] frame picker (f01;f02;f03)   -- only present when case kind == 'montage'
#   [1] RIGHT KL (0..4, NA)
#   [2] LEFT KL  (0..4, NA)
#   [3] usable (yes/no)
# For single-image cases there's no frame radio, so the KL radios move up.
def set_grade(at, frame=None, klr=None, kll=None, usable=None, conf=None, note=None):
    rs = list(at.radio)
    idx = 0
    if frame is not None:
        rs[idx].set_value(frame); idx += 1
    else:
        idx += 1  # skip frame radio even if we don't set it (montage case)
    if klr is not None: rs[idx].set_value(klr)
    idx += 1
    if kll is not None: rs[idx].set_value(kll)
    idx += 1
    if usable is not None: rs[idx].set_value(usable)
    if conf is not None:
        at.slider[0].set_value(conf)
    if note is not None:
        at.text_input[0].set_value(note)
    return at.run()


# ---------- Test 1: Login creates reader ----------
# Rejects wrong password first
print("\n=== Test 0: Login rejects wrong password ===")
at_bad = login("dra", "wrong")
if any("Wrong username or password" in e.value for e in at_bad.error):
    ok("wrong password rejected")
else:
    bad("wrong password should have been rejected")
if len(db.all_readers()) == 0:
    ok("failed login did NOT create a DB row")
else:
    bad(f"failed login created a row: {[r['name'] for r in db.all_readers()]}")

print("\n=== Test 1: Login flow ===")
at = login("dra", "jade-cobalt-71")
md = " | ".join(m.value for m in at.markdown)
(ok if "Grader" in md else bad)("logged in and landed on grading page")
(ok if "Case 0001" in md else bad)("showed first case (Case 0001)")
readers = db.all_readers()
if len(readers) == 1 and readers[0]["name"] == "dra":
    ok(f"DB has exactly one reader: id={readers[0]['reader_id']} name='dra'")
    seniority = readers[0]["seniority"] if isinstance(readers[0], dict) else readers[0]["seniority"]
    (ok if seniority == "PG / Resident" else bad)(f"seniority stored from USERS secret: {seniority!r}")
else:
    bad(f"readers table unexpected: {[dict(r) for r in readers]}")

# ---------- Test 2: Required-field guard ----------
print("\n=== Test 2: Required-field guard ===")
save_btn = at.button[-1]
(ok if save_btn.disabled else bad)("Save disabled when nothing picked")
caps = " | ".join(c.value for c in at.caption)
if "RIGHT KL" in caps and "LEFT KL" in caps and "AP frame" in caps:
    ok("caption lists all three missing fields")
else:
    bad(f"missing-fields caption wrong: {caps!r}")

# Pick only the frame — Save should still be disabled
at.radio[0].set_value("f01")
at.run()
(ok if at.button[-1].disabled else bad)("Save still disabled after frame only")

# Pick RIGHT and LEFT — Save should enable
at.radio[1].set_value("2"); at.radio[2].set_value("3"); at.run()
(ok if not at.button[-1].disabled else bad)("Save enabled after frame + both KL")

# ---------- Test 3: Happy-path save ----------
print("\n=== Test 3: Happy-path save ===")
at.slider[0].set_value(85)
at.text_input[0].set_value("dra first grade")
at.button[-1].click().run()

alpha_id = readers[0]["reader_id"]
grades = db.all_grades()
if len(grades) == 1:
    g = grades[0]
    checks = {
        "reader_id matches": g["reader_id"] == alpha_id,
        "case_id = 1": g["case_id"] == 1,
        "kl_right = '2'": g["kl_right"] == "2",
        "kl_left = '3'": g["kl_left"] == "3",
        "confidence = 85": g["confidence"] == 85,
        "usable = 'yes'": g["usable"] == "yes",
        "notes stored": g["notes"] == "dra first grade",
        "chosen_frame has VIS-": g["chosen_frame"].startswith("VIS-"),
        "chosen_frame has _f01": g["chosen_frame"].endswith("_f01"),
        "seconds_spent >= 0": g["seconds_spent"] >= 0,
        "timestamp populated": bool(g["timestamp"]),
    }
    for k, v in checks.items():
        (ok if v else bad)(f"grade: {k}")
else:
    bad(f"expected 1 grade, got {len(grades)}")

# ---------- Test 4: Advance to next case + progress ----------
print("\n=== Test 4: Advance + progress ===")
md4 = " | ".join(m.value for m in at.markdown)
(ok if "Case 0002" in md4 else bad)("advanced to Case 0002 after save")
if db.graded_count(alpha_id) == 1:
    ok("graded_count=1 after one save")
else:
    bad(f"graded_count wrong: {db.graded_count(alpha_id)}")

# ---------- Test 5: NA + usable=no path ----------
print("\n=== Test 5: NA / usable=no path ===")
at.radio[0].set_value("f01")
at.radio[1].set_value("NA"); at.radio[2].set_value("NA")
at.radio[3].set_value("no")
at.text_input[0].set_value("blurry film")
at.run()
at.button[-1].click().run()

g2 = next(x for x in db.all_grades() if x["case_id"] == 2)
checks = {
    "R=NA": g2["kl_right"] == "NA",
    "L=NA": g2["kl_left"] == "NA",
    "usable=no": g2["usable"] == "no",
    "notes=blurry film": g2["notes"] == "blurry film",
}
for k, v in checks.items():
    (ok if v else bad)(f"NA/no path: {k}")

# ---------- Test 6: Resume ----------
print("\n=== Test 6: Resume ===")
at2 = login("dra", "jade-cobalt-71")
md6 = " | ".join(m.value for m in at2.markdown)
(ok if "Case 0003" in md6 else bad)(f"dra resumes at Case 0003 after relogin")
(ok if len(db.all_readers()) == 1 else bad)("no duplicate reader created on relogin")

# ---------- Test 7: Second reader independence ----------
print("\n=== Test 7: Second reader ===")
at3 = login("drb", "cedar-plum-83")
md7 = " | ".join(m.value for m in at3.markdown)
(ok if "Case 0001" in md7 else bad)("drb starts at Case 0001")

# Beta grades 2 cases
for _ in range(2):
    at3.radio[0].set_value("f01")
    at3.radio[1].set_value("1"); at3.radio[2].set_value("2")
    at3.run()
    at3.button[-1].click().run()

beta_id = [r["reader_id"] for r in db.all_readers() if r["name"] == "drb"][0]
(ok if db.graded_count(beta_id) == 2 else bad)(f"drb has 2 grades (got {db.graded_count(beta_id)})")
(ok if db.graded_count(alpha_id) == 2 else bad)(f"dra count untouched ({db.graded_count(alpha_id)})")

# ---------- Test 8: Re-grading upserts ----------
print("\n=== Test 8: Re-grade is an upsert ===")
before = len(db.all_grades())
db.save_grade(alpha_id, 1, "VIS-xxxx_f02", "4", "4", 99, "yes", "regrade", 7.0)
after = len(db.all_grades())
(ok if before == after else bad)(f"no new row created on re-grade (before={before}, after={after})")
row = [g for g in db.all_grades() if g["reader_id"] == alpha_id and g["case_id"] == 1][0]
(ok if row["kl_right"] == "4" and row["notes"] == "regrade" else bad)("re-grade persisted new values")

# ---------- Test 9: Admin page ----------
print("\n=== Test 9: Admin page ===")
at_a = AppTest.from_file(ADMIN_PATH, default_timeout=15).run()
at_a.text_input[0].set_value("wrong"); at_a.button[0].click().run()
(ok if any("Wrong token" in e.value for e in at_a.error) else bad)("admin rejects wrong token")

at_a2 = AppTest.from_file(ADMIN_PATH, default_timeout=15).run()
at_a2.text_input[0].set_value("letmein"); at_a2.button[0].click().run()
if len(at_a2.metric) >= 3:
    vals = {m.label: m.value for m in at_a2.metric}
    ok(f"admin unlocked, metrics: {vals}")
else:
    bad(f"admin metrics missing after correct token; count={len(at_a2.metric)}")

# ---------- Test 10: Exports ----------
print("\n=== Test 10: Exports ===")
import subprocess
export_dir = tempfile.mkdtemp()
r = subprocess.run(
    [sys.executable, os.path.join(ROOT, "export.py"), export_dir],
    env={**os.environ, "KOA_DB": _tmp_db.name},
    capture_output=True, text=True,
)
if r.returncode != 0:
    bad(f"export.py failed: {r.stderr}")

expected = {"model_ready.csv", "publishable.csv",
            "reader_dra.csv", "reader_drb.csv"}
got = set(os.listdir(export_dir))
(ok if expected.issubset(got) else bad)(f"exports contain all expected files: {sorted(got)}")

with open(os.path.join(export_dir, "model_ready.csv"), encoding="utf-8") as f:
    rows = list(csv.DictReader(f))
graded_case_ids = {g["case_id"] for g in db.all_grades()}
(ok if len(rows) == len(graded_case_ids) else bad)(
    f"model_ready.csv rows ({len(rows)}) == distinct graded cases ({len(graded_case_ids)})"
)

first = next(r for r in rows if r["display_id"] == "Case 0001")
if first["dra_R"] == "4" and first["dra_L"] == "4":
    ok("Case 0001 reflects dra's re-grade (R=4, L=4) in model_ready")
else:
    bad(f"Case 0001 dra fields wrong: {first}")
(ok if first["consensus_R"] else bad)(f"consensus_R populated ({first['consensus_R']!r})")
(ok if first["n_readers"] in ("1", "2") else bad)(f"n_readers populated ({first['n_readers']!r})")

with open(os.path.join(export_dir, "publishable.csv"), encoding="utf-8") as f:
    pub = f.read()
(ok if "dra" not in pub and "drb" not in pub else bad)(
    "publishable.csv does not leak reader usernames"
)
(ok if "Viswanathan" not in pub else bad)("publishable.csv does not leak source name")

with open(os.path.join(export_dir, "reader_dra.csv"), encoding="utf-8") as f:
    j_rows = list(csv.DictReader(f))
(ok if len(j_rows) == 2 else bad)(f"dra CSV has {len(j_rows)} rows (expected 2)")
(ok if {r["display_id"] for r in j_rows} == {"Case 0001", "Case 0002"} else bad)(
    "dra CSV lists the cases they graded"
)

# Code-level: no source leak in UI
with open(APP_PATH, encoding="utf-8") as f:
    app_src = f.read()
(ok if "case['source']" not in app_src and 'case["source"]' not in app_src else bad)(
    "app.py never dereferences case['source']"
)

# ---------- Summary ----------
print("\n" + "=" * 60)
print(f"PASSED: {len(PASS)}   FAILED: {len(FAIL)}")
if FAIL:
    print("\nFailures:")
    for f in FAIL: print(f"  - {f}")
    sys.exit(1)
print("\nALL E2E TESTS PASSED")
