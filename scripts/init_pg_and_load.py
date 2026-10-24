"""Initialise the hosted Postgres: apply schema and load the 608 cases.

Requires:
  KOA_PG_URL   pooled Postgres URL from Supabase (port 6543, sslmode=require)
  KOA_IMAGES   local folder containing the cohort_manifest.csv

Safe to re-run: load_cases is idempotent — it exits as a no-op if cases exist.
"""
import os
import sys

if not os.environ.get("KOA_PG_URL"):
    sys.stderr.write("KOA_PG_URL must be set (pooled Supabase Postgres URL)\n")
    sys.exit(1)

# Add project root to path when running from scripts/
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import db           # noqa: E402  — triggers schema install on first conn()
import load_cases   # noqa: E402

print(f"[init] backend={db.BACKEND}")
# Trigger schema install.
c = db.conn(); db.close(c)
print("[init] schema installed")
load_cases.run()
print("[init] done")
