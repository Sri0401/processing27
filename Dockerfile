# One image for every Python workload (ingest bridge, simulator, loaders, dbt, tests).
# Pinned to 3.12 because dbt does not yet support the newest Python releases.
FROM python:3.12.8-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src:/app/lambdas/ingest

WORKDIR /app

# psql is used to bootstrap managed PostgreSQL servers (scripts/bootstrap_remote_db.sh)
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .

# Run as non-root
RUN useradd --create-home --uid 10001 platform && chown -R platform /app
USER platform

CMD ["python", "-m", "smart_campus.bridge"]
