FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY portal_server.py apps.json ./
COPY static ./static

RUN useradd --system --no-create-home portal
USER portal

ENV PORTAL_PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

CMD ["python", "portal_server.py"]
