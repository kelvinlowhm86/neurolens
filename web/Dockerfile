# syntax=docker/dockerfile:1.7
# NeuroLens web tier (Flask + gunicorn). Build from the repo root:  docker build -f web/Dockerfile .
FROM python:3.11-slim AS build
WORKDIR /w
COPY web/requirements.txt .
COPY packages/common /w/common
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt /w/common

FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    STATIC_DIR=/app/static MIGRATIONS_DIR=/app/migrations SAMPLES_JSON=/app/data/samples.json
RUN useradd -m -u 10001 app
COPY --from=build /wheels /wheels
RUN pip install /wheels/* && rm -rf /wheels
WORKDIR /app
COPY web/neurolens_web ./neurolens_web
COPY web/static ./static
COPY migrations ./migrations
COPY data/samples.json ./data/samples.json
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
# keep-alive above the ALB idle timeout (60 s), or the ALB reuses connections gunicorn already closed (502s)
CMD ["gunicorn", "-b", "0.0.0.0:8000", "-w", "2", "--threads", "4", "--keep-alive", "75", "--access-logfile", "-", "neurolens_web:create_app()"]
