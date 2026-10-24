"""Knee OA KL Grading — mobile-first Streamlit UI.

Env vars (image serving — first match wins):
  SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY + SUPABASE_BUCKET
    -> images fetched via signed URLs from a PRIVATE Supabase Storage bucket
  KOA_IMAGE_BASE_URL
    -> images fetched from this URL (public bucket or CDN)
  KOA_IMAGES (default: ./COHORT_VIS)
    -> images loaded from local filesystem (dev only)

Other env vars:
  KOA_PG_URL   Postgres URL (if unset -> local SQLite via KOA_DB)
  USERS        multi-line "username:password:role" entries for login
  ADMIN_TOKEN  token for the admin page

Data-safety rules enforced here:
  - "Save & Next" never advances the view unless save_grade() returned cleanly.
  - A failed save shows a loud banner and keeps the inputs on screen so the
    radiologist can tap Save again. db.save_grade() itself retries 4x before
    raising, so a transient DB blip is invisible.
"""
import json
import os
import time
import urllib.request

import streamlit as st

import db

st.set_page_config(
    page_title="Knee OA KL Grading",
    layout="centered",
    initial_sidebar_state="collapsed",
)

_DEFAULT_LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "COHORT_VIS")
LOCAL_BASE = os.environ.get("KOA_IMAGES", _DEFAULT_LOCAL)
IMAGE_BASE_URL = os.environ.get("KOA_IMAGE_BASE_URL", "").rstrip("/")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET", "koa-cohort")
_USE_SIGNED_URLS = bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)


# ---------- mobile-friendly CSS ----------
st.markdown(
    """
    <style>
      /* Prevent horizontal scroll on narrow screens */
      .main .block-container {max-width: 820px; padding-top: 1rem; padding-bottom: 4rem;}
      /* Bigger tap targets for radios / selectboxes */
      div[role="radiogroup"] label {
          padding: 10px 14px; margin-right: 6px; border: 1px solid #ddd;
          border-radius: 8px; min-width: 48px; text-align: center;
      }
      /* Full-width primary button */
      div.stButton > button[kind="primary"] {
          width: 100%; height: 52px; font-size: 18px; font-weight: 600;
      }
      /* Make the slider easier to grab on touchscreens */
      div.stSlider > div[data-baseweb="slider"] {padding: 14px 0;}
      /* Caption below progress bar — tighter spacing */
      .progress-caption {font-size: 0.9rem; color: #555; margin-top: -8px;}
      /* Make images never overflow */
      img {max-width: 100%; height: auto;}
      @media (max-width: 600px) {
          .main .block-container {padding: 0.5rem 0.75rem 4rem;}
          h1, h2, h3 {font-size: 1.25rem !important;}
      }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(ttl=3000, show_spinner=False)  # cache ~50 min; signed URL lives 1 h
def _signed_url(rel_path: str) -> str | None:
    """Fetch a time-limited signed URL for an object in the private bucket.

    Returns None on any failure so the caller can surface an 'image unavailable'
    message rather than crashing the grading flow.
    """
    endpoint = f"{SUPABASE_URL}/storage/v1/object/sign/{SUPABASE_BUCKET}/{rel_path.lstrip('/')}"
    req = urllib.request.Request(
        endpoint,
        method="POST",
        headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
        },
        data=json.dumps({"expiresIn": 3600}).encode("utf-8"),
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return f"{SUPABASE_URL}/storage/v1{data['signedURL']}"
    except Exception:
        return None


def _image_src(rel_path: str) -> str | None:
    if not rel_path:
        return None
    if _USE_SIGNED_URLS:
        return _signed_url(rel_path)
    if IMAGE_BASE_URL:
        return f"{IMAGE_BASE_URL}/{rel_path.lstrip('/')}"
    local = os.path.join(LOCAL_BASE, rel_path.replace("/", os.sep))
    return local if os.path.exists(local) else None


# ---------- auth ----------
# USERS secret format (one line per reader):
#   username:password[:role]
# Role is optional; defaults to "Radiologist".
def _parse_users():
    out = {}
    raw = os.environ.get("USERS", "").strip()
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":", 2)
        if len(parts) < 2:
            continue
        u, p = parts[0].strip(), parts[1].strip()
        role = parts[2].strip() if len(parts) >= 3 and parts[2].strip() else "Radiologist"
        if u and p:
            out[u] = (p, role)
    return out


USERS = _parse_users()


# ---------- login ----------
if "reader_id" not in st.session_state:
    st.title("Knee OA — KL Grading")
    st.write("Please sign in to begin or continue grading. You can close and come back anytime.")
    if not USERS:
        st.error(
            "No readers configured on this deployment. "
            "Set the USERS secret and reload."
        )
        st.stop()
    u = st.text_input("Username").strip()
    p = st.text_input("Password", type="password").strip()
    if st.button("Sign in", type="primary", disabled=not (u and p)):
        creds = USERS.get(u)
        if creds and creds[0] == p:
            _password, role = creds
            st.session_state.reader_id = db.get_or_create_reader(u, role)
            st.session_state.reader_name = u
            st.session_state.t0 = time.time()
            st.rerun()
        else:
            st.error("Wrong username or password.")
    st.stop()

rid = st.session_state.reader_id
total = db.total_cases()
done = db.graded_count(rid)

# ---------- header / progress ----------
st.markdown(f"**Grader:** {st.session_state.reader_name}")
st.progress(done / total if total else 0, text=f"Graded {done} of {total}")
if done >= total:
    st.success("You have graded every case. Thank you! You may close this window.")
    st.stop()

case = db.next_case(rid)
if case is None:
    st.success("All done. Thank you!")
    st.stop()

st.markdown(f"### {case['display_id']}")

# Show a toast-like confirmation of the previous save, if any
if st.session_state.pop("just_saved", False):
    st.success("Saved ✓", icon="✅")

# ---------- image + frame picker ----------
if case["kind"] == "single":
    src = _image_src(case["main_image"])
    if src:
        st.image(src, width="stretch")
    else:
        st.warning("Image unavailable — please skip or alert the admin.")
    chosen_frame = case["patient_id"]
    frame_ok = True
else:
    mon_src = _image_src(case["montage_image"])
    if mon_src:
        st.image(
            mon_src,
            width="stretch",
            caption="All frames for this patient — pick the AP (front) knee below",
        )
    else:
        st.warning("Montage unavailable — please skip or alert the admin.")

    raw = case["frames"] or ""
    frame_opts = [f for f in raw.split(";") if f]
    if frame_opts:
        # Radio renders as tappable chips thanks to the CSS above
        chosen_suffix = st.radio(
            "Which frame is the AP knee?",
            frame_opts,
            horizontal=True,
            index=None,  # force an explicit choice
            key=f"frame_{case['case_id']}",
        )
    else:
        chosen_suffix = None
    frame_ok = chosen_suffix is not None
    chosen_frame = (
        f"{case['patient_id']}_{chosen_suffix}" if chosen_suffix else case["patient_id"]
    )
    if chosen_suffix:
        fsrc = _image_src(f"images/{case['patient_id']}_{chosen_suffix}.png")
        if fsrc:
            st.image(fsrc, width="stretch", caption="Selected frame to grade")

# ---------- grading controls ----------
st.markdown("#### Your grading")

c1, c2 = st.columns(2)
kl_r = c1.radio(
    "RIGHT knee KL",
    ["0", "1", "2", "3", "4", "NA"],
    horizontal=True, index=None, key=f"klr_{case['case_id']}",
)
kl_l = c2.radio(
    "LEFT knee KL",
    ["0", "1", "2", "3", "4", "NA"],
    horizontal=True, index=None, key=f"kll_{case['case_id']}",
)

conf = st.slider("Confidence (0 = unsure, 100 = certain)", 0, 100, 70,
                 key=f"conf_{case['case_id']}")
usable = st.radio("Is this a gradable AP knee image?", ["yes", "no"],
                  horizontal=True, index=0, key=f"use_{case['case_id']}")
notes = st.text_input("Notes (optional)", key=f"note_{case['case_id']}")

# Guard: both KL fields and (for montage) a frame must be chosen
missing = []
if kl_r is None: missing.append("RIGHT KL")
if kl_l is None: missing.append("LEFT KL")
if not frame_ok: missing.append("the AP frame")
can_save = not missing

if missing:
    st.caption("Please choose: " + ", ".join(missing))

if st.button("Save & Next", type="primary", disabled=not can_save):
    secs = round(time.time() - st.session_state.get("t0", time.time()), 1)
    try:
        db.save_grade(rid, case["case_id"], chosen_frame,
                      kl_r, kl_l, conf, usable, notes, secs)
    except Exception as e:
        st.error(
            "⚠️ Could not save this grade (network/database issue). "
            "Your inputs are still on screen — please tap **Save & Next** again. "
            f"\n\nDetails: {type(e).__name__}: {e}"
        )
        st.stop()
    st.session_state.just_saved = True
    st.session_state.t0 = time.time()
    st.rerun()

st.caption(
    "Grades save instantly to the central database. Close the tab anytime — "
    "the next time you open this link and type the same name, you resume at the "
    "next ungraded case."
)
