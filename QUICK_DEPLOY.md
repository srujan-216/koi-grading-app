# Quick Deploy (the easy path)

**Time:** ~30 min first-time. **Services:** 2 (Supabase for the DB, Hugging Face
for the app). **Data persistence:** full — grades live in Postgres with daily
backups, never on the ephemeral Space.

Why this is the easy path: images ship inside the Space's git repo via Git LFS,
so you skip the Supabase Storage upload step entirely. Fewer URLs, fewer tokens,
less that can go wrong.

---

## One-time: on your laptop

### 1. Supabase (5 min) — only for the database

1. https://supabase.com → **New project** → pick the region nearest to the
   radiologists. Set a strong DB password.
2. When it's ready: **Project Settings → Database → Connection string →
   Transaction (port 6543)**. Copy the URL and append `?sslmode=require`.
   This is `KOA_PG_URL`. Save it somewhere.

That's all you need from Supabase. **Don't** make a Storage bucket.

### 2. Hugging Face (5 min) — the public link

1. https://huggingface.co → **New Space** → SDK **Streamlit**, Hardware **CPU basic (free)**, Visibility **Public**.
2. **Settings → Variables and secrets → New secret** (twice):

   | Name | Value |
   |---|---|
   | `KOA_PG_URL` | the URL from step 1 |
   | `ADMIN_TOKEN` | any long random string you keep private |

### 3. Push the code + images (10 min)

```powershell
cd D:\downloads\grading_app

# 3a. install LFS once (if you don't have it)
git lfs install

# 3b. clone your empty Space
git clone https://huggingface.co/spaces/<you>/<space-name> hf-space
cd hf-space

# 3c. copy everything from the project into the Space working tree
Copy-Item -Recurse -Force ..\* .

# 3d. tell git which files to send through LFS (large/binary)
git lfs track "*.png"
git add .gitattributes

# 3e. first commit (images included — this upload is ~430 MB, one time only)
git add .
git commit -m "initial deploy"
git push
```

The Space will show "Building…" for ~2 min, then "Running". Visit:
```
https://huggingface.co/spaces/<you>/<space-name>
```

### 4. Load the 608 cases (1 min)

```powershell
$env:KOA_PG_URL = "<the Supabase URL from step 1>"
python scripts/init_pg_and_load.py
```

Expected output:
```
[init] backend=postgres
[init] schema installed
[load_cases] inserted 608 cases, total in DB: 608
[init] done
```

Open the Space URL — the radiologists can now log in and grade.

---

## That's it

Share the Space URL with the radiologists. Each one:
- Opens the link on their phone or laptop (mobile-friendly).
- Types their name, picks their role, starts grading.
- Can close the tab and reopen later — resumes at the next ungraded case.

The three CSV exports (`model_ready.csv`, per-reader, publishable) are one click
away: visit `https://<your-space-url>/Admin`, type the `ADMIN_TOKEN`, press
**Download EVERYTHING**.

---

## Updating later

Any code change:
```powershell
cd hf-space
Copy-Item -Recurse -Force D:\downloads\grading_app\app.py .
Copy-Item -Recurse -Force D:\downloads\grading_app\db.py .
# ... whichever files changed
git add -A ; git commit -m "update" ; git push
```
Space rebuilds in ~15 s. **Database untouched. Grades persist.**

---

## Backups (how we make sure no grade is ever lost)

Four layers, strongest to weakest:

1. **Every save is an atomic upsert with retry.** `db.save_grade` tries up to
   4 times before failing. On a true failure, the UI shows a loud banner and
   does **not** advance — the radiologist taps Save again. There is no silent
   data loss path.
2. **Supabase has automatic point-in-time backups** (free tier: 7-day window).
   Supabase dashboard → **Database → Backups**.
3. **One-click admin export.** `/Admin` → **Download EVERYTHING** zips the
   whole DB (per-reader CSVs + model-ready wide table + publishable table +
   raw tables). Do this once a week while grading is active.
4. **CLI export** for the moment grading finishes:
   ```powershell
   $env:KOA_PG_URL = "<...>"
   python export.py exports/
   ```
   Writes `reader_<name>.csv`, `model_ready.csv`, `publishable.csv`.
   `model_ready.csv` is the one you feed to the model.

---

## What `model_ready.csv` looks like

One row per case. Columns:

```
display_id, source, patient_id, chosen_frame,
<reader1>_R, <reader1>_L, <reader1>_conf,
<reader2>_R, <reader2>_L, <reader2>_conf,
...
consensus_R, consensus_L, n_readers
```

- `chosen_frame` names the exact PNG the readers graded (e.g. `VIS-0336_f02`).
- `consensus_R` / `consensus_L` is the plurality vote across readers (ties
  broken by whichever grade appeared first).
- `n_readers` lets you filter to rows that have enough signal (e.g. `n_readers >= 3`).

Feed this straight to training.

---

## Troubleshooting

| What you see | Fix |
|---|---|
| HF Space build fails on `psycopg2-binary` | HF already supports it on CPU basic. Make sure `requirements.txt` is at the Space repo root. |
| "too many connections" error in logs | You pasted the direct URL (port 5432) instead of the pooled one (6543). Repaste into the Space secret and restart. |
| Images show as broken icons | You didn't `git lfs track "*.png"` before the first push, so the Space has 1 kB LFS pointers instead of real images. Re-run step 3d, `git add --renormalize .`, commit and push. |
| Save button spins / "Could not save this grade" banner | Transient DB blip. The grade is on screen — the radiologist taps Save again. No data lost. If it keeps happening, check the Supabase project status page. |

---

If you prefer the fuller walkthrough with Supabase Storage (public bucket of
images, no Git LFS), see `DEPLOY.md` — same code, more steps.
