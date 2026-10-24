---
title: Knee OA KL Grading
emoji: 🦴
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
short_description: KL-grading web app for the knee-OA cohort.
---

# Knee OA KL Grading — Web App

Radiologists log in with their name, grade one case at a time (KL 0–4 per knee,
confidence 0–100, usable yes/no), and the grade is saved to a hosted database.
Closing the browser and returning resumes at the next ungraded case. The
case-source (hospital) is stored in the DB for analysis but **never** shown
in the UI.

## Local development

```bash
pip install -r requirements.txt
# windows:  set KOA_IMAGES=D:\downloads\grading_app\COHORT_VIS
export KOA_IMAGES=/path/to/COHORT_VIS      # folder with images/ and patient_montages/
python load_cases.py                       # one time: loads 608 cases
python -m streamlit run app.py             # opens http://localhost:8501
```

## Hosted deployment

- **Easy path (recommended): [QUICK_DEPLOY.md](./QUICK_DEPLOY.md)** —
  Supabase for the DB, images shipped in the Space repo via Git LFS. Two
  services, two secrets, one `git push`.
- Longer path with Supabase Storage for images: [DEPLOY.md](./DEPLOY.md).

## Exports

- Live export of every CSV from the admin page (one zip, point-in-time).
- Scriptable export: `python export.py exports/` writes
  `reader_<name>.csv`, `model_ready.csv`, `publishable.csv`.

## Schema

```
readers(reader_id, name UNIQUE, seniority, created_at)
cases  (case_id, display_id UNIQUE, source[hidden], patient_id[hidden],
        kind, main_image, montage_image, frames)
grades (grade_id, reader_id, case_id, chosen_frame,
        kl_right, kl_left, confidence, usable, notes,
        seconds_spent, timestamp,
        UNIQUE(reader_id, case_id))
```

Re-grading a case upserts the same `grades` row — never a duplicate.
