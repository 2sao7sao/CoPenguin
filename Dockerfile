FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOST=0.0.0.0 \
    PORT=8787 \
    COPENGUIN_DATA_DIR=/data \
    COMPUTER_PROVIDER=dry-run

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install --no-cache-dir . \
    && addgroup --system copenguin \
    && adduser --system --ingroup copenguin copenguin \
    && mkdir -p /data \
    && chown copenguin:copenguin /data

USER copenguin
VOLUME ["/data"]
EXPOSE 8787

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=2)"]

CMD ["copenguin", "serve"]
