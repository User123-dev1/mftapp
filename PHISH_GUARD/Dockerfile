FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY phishguard ./phishguard
COPY run.py ./

# Database and copies of quarantined / reported messages (message_store/) live on the /data volume.
RUN useradd --system --uid 10001 app && mkdir -p /data && chown app /data
USER app
ENV PG_DB_PATH=/data/phishguard.db
EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8100/health')"

# Console only. The SMTP gateway mode is not used in this deployment.
CMD ["python", "run.py", "console", "--host", "0.0.0.0", "--port", "8100"]
