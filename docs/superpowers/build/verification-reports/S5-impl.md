# S5-impl — M5 Task 25: OpenShift manifests (stream `fos`, finish stage)

**Verdict: DONE.** Task 25 is implemented test-first and committed on branch `fos`
(worktree `/Users/neelu/document_intelligence/dps-wt-fos`), every gate is green, and each role's
runtime assumptions were tested by running its rendered prod pod spec. Not pushed (the owner's
autopush loop picks up `fos`).

| Commit | Message |
|---|---|
| `b7f8112` | feat(deploy): OpenShift kustomize base, network policies, monitoring, dev and prod overlays for every role (the plan's message) |
| `6f1fa33` | fix(deploy): both workers hold the app login they open at start (found by the runtime proof) |
| `d397a4e` | docs(deploy): host-network routers and ConfigMap changes in the OpenShift guide |

Base: main `64c46e3` (M0+M1+M2+M5+M3 merged; M4 not merged). Author `NilenduGanguli
<nilendu.ganguli@gmail.com>`, no trailers (checked), explicit pathspecs; gitleaks (the same Docker
command autopush uses) over `64c46e3..6f1fa33`: **no leaks found**.

## 1. Worktree setup

- `git worktree add ../dps-wt-fos -b fos main`.
- Git-ignored lane files: `.env` (`COMPOSE_PROJECT_NAME=dps-wt-fos`, `DPS_IMAGE_TAG=fos`, ports
  app 8419, db 5513, source-pg 5514, MinIO 9100/9101, Oracle 1529, Mongo 27025, OCR 5096, demo site
  8429, tokenizer mock 8439, worker metrics 9419, browser-worker metrics 9420 — all checked free with
  lsof), `.env.lane` (the contract §12.2 `DPS_TEST_*` on those ports, the m3 format), `lane.sh` (copy of
  m3's). `.env`/`.env.lane` are covered by `.gitignore`, `lane.sh` by the shared `info/exclude`.
- `uv venv --python 3.12 .venv`; `./lane.sh uv pip install -e ".[fpe,docs,db,connectors,tabular,e2e,ner,vertex,dev]"`
  (every extra except `synth-ml`, which pulls torch and is never in an image); `cd frontend && npm ci`.
- **Lane trap found and fixed:** `DPS_WORKER_METRICS_PORT` is both a compose host-port variable
  (contract §0) and the worker's own setting (`worker_metrics_port`). Exported into the test
  environment it changed Settings defaults (`tests/test_config_m2.py` failed: 9419 ≠ 9400). It is now
  only in `.env` (compose interpolation), not in `.env.lane`. Other lanes must not export it either.
- No compose stack was started (Task 25 needs none). The runtime proof used my own `dps-wt-fos-ocp-*`
  containers/volumes/network, all removed afterwards. No other project was touched.

## 2. What was built

`deploy/openshift/` (Wega layout):
- `base/`: Deployments `dps-app` (uvicorn, `--proxy-headers --no-access-log`, port `http` 8400),
  `dps-worker` (`python -m dps.worker`, `ops` 9400), `dps-browser-worker` (`python -m
  dps.browser_worker`, browser image, `ops` 9401); Job `dps-migrate-0` (`python -m dps.migrate`,
  `DPS_MIGRATIONS_MODE=auto`, owner login only); Services `dps-app` (ClusterIP) and `dps-db`
  (ExternalName); Route (edge TLS, redirect, 300 s timeout); PDBs; four token-less ServiceAccounts;
  ConfigMap `dps` from `configmap-dps.env` (42 non-secret settings; secrets only as `*_FILE`).
- `components/network-policies` (default-deny, DNS, router → app 8400, monitoring → 8400/9400/9401,
  DB, gateways one rule per endpoint, object store, source databases, scan targets),
  `components/monitoring` (PodMonitor with `podTargetLabels`; PrometheusRule with the 7 alerts,
  `DpsRawPiiSentToLlm` critical), `components/dev-mocks` (tokenizer mock with a byte-identical
  `server.py`, Azure OCR mock, their network policies).
- `overlays/dev` (`dps-dev`, 1/1/1, mocks, stub LLM, TLS egress off) and `overlays/prod` (`dps-prod`,
  3/2/1); every replica change is a JSON6902 `test` + `replace`.
- `release-example/`: a release overlay that sets every parameter (images, `DPS_IMAGE_DIGEST`, 6
  hosts, `dps-db` externalName, Route host, 8 CIDRs, the Job's name) with documentation values.
- `README.md`: roles, platform prerequisites, secrets table with keys, deploy steps, the parameter
  table (every placeholder), the release check, operations (alerts, KEK rotation, scaling), dev.
- `tools/openshift_check.py` (the Wega lesson "warn placeholder + `--fail-on-warn`"): placeholders and
  example values **warn**; hardening, literal secrets, `*_FILE` outside a mounted secret, owner secret
  outside the migrate Job, missing default-deny, mocks in staging/prod and — in a clean subprocess with
  dummy secret files — each DPS role's `Settings` and production posture **deny**. Exit 0/1/2.
- `tests/deploy/test_openshift.py`: 37 tests (36 pass, 1 skip until the M4 merge adds
  `dps.browser_worker`).
- HANDOFF §1 row, §7 OpenShift secret names, new §15; contract §1 row and §11.1 line; deviation log
  `## Task 25` (marked finish stage) and the 16B→25 deferral marked closed.

## 3. Deviations from the plan (details in `docs/superpowers/build/M5-DEVIATIONS.md` § Task 25)

1. **`DPS_PG_HOST` = the platform host, not `dps-db`.** verify-full TLS (`dps/db.py`,
   `check_hostname=True`) checks the certificate against the name dialled. Proved at runtime: the same
   server dialled as `dps-db-other` fails `SSLCertVerificationError`, as `dps-db` (its certificate
   name) succeeds. The ExternalName stays as the alias; a test keeps the two equal.
2. **Both workers hold the app login too** (`dps-pg-app`): `dps/worker.py::_amain` and M4's
   `browser_worker.amain` call `db.init_pool` before `db.init_worker_pool`. The plan's "worker login
   instead of the app's" made the worker exit at boot (`password authentication failed for user
   "dps_app"`). Each login has its own mount; a test reads both start-up functions.
3. **Both workers mount `dps-api-keys` and the KEK**: they run `assert_production_posture`, which
   requires `DPS_API_KEYS(_FILE)` (plan: not needed for jobs).
4. **Browser worker**: features switched off in its env (`DPS_OCR_PROVIDER=off`,
   `DPS_TOKENIZER_PROVIDER=""`, `DPS_LLM_PROVIDER=""`) because it has none of their secrets; with the
   shared ConfigMap it would fail the posture guard (probe recorded).
5. ConfigMap adds `DPS_VERIFY_WARN_ALLOWED=false`, `DPS_LLM_EGRESS_CHECK=strict` (Task 16 boot rules;
   the plan's own posture test failed without the first) and `DPS_IMAGE_DIGEST` (placeholder, stamped
   into evidence); drops `DPS_SCAN_STORE_UNMASKED_ORIGINALS` (not a main setting until M4 merges;
   default false; a test refuses any `DPS_*` name that is not a setting).
6. The app also mounts the exports PVC (its `pvc` connection test writes a probe object) → RWX.
7. Network policies: source databases reachable from the app too (connection test/browse); the object
   store is its own policy covering the browser worker (captures); one gateway rule per endpoint;
   migrate included in DB egress; dev mocks get their own policies.
8. `enableServiceLinks: false` and `HOME=/tmp` on every pod; selectors use name + component; PDB
   `maxUnavailable: 1`; secrets `defaultMode: 288` and not optional where a `*_FILE` points into them;
   workers' grace 60 s; worker `/tmp` 4Gi; Route timeout 300 s; PodMonitor `podTargetLabels`;
   `DpsNoDbKeysLoaded` scoped to the app; three prod replica patches instead of one file.
9. Tests 37 instead of the plan's 13 (posture test per role, plus the checks listed in the log).

## 4. Gates and proofs (all in the worktree, through `./lane.sh`)

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` / `ruff check dps tests tools` | 378 files formatted / all checks passed |
| `mypy dps` | no issues, 134 files |
| `pytest -q` (unit; service tests skip without containers) | **2174 passed, 488 skipped** |
| `pytest -q -m deploy tests/deploy` | **36 passed, 1 skipped** (`dps.browser_worker` until M4) |
| `kustomize build` (v5.8.1) dev / prod / release-example | renders (32 / 25 / 25 objects) |
| `kubeconform -strict` (k8s 1.29; OpenShift 4.14 Route schema from melmorabity/openshift-json-schemas; datree CRDs for PodMonitor/PrometheusRule) | dev 32/32, prod 25/25, release-example 25/25 valid, 0 errors, 0 skipped |
| `promtool check rules` (local `prom/prometheus`) on the PrometheusRule | 7 rules, SUCCESS |
| `tools/openshift_check.py` prod / dev | 0 denied, 22 warnings each (the placeholders) |
| release example | 0 denied, only `example-value` warnings (so a verbatim copy fails `--fail-on-warn`) |
| posture probe, prod app / worker / browser-worker | `[]` each; a weakened ConfigMap is caught |

**Runtime proof (no cluster available)** — each role's rendered prod pod spec run with Docker: the
ConfigMap + container env (`DPS_ENV=prod`), every secret a `root:<fsGroup>` 0440 file at its mount path
(volumes populated like the kubelet does), UID 1000680000 with group 0 and the fsGroup,
`--read-only`, `--cap-drop ALL`, `no-new-privileges`, tmpfs `/tmp` (and `/dev/shm`), against a scratch
PostgreSQL 16 with TLS whose certificate names only `dps-db` (images `data-privacy-services:fimg`,
`data-privacy-services-browser:m4` — read only, not modified):
- migrate Job: `migrations: applied 17` (001–010, 020, 040–045);
- worker: `/readyz` 200 — db up, migrations verified, kek loaded, queue claiming;
- app: `/readyz` 200 (db up, jobs ready, keys `db+static`; tokenizer/OCR down = `.invalid`
  placeholders, not required), `/health` 200, `POST /api/v1/detect` 200 with the static key, 401
  without, `/api/v1/openapi.json` 404 (prod), `GET /api/v1/admin/posture` **11/11 passed, enforced**;
- browser worker: ready, Chromium 140 launched under the read-only root;
- exports volume writable through the fsGroup; root filesystem read-only (EROFS); `HOME=/tmp`;
- TLS name: dialling `dps-db` → `applied 0 (none pending)`; dialling `dps-db-other` →
  `SSLCertVerificationError`;
- log scan (app, worker, PostgreSQL): 0 hits of the API key, the KEK and the request's e-mail; 0
  tracebacks/errors; the audit line is value-free (`findings_by_type: {EMAIL: 1}`).

## 5. Findings for other streams

- **F1 security (fsec):** `/health`, `/readyz` and `/metrics` answer without a key on port 8400, so
  they are reachable through the Route (component states, tenant ids in metric labels, no values).
  README §6 tells the platform to restrict them at the router if the Route leaves the bank network;
  an app-level fix (metrics on a separate port, or a key) is F1's call.
- `python -m dps.migrate --verify` authenticates with the **app** login, so it cannot run in the
  migrate Job's pod (owner login only); the Job uses the apply mode, which is unaffected.
- **M4 merge:** M4's `browser_worker` calls `assert_production_posture(settings, role=...)` (role-aware
  posture). `tools/openshift_check.py` calls `posture_problems(settings)` without a role, which is
  stricter for the browser worker; it passes today thanks to the env overrides. After the merge,
  consider passing the role, re-add `DPS_SCAN_STORE_UNMASKED_ORIGINALS=false` if wanted, and the
  module test stops skipping. The login test already reads `browser_worker.amain` once it exists.
- **fimg (Task 24):** the manifests set `HOME=/tmp` themselves; the fimg image already has `ENV
  HOME=/tmp` and ran fine under the arbitrary UID here.
- **fdocs (Task 27):** HANDOFF gained a §1 row, a §7 sentence and §15; expect a textual conflict if
  HANDOFF is restructured — keep §15's content.

## 6. Not done / limits

- No live OpenShift apply (no cluster): the SCC admission (UID range, fsGroup) and NetworkPolicy
  enforcement are covered by static tests, kubeconform and the Docker emulation, not by a cluster.
- No compose stack, no frontend changes (so no `npm test`/build gate needed beyond `npm ci`).
- Not pushed (left to the owner's autopush loop); no tags.

## 7. Files

- Manifests: `/Users/neelu/document_intelligence/dps-wt-fos/deploy/openshift/` (README.md, base/,
  components/, overlays/, release-example/)
- Checker: `/Users/neelu/document_intelligence/dps-wt-fos/tools/openshift_check.py`
- Tests: `/Users/neelu/document_intelligence/dps-wt-fos/tests/deploy/test_openshift.py`
- Docs: `HANDOFF.md` §15, `docs/superpowers/build/M5-DEVIATIONS.md` § Task 25,
  `docs/superpowers/plans/2026-10-11-m1-m5-contract.md` §1 and §11.1
- Logs: `~/.cache/dps-build/fos-pytest.log`, `fos-pip.log`, `fos-npm.log`
