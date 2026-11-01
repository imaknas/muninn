# Muninn in a container, for a NAS or a server: data in /data, listening on 0.0.0.0:18795 inside.
# The Claude CLI is not in the image: use an API key (Anthropic, or any OpenAI-compatible service), or Ollama on
# the host (base_url http://host.docker.internal:11434). See compose.yaml.

FROM node:22-slim AS web
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY src/muninn/adapters/web src/muninn/adapters/web
RUN npm run --silent build

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS wheel
WORKDIR /src
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src src
COPY --from=web /src/src/muninn/adapters/web/static/ src/muninn/adapters/web/static/
RUN uv build --wheel -o /dist

FROM python:3.13-slim
COPY --from=wheel /dist /dist
RUN pip install --no-cache-dir /dist/*.whl && rm -rf /dist && useradd --create-home --uid 1000 muninn \
    && mkdir /data && chown muninn /data
USER muninn
ENV MUNINN_DATA=/data MUNINN_HOST=0.0.0.0 MUNINN_SERVICE=1 PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 18795
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18795/healthz')"
# its own schedule unless config says otherwise; saving the setup page stops it, and the container restarts it
CMD ["sh", "-c", "[ -f /data/config.yaml ] || printf 'jobs: default\\n' > /data/config.yaml; muninn init && exec muninn serve"]
