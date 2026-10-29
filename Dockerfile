FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY requirements-postgres.txt .
# No --upgrade: retain installed, compatible application dependencies.
RUN pip install --no-cache-dir -r requirements-postgres.txt \
    && python -c "import psycopg"

RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid 1000 \
       --create-home --shell /bin/sh appuser

COPY . .

# Host logs are excluded; create only the writable runtime log directory.
RUN mkdir -p /app/logs && chown appuser:appuser /app/logs

USER appuser

CMD ["celery", "-A", "config", "worker", "--loglevel=INFO", "--pool=solo"]
