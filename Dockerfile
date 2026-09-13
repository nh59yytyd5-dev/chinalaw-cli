FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 chinalaw \
    && mkdir /data \
    && chown chinalaw:chinalaw /data

WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICES.md CHANGELOG.md ./
COPY src ./src
COPY data ./data
RUN python -m pip install --no-cache-dir ".[server]"

USER chinalaw
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD python -c "import os, urllib.request; from urllib.parse import urlsplit; r=urllib.request.Request('http://127.0.0.1:8765/healthz',headers={'Host':urlsplit(os.environ.get('CHINALAW_PUBLIC_URL','http://127.0.0.1:8765')).netloc}); urllib.request.urlopen(r,timeout=3).read()"
CMD ["chinalaw-server", "serve", "--server", "--host", "0.0.0.0", "--db", "/data/library.db", "--state-dir", "/data/server-state"]
