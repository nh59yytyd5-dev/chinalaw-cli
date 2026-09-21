FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 chinalaw \
    && mkdir /data \
    && chown chinalaw:chinalaw /data

WORKDIR /app
# hatchling 构建 wheel 时需要 src/chinalaw 与 shared-data 里的 data/ 都在场，
# 因此无法把「先装依赖、后 COPY src」拆成两层。这里先从 pyproject 抽出 server
# extra 的依赖清单单独安装（复用层缓存，源码改动不重新拉依赖），再 COPY 源码做正式安装。
COPY pyproject.toml README.md LICENSE NOTICES.md CHANGELOG.md ./
RUN python -c 'import tomllib; p = tomllib.load(open("pyproject.toml", "rb"))["project"]; print(chr(10).join(list(p.get("dependencies", [])) + list(p["optional-dependencies"]["server"])))' > /tmp/server-requirements.txt \
    && python -m pip install --no-cache-dir -r /tmp/server-requirements.txt \
    && rm -f /tmp/server-requirements.txt
COPY src ./src
COPY data ./data
RUN python -m pip install --no-cache-dir ".[server]"

USER chinalaw
# 服务参数默认值：init / password / serve 都读这些变量，compose 与手工 docker run 不必重复传参。
ENV CHINALAW_DB=/data/library.db CHINALAW_STATE_DIR=/data/server-state CHINALAW_HOST=0.0.0.0
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD python -c "import os, urllib.request; from urllib.parse import urlsplit; r=urllib.request.Request('http://127.0.0.1:8765/healthz',headers={'Host':urlsplit(os.environ.get('CHINALAW_PUBLIC_URL','http://127.0.0.1:8765')).netloc}); urllib.request.urlopen(r,timeout=3).read()"
CMD ["chinalaw-server", "serve", "--server"]
