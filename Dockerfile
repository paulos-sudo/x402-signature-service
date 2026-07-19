FROM python:3.12-slim

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY agent-tool.json .

# Non-root; /data fuer die SQLite-DB (Volume/Disk mounten!)
RUN useradd -r -u 10001 appuser && mkdir -p /data && chown appuser /data
USER appuser

EXPOSE 8000

# Render/Railway injizieren PORT; lokal Fallback 8000.
# 1 Worker: SQLite + In-Prozess-Statuscache — fuer mehr Durchsatz DB nach Postgres heben.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --no-server-header
