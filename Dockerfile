# syntax=docker/dockerfile:1

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Install basic packages (Playwright will install browser deps itself)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl unzip \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt \
  && python -m playwright install --with-deps chromium

COPY scraper.py ./
COPY config.json ./
COPY start.sh ./
COPY app.py ./

RUN chmod +x /app/start.sh && useradd -m -u 1000 appuser && chown -R appuser:appuser /app /ms-playwright
USER appuser

CMD ["/app/start.sh"]


