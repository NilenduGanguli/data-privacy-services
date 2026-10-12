# Data Privacy Services — application image (multi-stage).
# Stage 1 compiles the console; stage 2 is the lean Python runtime with only the built dist.
#
#   docker build -t data-privacy-services:local .                       # what compose builds
#   docker build --platform linux/amd64 -t <registry>/data-privacy-services:<semver> .
#   docker build --build-arg WITH_NER=1 ...                               # + Presidio and spaCy

# ---------------------------------------------------------------- stage 1: console
FROM node:22-slim AS frontend
WORKDIR /ui
# Dependency layer (cached unless the manifest changes). The lockfile is committed, so `npm ci`
# only: a silent fallback to `npm install` would build with unpinned versions.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------- stage 2: runtime
FROM python:3.12-slim
LABEL org.opencontainers.image.title="data-privacy-services" \
      org.opencontainers.image.description="Data Privacy Services: Layer 1 API and Privacy Console"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# uv pinned to an exact release (never :latest).
COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml README.md ./
COPY dps ./dps
# Editable on purpose: dps/app.py serves the console from <repo root>/frontend/dist, i.e.
# /app/frontend/dist; a regular install would look under site-packages.
# [fpe] is MIT-licensed fastfpe; FF1 is still gated at runtime by DPS_FPE_ENABLED (spec D8).
# [db] (asyncpg) and [connectors] (sqlglot, pyarrow, boto3) are the job platform (M2): app, worker
# and migrate run this one image. [docs] and [tabular] arrive with M1/M5 (contract §11.1); until
# then uv notes that the package has no such extra and installs the rest.
# The [ner] extra (Presidio + spaCy models) is deliberately not baked in: pass --build-arg WITH_NER=1.
# Without it, names in free text are found by their cues only, and reports state that gap
# (report.verification.coverage, status "partial").
ARG WITH_NER=0
RUN if [ "$WITH_NER" = "1" ]; then \
      uv pip install --system --no-cache -e ".[fpe,ner,docs,db,connectors,tabular]" \
        && python -m spacy download en_core_web_sm; \
    else \
      uv pip install --system --no-cache -e ".[fpe,docs,db,connectors,tabular]"; \
    fi
COPY --from=frontend /ui/dist ./frontend/dist
# tools/: the seed-sources one-shot and the operator tools (seed, bench, demo, reveal) run in it.
COPY tools ./tools
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
# /app/var holds the local artefact and export volumes; named volumes inherit this ownership.
RUN chmod 0755 /usr/local/bin/entrypoint.sh \
    && useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/var/artefacts /app/var/exports \
    && chown -R appuser /app/var
# Runs as a non-root user; /app stays root-owned and read-only to the process (except /app/var).
USER 10001
EXPOSE 8400
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8400/health', timeout=3).status==200 else 1)"
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
# --no-access-log: uvicorn's access log is stdlib logging and bypasses the structlog allow-list
# (it would print every request path); requests are counted in /metrics and audited instead.
CMD ["uvicorn", "dps.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8400", "--no-access-log"]
