# Chromium needs system libraries that Railway's Nixpacks autodetection does
# not install, so build explicitly and let Playwright pull its own deps.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY scripts/requirements.txt scripts/requirements.txt
RUN pip install --no-cache-dir -r scripts/requirements.txt \
    && python -m playwright install --with-deps chromium

COPY . .

# Defaults for Railway; the volume is mounted at /data.
ENV SCRAPER_OUTPUT_DIR=/data/ics \
    SCRAPER_BROWSER_PROFILE=/data/browser-profile \
    GIT_WORK_DIR=/data/repo \
    PORT=8080

EXPOSE 8080
CMD ["python", "scripts/service.py"]
