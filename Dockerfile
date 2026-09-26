FROM python:3.13-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md ./
COPY app ./app
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
  CMD python -c "import os,urllib.request; p=os.getenv('FEEDVANTA_BASE_PATH','').rstrip('/'); urllib.request.urlopen('http://127.0.0.1:8000'+p+'/health',timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
