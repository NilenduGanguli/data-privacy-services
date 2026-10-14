# Data Privacy Services — application image (multi-stage).
# Stage 1 compiles the console; stage 2 is the lean Python runtime with only the built dist.
#
#   docker build -t data-privacy-services:local .                       # what compose builds
#   docker build --platform linux/amd64 -t <registry>/data-privacy-services:<semver> .
#   docker build --build-arg WITH_NER=1 ...                               # + Presidio and spaCy
#   docker build --build-arg EXTRAS=synth-ml ...                          # + opt-in extras (bank side)
#
# One image, three roles (app, worker, migrate). It runs as uid 10001 (compose) or as any UID whose
# group is 0 (OpenShift's restricted SCC), with a read-only root filesystem: the process writes only
# to /tmp (HOME; a tmpfs/emptyDir) and to the artefact and export volumes under /app/var.

# ---------------------------------------------------------------- stage 1: console
# Built on the build host's own platform: the dist is plain JS/CSS, so a --platform linux/amd64
# build on an arm64 Mac never runs npm under emulation.
FROM --platform=$BUILDPLATFORM node:22-slim AS frontend
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
# Local OCR (M1, spec §6 "local Tesseract when unset"): with DPS_OCR_PROVIDER=auto (this image's
# default) scanned pages go to the tesseract CLI unless an Azure Read endpoint and key are
# configured; compose's docs profile selects azure (the Read v3.2 mock). English data only
# (tesseract-ocr-eng); add tesseract-ocr-<lang> packages and DPS_TESSERACT_LANGS for others.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
ENV DPS_OCR_PROVIDER=auto
# uv pinned to an exact release (never :latest).
COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml README.md ./
COPY dps ./dps
# Editable on purpose: dps/app.py serves the console from <repo root>/frontend/dist, i.e.
# /app/frontend/dist; a regular install would look under site-packages.
# [fpe] is MIT-licensed fastfpe; FF1 is still gated at runtime by DPS_FPE_ENABLED (spec D8).
# [docs] (M1) is required by the app: the documents routes import the pipeline at startup.
# pypdfium2's manylinux wheel bundles PDFium; the other document libraries ship wheels too.
# [db] (asyncpg) and [connectors] (sqlglot, pyarrow, boto3, oracledb, pymongo) are the job platform
# (M2, M3): app, worker and migrate run this one image. [tabular] (M5: numpy + scipy) runs the
# Gaussian-copula engine and the fidelity/privacy metrics (contract §11.1).
# The [ner] extra (Presidio + spaCy models) is deliberately not baked in: pass --build-arg WITH_NER=1.
# Without it, names in free text are found by their cues only, and reports state that gap
# (report.verification.coverage, status "partial").
# EXTRAS adds opt-in extras to either variant, e.g. --build-arg EXTRAS=synth-ml for a bank-built
# image with MOSTLY AI after its licence and size review (torch ≈ 2 GB; never in the default
# image); without it options.tabular="mostly" is refused with tabular_invalid. Extra names only:
# lower-case letters, digits, '-' and ',' (anything else stops the build).
ARG WITH_NER=0
ARG EXTRAS=""
RUN case "$EXTRAS" in *[!a-z0-9,-]*) echo "EXTRAS: lower-case extra names separated by commas" >&2; exit 2;; esac \
    && if [ "$WITH_NER" = "1" ]; then \
      uv pip install --system --no-cache -e ".[fpe,ner,docs,db,connectors,tabular${EXTRAS:+,$EXTRAS}]" \
        && python -m spacy download en_core_web_sm; \
    else \
      uv pip install --system --no-cache -e ".[fpe,docs,db,connectors,tabular${EXTRAS:+,$EXTRAS}]"; \
    fi
COPY --from=frontend /ui/dist ./frontend/dist
# tools/: the seed-sources one-shot and the operator tools (seed, bench, demo, reveal) run in it.
COPY tools ./tools
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
# Arbitrary UID (OpenShift runs the image as a random UID in group 0) and a read-only root:
# - the image user 10001 has group 0 as its primary group, so compose's processes create group-0
#   files like OpenShift's do;
# - /app/var (the local artefact and export volumes; named volumes and emptyDirs inherit this
#   ownership and mode) belongs to uid 10001 and group 0, writable by both, and its directories
#   are setgid: what any UID creates in them stays group 0. With the entrypoint's umask 0002 new
#   directories are 2775, and the object store creates every artefact and export object 0660
#   (dps/storage.py OBJECT_MODE: owner and group, never other users), so a later UID (a redeploy,
#   another namespace UID) reads, replaces and adds to what the previous one stored;
# - the code under /app is readable by group 0 and writable by nobody but root, whatever the
#   build context's file modes were (a 664 file would otherwise be writable by every OpenShift
#   UID);
# - HOME=/tmp: libraries that write under ~ write to the /tmp scratch mount, never the root fs.
RUN chmod 0755 /usr/local/bin/entrypoint.sh \
    && useradd --create-home --uid 10001 --gid 0 appuser \
    && mkdir -p /app/var/artefacts /app/var/exports \
    && chown -R 10001:0 /app/var \
    && chgrp -R 0 /app && chmod -R g+rX,g-w /app && chmod -R g=u /app/var \
    && find /app/var -type d -exec chmod g+s {} +
ENV HOME=/tmp
# Runs as a non-root user; /app stays root-owned and read-only to the process (except /app/var).
USER 10001
EXPOSE 8400
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8400/health', timeout=3).status==200 else 1)"
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
# --no-access-log: uvicorn's access log is stdlib logging and bypasses the structlog allow-list
# (it would print every request path); requests are counted in /metrics and audited instead.
CMD ["uvicorn", "dps.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8400", "--no-access-log"]
