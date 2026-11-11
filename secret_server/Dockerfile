# Build from the repository root:  docker build -f secret_server/Dockerfile .
FROM python:3.12-slim

WORKDIR /app
COPY secret_server/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY secret_server ./secret_server
COPY secret_server/docker-entrypoint.sh /usr/local/bin/vault-entrypoint
RUN chmod 0755 /usr/local/bin/vault-entrypoint

# Encrypted vault database on the /data volume. The master key is NOT stored here:
# it comes from SS_MASTER_KEY (AWS Secrets Manager on the EC2 deployment).
RUN useradd --system --uid 10001 app && mkdir -p /data && chown app /data
USER app
ENV SS_DB_PATH=/data/secret_server.db
EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8090/health')"

ENTRYPOINT ["vault-entrypoint"]
