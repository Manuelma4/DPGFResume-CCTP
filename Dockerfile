FROM node:24-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DPGF_ENVIRONMENT=production \
    DPGF_AUTH_REQUIRED=true

RUN groupadd --system moduo && useradd --system --gid moduo --home /app moduo
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY --from=frontend /build/frontend/dist ./frontend/dist
RUN mkdir -p /app/output && chown -R moduo:moduo /app

USER moduo
EXPOSE 8070
VOLUME ["/app/output"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8070/api/health', timeout=3)"

# The container port is published only on host loopback. Trust the forwarding
# headers sent by the Apache reverse proxy through Docker's bridge gateway.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8070", "--proxy-headers", "--forwarded-allow-ips=*"]
