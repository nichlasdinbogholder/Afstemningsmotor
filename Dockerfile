# Én opskrift til webdel (api), worker og scheduler – de starter blot med hver sin kommando.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app
COPY scripts ./scripts

# Kør aldrig som root i containeren.
RUN useradd --create-home --uid 1000 afstemning && mkdir -p /app/logs && chown afstemning /app/logs
USER afstemning

CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
