# HF Spaces (Docker SDK): container must listen on port 7860 and run as uid 1000.
FROM python:3.11-slim

RUN useradd -m -u 1000 user
WORKDIR /app

# Install deps first (better layer caching)
COPY --chown=user:user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Then the app
COPY --chown=user:user . .

USER user
EXPOSE 7860

# HF Spaces secrets (KOA_PG_URL, ADMIN_TOKEN) are injected as env vars at runtime
# KOA_IMAGES defaults to ./COHORT_VIS inside the image, which is where our LFS
# images land.
CMD ["python", "-m", "streamlit", "run", "app.py", \
     "--server.port=7860", "--server.address=0.0.0.0", \
     "--server.headless=true", "--browser.gatherUsageStats=false"]
