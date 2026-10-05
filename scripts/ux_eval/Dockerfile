FROM node:24-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv ca-certificates git ripgrep \
    && rm -rf /var/lib/apt/lists/*
RUN npm install --global @deepseek-ai/dsh@0.1.0-rc.8
# DSH rc.8 still calls HMR.registerConfig, which HMR 1.0.19 removed.
# Match the working host Harness's pure-JS HMR/loader pair explicitly.
RUN cd /tmp \
    && npm pack --quiet @deepseek-ai/cordis-plugin-hmr@1.0.16 \
    && tar -xzf deepseek-ai-cordis-plugin-hmr-1.0.16.tgz --strip-components=1 \
       -C /usr/local/lib/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/cordis-plugin-hmr \
    && npm pack --quiet @deepseek-ai/cordis-plugin-loader@1.0.2 \
    && tar -xzf deepseek-ai-cordis-plugin-loader-1.0.2.tgz --strip-components=1 \
       -C /usr/local/lib/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/cordis-plugin-loader \
    && rm /tmp/deepseek-ai-cordis-plugin-*.tgz
RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml README.md LICENSE NOTICES.md CHANGELOG.md /build/
COPY src /build/src
COPY data /build/data
RUN pip install --no-cache-dir '/build[server,dev]'
RUN useradd --create-home --uid 10001 evaluator && mkdir /work && chown evaluator /work
USER evaluator
ENV DSH_TELEMETRY_DISABLED=1 DSH_PERMISSION_MODE=danger-full-access
WORKDIR /work
CMD ["bash"]
