FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

# tzdata so the TZ env var (set in docker-compose.yml) actually resolves -
# Debian slim doesn't ship it, and without it every timestamp (alerts,
# logs, crop filenames) silently renders in UTC regardless of TZ.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY streaming/ ./streaming/

CMD ["python", "-m", "streaming.relay_server"]
