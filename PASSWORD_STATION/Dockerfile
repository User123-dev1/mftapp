FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pstation ./pstation
COPY run.py reset_admin.py ./

RUN useradd --system --uid 10001 app && mkdir -p /data && chown app /data
USER app
ENV PS_DB_PATH=/data/password_station.db
EXPOSE 8200

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8200/health')"

CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8200"]
