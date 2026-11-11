FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY run.py seed_demo.py ./

# Data (SQLite database, uploaded quotes, generated PDFs) lives on the /data volume.
RUN useradd --system --uid 10001 app && mkdir -p /data && chown app /data
USER app
ENV SD_PORT=8085 \
    SD_DATABASE_URL=sqlite:////data/servicedesk.db \
    SD_UPLOAD_DIR=/data/uploads
EXPOSE 8085

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8085/login')"

CMD ["python", "run.py", "--port", "8085"]
