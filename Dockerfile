FROM python:3.14-slim@sha256:930e1abba7c4e8f96ed93e8012c35d174d7732752731bbd5e8e757d7ec6f3966

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    BOBAOPS_DATA_DIR=/app/data/private
WORKDIR /app

COPY requirements.lock ./
RUN python -m pip install --no-cache-dir -r requirements.lock

RUN groupadd --gid 10001 bobaops \
    && useradd --uid 10001 --gid bobaops --no-create-home bobaops \
    && mkdir -p /app/data/private/sales_images \
    && chown -R bobaops:bobaops /app/data

COPY app.py alembic.ini ./
COPY bobaops/ ./bobaops/
COPY frontend/ ./frontend/
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/
RUN chmod -R a+rX /app/bobaops /app/frontend /app/migrations /app/scripts \
    && chmod 644 /app/app.py /app/alembic.ini

USER bobaops
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=5)"]
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
