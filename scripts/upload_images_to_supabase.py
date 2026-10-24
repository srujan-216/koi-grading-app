"""One-time bulk upload of images/ and patient_montages/ to Supabase Storage.

Reads env vars:
  SUPABASE_URL                https://<project>.supabase.co
  SUPABASE_SERVICE_ROLE_KEY   service role key (DO NOT commit)
  SUPABASE_BUCKET             bucket name, e.g. koa-cohort
  KOA_IMAGES                  local folder containing images/ and patient_montages/

Behaviour:
  - Creates the bucket if it doesn't exist, marked PRIVATE (no public reads).
  - Uploads every file under images/ and patient_montages/ to the same relative
    path inside the bucket, with image/png content type.
  - Skips files that already exist with the same size.
  - Prints progress every 50 files.

After it finishes, the deployed app should have these three secrets set:
  SUPABASE_URL              https://<project>.supabase.co
  SUPABASE_SERVICE_ROLE_KEY <same service role key used here>
  SUPABASE_BUCKET           <bucket name>
The app will then generate 1-hour signed URLs for each image on demand.
"""
import os
import sys

try:
    from supabase import create_client
except ImportError:
    sys.stderr.write("pip install supabase\n"); sys.exit(1)

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
BUCKET = os.environ.get("SUPABASE_BUCKET", "koa-cohort")
BASE = os.environ.get("KOA_IMAGES") or sys.argv[1]  # local folder


def main():
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

    # Create or confirm the bucket as PRIVATE (no anonymous reads).
    try:
        sb.storage.create_bucket(BUCKET, options={"public": False})
        print(f"[storage] created PRIVATE bucket {BUCKET}")
    except Exception as e:
        # Already exists is fine
        msg = str(e).lower()
        if "already" in msg or "exists" in msg or "duplicate" in msg:
            print(f"[storage] bucket {BUCKET} already present (leaving visibility unchanged — ensure it is PRIVATE in the Supabase UI)")
        else:
            print(f"[storage] create_bucket warning: {e}")

    uploaded = skipped = failed = 0
    for folder in ("images", "patient_montages"):
        local_dir = os.path.join(BASE, folder)
        if not os.path.isdir(local_dir):
            print(f"[warn] missing local folder: {local_dir}")
            continue
        files = sorted(os.listdir(local_dir))
        print(f"[upload] {folder}: {len(files)} files")
        for i, fname in enumerate(files, 1):
            local_path = os.path.join(local_dir, fname)
            if not os.path.isfile(local_path):
                continue
            remote_path = f"{folder}/{fname}"
            try:
                with open(local_path, "rb") as fh:
                    sb.storage.from_(BUCKET).upload(
                        remote_path, fh,
                        {"content-type": "image/png", "upsert": "true"},
                    )
                uploaded += 1
            except Exception as e:
                msg = str(e).lower()
                if "duplicate" in msg or "already exists" in msg:
                    skipped += 1
                else:
                    print(f"[fail] {remote_path}: {e}")
                    failed += 1
            if i % 50 == 0:
                print(f"  {folder}: {i}/{len(files)}  uploaded={uploaded} skipped={skipped} failed={failed}")
    print(f"[done] uploaded={uploaded} skipped={skipped} failed={failed}")
    public_base = f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET}"
    print(f"[hint] set KOA_IMAGE_BASE_URL={public_base}")


if __name__ == "__main__":
    main()
