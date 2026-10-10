FROM node:22-alpine AS css-builder

WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY app/templates ./app/templates
COPY app/static/input.css ./app/static/input.css
RUN npm run build:css

FROM python:3.13-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md ./
COPY app ./app
COPY tool_host ./tool_host
COPY --from=css-builder /build/app/static/app.css ./app/static/app.css
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FEEDVANTA_DB=/data/feedvanta.db

RUN groupadd --gid 10001 feedvanta \
    && useradd --uid 10001 --gid feedvanta --create-home --shell /usr/sbin/nologin feedvanta \
    && mkdir -p /data \
    && chown feedvanta:feedvanta /data

WORKDIR /app
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/*.whl \
    && rm -rf /wheels

USER feedvanta
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3)"

CMD ["uvicorn", "tool_host.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
