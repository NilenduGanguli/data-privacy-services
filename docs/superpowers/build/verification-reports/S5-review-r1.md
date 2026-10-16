# S5-review-r1: M5 Task 25 (OpenShift manifests) on branch `fos`

**Verdict: FAIL.** There are 2 blocking defects. Every gate is green, and the worktree setup is correct.

- Reviewer: an independent subagent, on 2026-10-14.
- Worktree: `/Users/neelu/document_intelligence/dps-wt-fos`, branch `fos`, head `d397a4e`. It is based on main `64c46e3`.
- Commits reviewed: `b7f8112`, `6f1fa33`, `d397a4e`.
- Implementer report: `S5-impl.md`.
- No repository file was modified. Scratch work went into the session scratchpad only.
- The one scratch PostgreSQL container I started (`dps-wt-fos-review-pg`) and its anonymous volume were removed afterwards.

## 1. Worktree preparation (the S5 setup brief)

| Item | Result |
|---|---|
| Worktree and branch | `dps-wt-fos` on `fos`, created from main. Clean tree. |
| `.env` | Holds the brief's ports plus `COMPOSE_PROJECT_NAME=dps-wt-fos` and `DPS_BROWSER_WORKER_METRICS_PORT=9420`. It covers every host-port variable that main's `docker-compose.yml` interpolates: app, db, source-pg, MinIO ×2, Oracle, Mongo, OCR, tokenizer mock and image tag. That compose file uses no `DPS_PG_PORT` or `DPS_EXPORT_ROOT`. |
| `.env.lane` | Exports the contract §12.2 `DPS_TEST_*` variables on the lane ports. I checked that no exported name is a `Settings` field. `DPS_WORKER_METRICS_PORT` is deliberately left out, and that call is correct. |
| `lane.sh` | Copied from m3 and executable. It is ignored through the shared `info/exclude`. `.env`, `.env.lane` and `.venv` are git-ignored. |
| venv | Python 3.12, extras `fpe,docs,db,connectors,tabular,e2e,ner,vertex,dev`. `uitest` is the same pin as `e2e` (`playwright==1.55.0`). Only `synth-ml` (torch) is missing, which is reasonable. |
| frontend | `frontend/node_modules` is a real directory from `npm ci`, not a symlink. |

## 2. Gates I ran myself

| Gate | Result |
|---|---|
| `kustomize build` (v5.8.1) of `overlays/dev`, `overlays/prod`, `release-example` and `base` | 32, 25, 25 and 14 objects, exit 0 |
| `kubectl kustomize` (embedded kustomize v5.7.1) | prod and release-example render objects identical to v5.8.1 |
| `kubeconform -strict` with k8s 1.29 defaults, the OpenShift 4.14 Route schema and the datree CRD catalog | dev 32/32, prod 25/25, release 25/25 valid, 0 errors, 0 skipped |
| `kubeconform -strict` with the full OpenShift **v4.14 and v4.16** standalone-strict catalogs for every kind, plus datree CRDs | all valid, 0 skipped |
| `kubeconform -strict` with k8s **1.27** defaults (OCP 4.14) | 82/82 valid |
| The plan's literal command (`-ignore-missing-schemas`) | dev 29 valid + 3 skipped, prod 22 valid + 3 skipped. The skipped objects are Route, PodMonitor and PrometheusRule. 0 errors. |
| `./lane.sh pytest -q -m deploy tests/deploy` | **36 passed, 1 skipped**. The skip is `dps.browser_worker`, which arrives with M4. |
| `ruff format --check` and `ruff check` on dps, tests and tools; `mypy dps`; `mypy tools/openshift_check.py` | clean: 378 files formatted, 134 files plus 1 file with no issues |
| Full unit suite `./lane.sh pytest -q` | run 1: **1 failed**, 2173 passed, 488 skipped. Run 2: **2174 passed, 488 skipped**. The failure was `tests/test_ndjson.py::test_one_audit_event_per_stream`, a timing flake. It passes 3/3 on its own, and `fos` changes nothing under `dps/` (N11). |
| `promtool check rules` (local `prom/prometheus`) on the rendered PrometheusRule | SUCCESS, 7 rules |
| `tools/openshift_check.py` on prod, dev and release-example | 0 denied each; 22, 22 and 21 warnings. `--fail-on-warn` exits 1 on all three. |
| A fully parameterised release overlay (outside the checkout, relative path) | `--fail-on-warn` gives **0 denied, 0 warnings, exit 0**. The parameter list is complete. |
| gitleaks (the autopush command) on `64c46e3..fos` | 3 commits, **no leaks found** |
| Commit authorship | every commit by NilenduGanguli `<nilendu.ganguli@gmail.com>`, 0 trailers |

## 3. Independent checks (my own tools, not the implementer's)

- **Pod Security Admission `restricted` rules** (written from the PSA spec, run on all three renders) found 0 violations:
  - host namespaces, privileged mode and hostPort: none;
  - capabilities: drop ALL, nothing added;
  - `runAsNonRoot` is true and no UID is 0;
  - seccomp is RuntimeDefault;
  - volumes are only configMap, secret, emptyDir and PVC;
  - root filesystems are read-only, images are pinned by digest, and no service-account token is mounted.
  - No Secret object is committed, and no ConfigMap value looks like a secret. The heuristic's only hits were the names `DPS_TOKENIZER_*`, `DPS_SECRET_DIR` and `DPS_INLINE_SECRETS_ALLOWED`, and their values are not secrets.
- **NetworkPolicies:**
  - No rule has an empty `to` or `from`, empty ports, `0.0.0.0/0`, or an empty namespace or pod selector.
  - `default-deny` covers every pod.
  - The browser worker reaches only DNS, the metadata DB, the object store and the scan targets.
  - The migrate Job reaches only DNS and the DB.
  - Only the app and the worker reach the gateways and the source databases.
- **Posture after the M4 merge.** I exported the committed `m4` branch, which already contains main, M5 and M3, and probed each rendered role against m4's `Settings` and its role-aware `posture_problems(role=...)`:
  - app, worker and browser-worker have no problems in prod, release or dev;
  - every `DPS_*` name is a setting.
  - So the manifests survive the M4 merge. The migrate Job is not posture-checked by design, because `dps.migrate` never calls the guard.
- **The app needs no worker login.** No API route uses `db.acquire_queue`, on fos or on m4. `acquire_queue` would lazily open the worker pool with a login the app does not hold.
- **Connector secret refs.** `dps/secrets.py` `_locate` resolves the root, the tenant folder and each file through `resolve()`. The kubelet's `..data` symlink layout, with an `items` mapping, therefore passes the containment checks.
- **Alert metric names and labels** match `dps/observability.py`:
  - prometheus_client adds `_total`;
  - `outcome` takes the values `ok`, `refused`, `malformed` and `unavailable`;
  - `podTargetLabels` produces the `app_kubernetes_io_component` label.

## 4. Plan acceptance

- **M5 Task 25**:
  - Every planned file exists. The one exception is prod's single `patch-replicas.yaml`, which was split into 3 files; this is logged.
  - Base, components, dev and prod overlays follow the Wega layout. `disableNameSuffixHash` is set.
  - Placeholders (zero digests, `.invalid` hosts, 192.0.2.0/24 peers, `dps-migrate-0`) are listed in the README and warn in the checker.
  - The roles app, worker, browser-worker and migrate Job are present, with `dps-db` as an ExternalName and an edge Route.
  - The 9 plan tests are present, plus 28 more.
  - HANDOFF §1, §7 and §15 and contract §1 and §11.1 are updated.
  - The deviation log entry is complete, and the commit message matches the plan.
  - **Met, except for B1 and B2.**
- **Programme plan §2.6 F2 row (the T25 part):** "kustomize build dev+prod + kubeconform clean" is **met**, and I re-ran it. The arbitrary-UID run was emulated by the implementer with Docker; the image half belongs to T24 (fimg).

## 5. Blocking findings

### B1. Nothing on OpenShift ever runs `dps.migrate --maintain`: the audit log stops recording about 4 months after the last release

- **Where:** `deploy/openshift/base/` has only `job-migrate.yaml`, which runs once per release. README §3 and §6 never mention `--maintain`.
- **Why it happens:**
  - `dps_audit_log` has monthly RANGE partitions and **no DEFAULT partition** (`dps/migrations/007_audit_log.sql`).
  - Partitions are created only by `run_migrations` (`ensure_audit_partitions`: the current month plus 3) and by `python -m dps.migrate --maintain`. Expired partitions are dropped only by `--maintain`.
  - The worker login cannot create them, and the retention job never touches them (`dps/retention.py`).
  - Compose hides the problem because its `migrate` one-shot reruns on every `up`. On OpenShift the Job runs only when a release renames it.
- **Proof** (scratch PostgreSQL 16, `python -m dps.migrate` from this tree):
  - It applied 17 migrations and created partitions `2026_10` through `2027_01`.
  - An audit insert dated 2027-01-31 succeeds.
  - An audit insert dated 2027-02-01 fails with `ERROR: no partition of relation "dps_audit_log" found for row`.
- **Effect:** after about 3.5 to 4 months without a release, every audit flush fails.
  - `DbAuditSink._flush` only logs `audit_flush_failed`. No metric is incremented, no readiness component changes and no alert fires.
  - The database audit trail, which the admin audit search and the report roll-ups read, silently stops.
  - `DPS_AUDIT_RETENTION_MONTHS` (13) is never enforced either.
- **Fix:**
  - Add a CronJob to base, e.g. `dps-migrate-maintain`, that runs `["python", "-m", "dps.migrate", "--maintain"]` daily or weekly, with:
    - `concurrencyPolicy: Forbid` and history limits;
    - the migrate Job's pod spec: SA `dps-migrate`, only the `dps-pg-owner` and `ca` mounts, the same security context and resources;
    - labels name plus `component: migrate`, so `egress-dps-db` covers it.
  - Change `tools/openshift_check.py`:
    - the `owner-secret` rule must accept this CronJob;
    - the `probes` rule must exempt CronJob, not only Job, at line 329.
  - Update `test_only_the_migrate_job_mounts_the_owner_secret` (tests line 233). Add a test that the CronJob exists, runs `--maintain` and is owner-only.
  - Update README §1, §3 and §6 and HANDOFF §15.
  - Optional: an alert once an audit-failure metric exists. That metric needs an app change, so treat it as an F1/F4 follow-up.

### B2. `DPS_IMAGE_DIGEST` is one shared ConfigMap value, so evidence packs can record the wrong image, and the release gate cannot see it

- **Where:**
  - `deploy/openshift/base/configmap-dps.env:46`;
  - `deploy/openshift/base/deployment-browser-worker.yaml`, whose env does not set its own digest;
  - `tools/openshift_check.py`, which has no rule comparing the setting to the images;
  - `release-example/kustomization.yaml:35`, which uses the same `1…1` value for both images and the setting.
- **Wrong image for scans:**
  - The browser worker runs the `data-privacy-services-browser` image but inherits the ConfigMap's value, which is the *app* image's digest. The README says so explicitly.
  - On `m4`, scan evidence is built in the browser worker with `image_digest=settings.image_digest`: `dps/layers/scanner/inventory.py:734`, called from `_write_pack` in `dps/layers/scanner/__init__.py:523` with `ctx.settings`.
  - So after the M4 merge, which v1.0.0 includes, every scan evidence pack names an image that did not produce it.
- **Stale digest:** `DPS_IMAGE_DIGEST` is a separate parameter from the `images:` digests, and nothing ties the two together.
- **Proof:**
  - I built a release overlay with app image `sha256:aaa…`, browser image `sha256:bbb…` and `DPS_IMAGE_DIGEST=sha256:ccc…`.
  - `openshift_check.py --fail-on-warn` printed `25 objects: 0 denied, 0 warnings` and exited 0.
  - A release that bumps the image digests but forgets the ConfigMap literal therefore ships evidence stamped with the previous release, and passes the gate.
- **Fix:**
  - Give the browser-worker container its own `DPS_IMAGE_DIGEST` env entry (the zero placeholder). The env entry overrides envFrom. Add it to the README parameter table and to a release-example patch.
  - Add a deny rule to `openshift_check.py` (e.g. `image-digest-setting`): for each DPS container, the effective `DPS_IMAGE_DIGEST` must equal the `@sha256:` digest of that container's own image, unless it is still the zero placeholder.
  - Alternatively, derive the value with a kustomize `replacements` entry in the release overlay; it must run after `images`.
  - Add tests for both the browser-worker mismatch and the stale-digest case.

## 6. Non-blocking findings (fix if cheap; otherwise carry them)

- **N1. The S3 artefact store ignores the org CA bundle.**
  - `dps/storage.py:239-252` builds the boto3 client without `verify`. botocore then trusts only certifi. I verified that `SSL_CERT_FILE` has no effect and `AWS_CA_BUNDLE` does.
  - README §2.3 says `org-ca.pem` covers "every TLS egress".
  - An internally signed S3 endpoint will fail every artefact write, loudly.
  - Manifest fix: `AWS_CA_BUNDLE=/etc/dps/ca/org-ca.pem` in `configmap-dps.env`. Code fix (F1/F2): pass `verify=settings.ssl_cert_file or None`, also for S3 file targets.
- **N2. README step 2 and the release-example header say to point `resources` at `<checkout>/deploy/openshift/overlays/prod`.**
  - With an absolute path, kustomize fails: `new root '…/overlays/prod' cannot be absolute`. I reproduced this.
  - A relative path works, and I tested that too. Say "a relative path".
- **N3. README §3 step 5 is wrong about the workers.** It says "the app and the workers only verify the migrations; their /readyz reports them until then".
  - In fact `dps.worker._amain` calls `verify_migrations` before starting its ops server and exits 1 on `MigrationDrift`. I reproduced this against an empty database: exit 1, `dps.db.MigrationDrift`.
  - Workers therefore CrashLoopBackOff until the migrate Job completes, on first install and on every release with new migrations.
  - Fix the wording, or add an initContainer that waits for the schema.
- **N4. The checker's `literal-secret` rule uses the suffix regex `(KEY|SECRET|PASSWORD|TOKEN|KEK)$`**, which misses `DPS_COIN_CLIENT_ID`, `DPS_S3_ACCESS_KEY_ID` and `DPS_JOB_KEK_PREVIOUS`.
  - I verified this: these literals in the ConfigMap are denied only through the posture probe, i.e. prod with `--settings on`.
  - In dev, or with `--settings off`, they pass.
  - Use `dps.posture.SECRET_ENV`, keeping the regex as a fallback. The test at tests line 38 uses the same regex.
- **N5. The checker does not enforce `DPS_PG_HOST == dps-db externalName` for release overlays.** Only the tests check it, and only on committed overlays. Add a rule.
- **N6. `INVALID_HOST` and `EXAMPLE_HOST` use the lookahead `(?=[:/]|$)`.** Hosts inside comma lists, such as a future `DPS_SCAN_REGISTERED_HOSTS` or CORS lists, are missed. Use `(?![A-Za-z0-9.-])`.
- **N7. `--proxy-headers` does nothing without `--forwarded-allow-ips`.** uvicorn trusts `127.0.0.1` only, so X-Forwarded-* from the router is ignored. This is harmless today, because authenticated rate limiting is keyed by API key. Drop the flag, or set `FORWARDED_ALLOW_IPS` deliberately.
- **N8. Follow-ups for the M4 merge:**
  - The browser worker's posture becomes role-aware, so it no longer needs `dps-api-keys`. That is least privilege for a pod that renders untrusted pages in Chromium without a sandbox.
  - Re-add `DPS_SCAN_STORE_UNMASKED_ORIGINALS=false`.
  - Note that with `DPS_ENV=dev`, M4 falls back to `scan_registered_hosts=localhost,127.0.0.1,demo-site`, so a dev-namespace scan could load the pod's own loopback ops port.
- **N9. Availability:**
  - There is no `topologySpreadConstraints` or anti-affinity for the 3 app and 2 worker replicas.
  - The RWX `dps-exports` PVC is mandatory even when no `pvc` target is used; pods stay Pending without RWX storage. Consider an opt-in component.
  - No `preStop` drain delay on the app.
- **N10. Carried to F1, already documented in README §6:** `/health`, `/readyz` and `/metrics` answer through the Route without a key, and the metrics carry tenant ids.
- **N11. Flaky unit test:** `tests/test_ndjson.py::test_one_audit_event_per_stream` failed once in a full run. It is not related to this branch; log it for F4.
- **N12. Image pull secrets:** the README does not mention them for an external registry. The release example uses `registry.bank.example`; an `oc secrets link … --for=pull` step is missing.

## 7. Areas checked with no defect found

- **Privileged pods:** none. Every pod and container meets `restricted-v2` and PSA restricted. There is no `runAsUser`, and the mocks also run as non-root under the SCC-assigned UID.
- **NetworkPolicies:** complete for the documented topology, with default-deny, DNS on 53 and 5353, and router ingress for both label styles.
  - HostNetwork routers are documented.
  - Monitoring is limited to `openshift-user-workload-monitoring`.
  - The gateways have one rule per endpoint, and each role's egress is minimal.
- **Secrets in ConfigMaps:** none. Every secret is a `*_FILE` mounted at 0440 (288). Only the migrate Job mounts the owner login, and the app does not hold the worker login.
- **Placeholders reaching prod:** the zero digests, `.invalid` hosts, TEST-NET-1 peers and `dps-migrate-0` all warn, so `--fail-on-warn` refuses them.
  - Applying the committed prod overlay as is would fail loudly: ImagePullBackOff, NXDOMAIN and blocked egress.
  - The exceptions are B2 (the digest setting) and N5 (DB host equality).
- **The implementer's runtime findings**, which I re-checked in code:
  - both workers open the API pool, so they need the app login;
  - verify-full TLS checks the host dialled, so `DPS_PG_HOST` must be the platform host;
  - `enableServiceLinks: false` is required, because `dps-*` Services would inject `DPS_*` variables.

## 8. Files

- Manifests: `/Users/neelu/document_intelligence/dps-wt-fos/deploy/openshift/`
- Checker: `/Users/neelu/document_intelligence/dps-wt-fos/tools/openshift_check.py`
- Tests: `/Users/neelu/document_intelligence/dps-wt-fos/tests/deploy/test_openshift.py`
- The reviewer's scratch probes (`probe_roles.py`, `psa_check.py`, renders) are in the session scratchpad `…/scratchpad/s5r1/`. They are not part of the repository.
