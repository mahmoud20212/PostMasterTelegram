FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid 1000 \
       --create-home --shell /bin/sh appuser

COPY . .

USER appuser

CMD ["celery", "-A", "config", "worker", "--loglevel=INFO", "--pool=solo"]
