FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gmp_assets ./gmp_assets
COPY run_server.py ./

# The SQLite database lives on the /data volume.
RUN useradd --system --uid 10001 app && mkdir -p /data && chown app /data
USER app
ENV GMP_DB_PATH=/data/gmp_assets.db
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"

CMD ["python", "run_server.py", "--host", "0.0.0.0", "--port", "8000"]
