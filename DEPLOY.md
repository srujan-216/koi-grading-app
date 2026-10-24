# Deploy — Supabase + Hugging Face Spaces

End-to-end walkthrough for publishing the grading app at one public URL,
backed by a hosted Postgres and Supabase Storage for the images. Everything
below stays within free tiers.

Rough time budget: ~45 minutes the first time, ~2 minutes for a redeploy.

---

## Step 1 — Create the Supabase project

1. Sign in at https://supabase.com and create a new project. Pick the region
   geographically nearest to your radiologists.
2. Set a strong database password; **save it** — you'll paste it into the
   connection string below.
3. Wait for provisioning to finish (~2 minutes).

### 1a. Get the pooled Postgres URL

- In the project: **Project Settings → Database → Connection string → URI**.
- Copy the **Transaction** mode string (port **6543**, not 5432). It looks like:
  ```
  postgresql://postgres.<proj>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
  ```
- Append `?sslmode=require` at the end.
- This is `KOA_PG_URL`. Store it somewhere safe for the Space secret.

> Why pooled, not direct? Streamlit opens a connection per interaction; the
> transaction pooler (PgBouncer) is what you want for lots of short connections.

### 1b. Create the Storage bucket

- **Storage → Create bucket →** name it `koa-cohort`, mark **Public**. (Public
  read is fine because the filenames are neutral — VIS-xxxx — and contain no
  patient info. If you prefer a signed-URL flow instead, see "Alternative:
  signed URLs" at the bottom.)
- Grab the project reference from the top of **Settings → API**. Note the
  **service_role** key — needed only by the one-time upload script, never by
  the app.

Your image base URL will be:
```
https://<project>.supabase.co/storage/v1/object/public/koa-cohort
```
This is `KOA_IMAGE_BASE_URL`.

---

## Step 2 — Load the data (one time)

On your laptop, from the project folder:

```powershell
# install deps
pip install -r requirements.txt

# fill in a local .env file (don't commit it) with:
#   KOA_PG_URL=<pooled URL from 1a>
#   KOA_IMAGES=D:\downloads\grading_app\COHORT_VIS
#   SUPABASE_URL=https://<proj>.supabase.co
#   SUPABASE_SERVICE_ROLE_KEY=<service_role>
#   SUPABASE_BUCKET=koa-cohort
# then export them in the current shell. PowerShell:
#   Get-Content .env | ForEach-Object { if ($_ -match '^(\w+)=(.*)$') { [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2]) } }

# 2a. Apply schema and insert the 608 cases
python scripts/init_pg_and_load.py

# 2b. Upload the ~1,760 PNGs to Supabase Storage
python scripts/upload_images_to_supabase.py
```

The uploader prints progress every 50 files, skips duplicates on re-run, and
ends with the exact `KOA_IMAGE_BASE_URL` to use.

Verify in the Supabase dashboard:
- **Database → Table editor →** 608 rows in `cases`, 0 rows in `grades`.
- **Storage → koa-cohort →** `images/` and `patient_montages/` populated.

---

## Step 3 — Create the Hugging Face Space

1. Sign in at https://huggingface.co and create a **new Space**:
   - SDK: **Streamlit**
   - Visibility: **Public** (the URL you share)
   - Hardware: **CPU basic (free)**
2. Clone the Space repo locally:
   ```
   git clone https://huggingface.co/spaces/<you>/<space-name> hf-space
   ```
3. Copy the app files into it (everything **except** the images folder):
   ```
   app.py  db.py  export.py  load_cases.py
   pages/  scripts/  tests/
   requirements.txt  README.md  DEPLOY.md
   .streamlit/  .gitignore
   ```
   The `COHORT_VIS/` folder stays out — images live in Supabase Storage.
4. In the Space UI, **Settings → Variables and secrets**, add:
   | Name | Value |
   |---|---|
   | `KOA_PG_URL` | the pooled Postgres URL from step 1a |
   | `KOA_IMAGE_BASE_URL` | the public bucket URL from step 1b |
   | `ADMIN_TOKEN` | a long random string you keep private |

   > Mark `KOA_PG_URL` and `ADMIN_TOKEN` as **Secrets** (not plain Variables)
   > so they aren't exposed in the Space's rebuild logs.

5. Push:
   ```
   cd hf-space
   git add -A
   git commit -m "initial deploy"
   git push
   ```
   The Space rebuilds (~2 minutes). When it's green, the public URL is:
   ```
   https://huggingface.co/spaces/<you>/<space-name>
   ```

---

## Step 4 — Give the URL to the radiologists

Share the Space URL. Each radiologist:
1. Opens the link.
2. Types their full name + role.
3. Grades. Closes the tab whenever. Reopens — resumes at the next case.

**No setup on their end.**

---

## Redeploy / update the app

Any time you change code locally:

```
cd hf-space
# replace the files, then
git add -A
git commit -m "update: <what changed>"
git push
```
HF rebuilds. The database is unaffected — grades persist across redeploys.

If you only changed `app.py`, pushing just `app.py` is enough; the Space
re-boots in ~15 seconds.

---

## Backups

The hosted Postgres is **not ephemeral**. Supabase keeps automatic point-in-time
backups on the free tier (7-day window). To check / restore: project
**Database → Backups**.

Belt-and-braces on-demand backups:

- **From the Admin page** (visit `/Admin` on your Space, enter `ADMIN_TOKEN`):
  "Download EVERYTHING" produces a zip with every per-reader CSV, the
  model-ready wide table, the publishable table, and a raw dump of `readers`
  and `grades`. Point-in-time snapshot of the live DB.
- **From the command line**:
  ```
  KOA_PG_URL=<pooled URL> python export.py exports/
  ```
  Writes the same CSVs to `exports/`.
- **pg_dump** of the whole database:
  ```
  pg_dump "<pooled URL>" --no-owner --no-privileges > backup.sql
  ```

Recommended cadence: trigger "Download EVERYTHING" once a week while grading
is active; keep the zips in your own cloud.

---

## Running the exports after grading completes

```
# anywhere, with KOA_PG_URL set to the hosted DB
python export.py ./exports
```
produces:
- `exports/reader_<name>.csv` — one per radiologist
- `exports/model_ready.csv` — wide table (one row per case, all readers' grades + consensus) for model training
- `exports/publishable.csv` — anonymised, centre-labelled, safe to publish

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| App boots but every image is a broken icon | `KOA_IMAGE_BASE_URL` is unset or the bucket isn't public. Re-check the URL by pasting `<URL>/images/VIS-0001_f01.png` in a browser. |
| "psycopg2 OperationalError … too many connections" | You're using the direct port (5432). Switch to the **pooled** URL on port 6543. |
| App shows an older progress bar than the DB | Streamlit caches in session; a tab reload fixes it. The DB is always correct. |
| Two radiologists with the same name sign in | They'll share a `reader_id`. Agree on name conventions ("Dr. A. Kumar" vs "Dr. Kumar") beforehand. |
| HF Space goes to sleep | Public Spaces on CPU basic don't sleep in practice; the first request after inactivity may be ~5s slow. |
| Need to restart grading from scratch for a reader | In Supabase SQL editor: `DELETE FROM grades WHERE reader_id = <rid>;` |

---

## Alternative: signed URLs (if you can't make the bucket public)

Keep the bucket private, swap the `_image_src` function in `app.py` for one
that calls `supabase.storage.from_(bucket).create_signed_url(path, 3600)`.
Cache the URLs for the session. Slightly more code, same radiologist UX.
Not necessary for this cohort (neutral filenames, no PHI leaks).
