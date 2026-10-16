# S5-review-r2: M5 Task 25 (OpenShift manifests) on branch `fos`, after fix round 1

**Verdict: FAIL.** One blocking defect, new in this round (B1). Every gate is green, both r1 blocking findings
(audit partitions, image digest per container) are fixed and hold up, and the worktree setup is correct.

- Reviewer: independent subagent, 2026-10-14. Worktree `/Users/neelu/document_intelligence/dps-wt-fos`, branch `fos`,
  head `0b6a1e0` (= `origin/fos`), base main `64c46e3`.
- Commits reviewed: `b7f8112`, `6f1fa33`, `d397a4e`, `0b6a1e0`. Read: `S5-impl.md`, `S5-review-r1.md`, `S5-fix-r1.md`,
  M5 plan Task 25 + DoD, programme plan §2.5–§3/§5/§6, contract §2.1/§11.1/§12.2, M5-DEVIATIONS (Task 25, S5 r1 fix), HANDOFF §15.
- No repository file was modified (tree clean). Scratch work: session scratchpad `…/scratchpad/s5r2/`. Every container I
  started (`dps-wt-fos-r2-pg` + its anonymous volume, `dps-wt-fos-r2-browserprobe*`, `dps-wt-fos-r2-appprobe`) is removed;
  no other compose project was touched.

## 1. Worktree preparation

| Item | Result |
|---|---|
| Worktree/branch | `dps-wt-fos` on `fos` from main; clean |
| `.env` | every port of the brief (8419/5513/5514/9100/9101/1529/27025/5096/8429/8439/9419) + `COMPOSE_PROJECT_NAME`, `DPS_BROWSER_WORKER_METRICS_PORT`; covers every host-port/image-tag variable this `docker-compose.yml` interpolates (the other `${DPS_*}` are settings pass-throughs with defaults; `DPS_PG_PORT`/`DPS_EXPORT_ROOT` are not interpolated by this compose file). `docker compose config` OK |
| `.env.lane` | the contract §12.2 `DPS_TEST_*` set on lane ports (same names as m3's, plus `COMPOSE_PROJECT_NAME`, `DPS_TOKENIZER_MOCK_PORT`; `DPS_TEST_REQUIRE_SERVICES` is a per-run switch). Checked: no exported `DPS_*` is a `Settings` field |
| `lane.sh` | copy of m3's, executable, ignored via `info/exclude`; `.env*`, `.venv`, `node_modules` ignored |
| venv / frontend | Python 3.12.13, extras `fpe,docs,db,connectors,tabular,e2e,ner,vertex,dev`; `frontend/node_modules` a real dir |

## 2. Gates I ran myself

| Gate | Result |
|---|---|
| `kustomize build` v5.8.1: dev / prod / release-example / base | 33 / 26 / 26 / 15 objects, exit 0; `kubectl kustomize` (v5.7.1) byte-identical for dev, prod, release |
| `kubeconform -strict` (v0.8.0), OpenShift **v4.14, v4.16, v4.19** standalone-strict catalogs + datree CRDs | dev 33/33, prod 26/26, release 26/26 valid, **0 errors, 0 skipped** (each version) |
| `kubeconform -strict` k8s 1.29 defaults + OCP 4.14 Route + datree CRDs (verbose) | prod 26/26 valid, every kind validated |
| plan's literal command (`-ignore-missing-schemas`), k8s 1.27 and 1.29 | dev 30, prod 23, release 23 valid + 3 skipped (Route, PodMonitor, PrometheusRule), 0 errors |
| negative control | 5 injected unknown fields (CronJob container, NetworkPolicy, PrometheusRule rule, Route tls, PodMonitor endpoint) → 5 invalid |
| `./lane.sh pytest -q -m deploy tests/deploy` | **52 passed, 1 skipped** (`dps.browser_worker` until M4); same with `KUSTOMIZE="kubectl kustomize"` |
| `./lane.sh pytest -q` (full unit suite) | **2190 passed, 488 skipped** (the r1 flaky `test_one_audit_event_per_stream` did not recur) |
| `ruff format --check` / `ruff check` (dps tests tools); `mypy dps`; `mypy tools/openshift_check.py` | 378 formatted / all passed; 134 files clean; 1 file clean |
| `tools/openshift_check.py` prod / dev / release-example | 0 denied each; 24 / 24 / 23 warnings (release: `example-value` only); `--fail-on-warn` exit 1 on all three |
| `promtool check rules` (local `prom/prometheus`) on the rendered PrometheusRule | SUCCESS, 8 rules |
| gitleaks (autopush command) on `64c46e3..0b6a1e0` | 4 commits, no leaks |
| Authorship | 4 commits by `NilenduGanguli <nilendu.ganguli@gmail.com>`, `git interpret-trailers --parse` empty |

## 3. Independent checks

- **Pod security** (my own PSA-`restricted` + `restricted-v2` checker on all three renders): no privileged container,
  no host namespaces/ports/`hostPath`, `runAsNonRoot` everywhere and no pinned UID/fsGroup, drop ALL / add none,
  `allowPrivilegeEscalation: false`, RuntimeDefault seccomp, read-only root, allowed volume types only, digests on every
  image, no SA token, no committed Secret. Only hits: two name heuristics (`DPS_TOKENIZER_PROVIDER=""`,
  `DPS_KEY_CACHE_SECONDS`) — not secrets.
- **NetworkPolicies**: every pod (app, worker, browser-worker, Job, CronJob, dev mocks) is selected by `default-deny`
  for Ingress and Egress; no allow-all rule (no empty `to`/`from`, no port-less rule, no `0.0.0.0/0`, no empty
  selectors). Per role: app = router, monitoring, DB, gateways, object store, source DBs; worker = same minus router;
  browser worker = DB, object store, scan targets (443), monitoring; migrate Job/CronJob = DNS + DB. The M4 API layer
  makes no outbound call to scan targets (checked on `m4`), so the app needs no scan-target egress.
- **Secrets in ConfigMaps**: none committed; every secret is a `*_FILE` under a mounted Secret (0440); only the Job and
  the CronJob mount `dps-pg-owner`. (A checker gap for non-`DPS_` keys: N2.)
- **After the M4 merge** (`m4` head `8f4c5b0`, extracted read-only): each role's env (ConfigMap + container env, dummy
  secret files) builds `Settings` and passes m4's role-aware `posture_problems` in prod, dev and the release example —
  also the stricter app-role rules for the browser worker; no `DPS_*` name is unknown; m4's `SECRET_ENV` ⊆ the checker's
  `SECRET_SETTINGS`. `fsec`'s only config change is `repr=False`. The scanner never calls the LLM, OCR or tokenizer.
- **Arbitrary UID**: browser image (`data-privacy-services-browser:m4`) launches Chromium 140 as UID 1000680000:0
  (+fsGroup), read-only root, caps dropped, no-new-privileges, `HOME=/tmp`; app image (`data-privacy-services:fimg`)
  serves `/health` 200 and `/readyz` 200 under the same emulation, root write → EROFS, 0 error lines.
- **r1 fixes verified**: B1 CronJob `dps-migrate-maintain` (`--maintain` daily 03:17 Etc/UTC, Forbid, deadlines, the Job's
  pod, owner login only, `component: migrate` → DNS + `egress-dps-db`; `dps.migrate` exits 1 on drift/DB errors, so a
  failure is a failed Job). B2 per-container `DPS_IMAGE_DIGEST` (browser env entry; `replacements` in the release example;
  deny rule `image-digest-setting`, tested for stale and inherited digests). N1–N6, N12 fixed as described.

## 4. Plan acceptance

- **M5 Task 25:** files, Wega layout, placeholders (all listed in README §4 and warned by the checker), roles app,
  worker, browser-worker, migrate Job (+ CronJob), `dps-db` ExternalName, edge Route, 9 plan tests + 43 more, HANDOFF
  §1/§7/§15, contract §1 and §11.1, deviation log, plan commit message — **met, except B1**.
- **Programme plan §2.6 F2 (T25 part):** kustomize dev+prod + kubeconform clean — **met** (re-run above). Arbitrary-UID
  run re-confirmed for both images; images/smoke are T24 (fimg).

## 5. Blocking finding

### B1. After a release with a new migration, app pods that start before the migrate Job finishes lose the whole job platform until restarted — silently

- **Where:** `deploy/openshift/base/deployment-app.yaml` (nothing makes the app wait for the schema);
  `deploy/openshift/README.md` §3 steps 4–8 (lines 124–135: apply Job and Deployments together; step 5 says the app shows
  `migrations pending` "until it completes"; step 8 smokes with `DPS_SMOKE_M2=off`). Root cause in the app:
  `dps/app.py` `_JobPlatform._connect` (lines 395–412) sets `db_state="down"` when migrations are pending and schedules no
  retry (only an exception starts `_reconnect`) — contract §2.1 (line 310) says verify mode "boot fails on pending or
  drifted files". Compose never hits it: `migrate` is a `service_completed_successfully` dependency of app and worker.
  Kubernetes has no such ordering and the manifests add none.
- **Proof** (`s5r2/exp_release_race.sh`, output `exp_release_race.out`; scratch PostgreSQL 16 with the repo's
  `docker/dps-db/initdb` roles):
  1. the "previous release" (this tree minus `045_policy_directory.sql`) applies 16 migrations;
  2. the app of this tree starts (as a rollout pod does, concurrently with the release Job): `/readyz` **200**,
     `jobs: migrations pending or drifted`; `GET /api/v1/jobs` **503 `db_unavailable`**;
  3. the release's `python -m dps.migrate` applies `045`;
  4. the same app process **40 s later** (4× `DB_RETRY_SECONDS`): still `pending or drifted`, still **503**;
  5. only after a restart: `jobs: ready`, `GET /api/v1/jobs` **200**. Log scan of both app runs: 0 hits of the key or KEK.
- **Impact:** every route behind `require_db` — jobs, connections, groups, snapshots, artefacts, evidence, reports,
  admin, policy store — and `@name` policy references answer 503 on each app replica whose start won the race. The pod is
  Ready (`/readyz` requires only `api-core`), so `oc rollout status` succeeds, README's smoke passes (M2 off), and no
  alert fires (`dps_auth_keys_loaded` keeps refreshing). With `maxSurge: 1` the first new replica is the most exposed;
  one stuck replica of three means about a third of job-platform calls fail in prod until someone restarts the pods. The
  race is likely whenever image pulls differ by node or a migration takes more than a few seconds. (A first install in
  prod self-heals: on an empty schema the RLS posture check either raises — the pod restarts — or errors — the retry loop
  starts. The worker exits on drift and restarts — r1 N3 — so only the app sticks, and only on upgrades.)
- **Fix (what the next review checks):**
  - **Required:** an initContainer (e.g. `wait-for-schema`) in `dps-app` and `dps-worker` (and `dps-browser-worker`,
    see N4), same image, `args: ["sh", "-c", "until python -m dps.migrate --verify; do sleep 5; done"]`, env = the `dps`
    ConfigMap + `HOME=/tmp` + `DPS_PG_PASSWORD_FILE` only (`--verify` uses the app login, which all three pods already
    mount), mounts `tmp`, `ca`, `pg-app`, the pod's security context and small cpu/memory requests+limits (the checker's
    hardening and `image-digest-setting` rules apply to init containers too). This restores compose's ordering on
    OpenShift and also ends the worker CrashLoopBackOff of r1 N3. Add a test that every Deployment reading the `dps`
    ConfigMap has it.
  - Correct README §3 step 5 (the app does not recover by itself) and HANDOFF §15.
  - **Follow-up for main (F1/F4, not this branch):** make the app follow contract §2.1 — in verify mode keep re-verifying
    until verified (or exit) instead of staying degraded.
  - Fallback only if the initContainer is rejected: README step "after the Job completes, `oc rollout restart
    deploy/dps-app`, then check component `jobs` is ok on every replica (`oc exec`)", plus the main follow-up above.

## 6. Non-blocking findings

New in this round:
- **N1. `DpsAuditMaintenanceStale` cannot fire for a CronJob that never succeeded, on OpenShift.** Its second branch
  reads `kube_cronjob_created`, which OpenShift's kube-state-metrics drops (`--metric-denylist=^kube_.+_created$` in
  cluster-monitoring-operator `release-4.14`, `release-4.16`, `master`), and `kube_cronjob_status_last_successful_time` is
  exported only after a first success (KSM `cronjob.go`). promtool (`s5r2/prom/test.yaml`): OpenShift-like series, failing
  since creation → **no alert at 5 days**; with `kube_cronjob_created` → fires; last success 4 days ago → fires. README
  §6 line 213 and HANDOFF line 1354 claim the "never" case. Fix: second branch on
  `kube_cronjob_info{cronjob="dps-migrate-maintain"} unless on(namespace) kube_cronjob_status_last_successful_time{…}`
  with a long `for`, or on `kube_job_status_failed{job_name=~"dps-migrate-maintain-.*"} > 0`.
- **N2. The release check misses literal secrets under non-`DPS_` ConfigMap keys.** `tools/openshift_check.py:429`
  checks only `DPS_*` keys. Proof: `AWS_SECRET_ACCESS_KEY`/`AWS_ACCESS_KEY_ID` or `PGPASSWORD` literals in ConfigMap `dps`
  → 0 findings (with the posture probe), while the same name as a container env literal is denied. botocore and libpq
  read those names, and the ConfigMap already carries a non-`DPS_` key (`AWS_CA_BUNDLE`). README §5 claims "a
  secret-named ConfigMap key" is denied. Fix: apply `is_secret_setting` to every ConfigMap key (allow `AWS_CA_BUNDLE`,
  `*_FILE`), with a test.
- **N3. Provenance on rollback.** App/worker/migrate read `DPS_IMAGE_DIGEST` from the shared, unhashed ConfigMap, not the
  pod template, so `oc rollout undo` (or an old-ReplicaSet pod restarting mid-rollout) runs the old image with the new
  digest, and evidence names the wrong build. The gate cannot see cluster operations. Fix: a per-container env entry
  (as the browser worker has), derived by `replacements`.
- **N4. The browser worker never verifies the schema** (no check at start or in `/readyz`, on `m4`). During a release it
  runs new code against the old schema until the Job completes; scan jobs may fail and retry. B1's initContainer covers it.
- **N5. Project-template NetworkPolicies.** Clusters whose project template adds `allow-from-same-namespace` (or
  similar) to new projects re-open in-namespace ingress (e.g. browser worker → app 8400) despite `default-deny`, because
  policies are additive. README §2 should require the DPS namespaces to carry no such policy.
- **N6. Audit retention is now enforced daily.** The CronJob drops audit months older than `DPS_AUDIT_RETENTION_MONTHS`
  (13). README §4's tunables list should name it, since bank audit retention is often longer.

Carried from r1 (still open, owners as before): N7 `--proxy-headers` without `--forwarded-allow-ips` (F1). N8 M4-merge
follow-ups: drop `dps-api-keys` from the browser worker once posture is role-aware, pass the role in the checker,
`DPS_SCAN_STORE_UNMASKED_ORIGINALS`. Confirmed this round: Chromium runs with `--no-sandbox` (Playwright default;
restricted-v2 cannot give it one), so the KEK, both DB logins, S3 keys and API-key hashes mounted in that pod are one
renderer exploit away from a hostile scan target — F1 should record it as residual risk and keep the pod's mounts
minimal. N9 availability: spread, opt-in RWX PVC, `preStop`; app grace 30 s is shorter than the 300 s Route timeout,
so a rollout cuts long document requests. N10 `/health`, `/readyz`, `/metrics` reachable through the Route (F1).
N11 flaky test (F4).

## 7. Checked, no defect

Privileged pods (none); NetworkPolicy coverage and least privilege per role; secrets only as files, the owner login only in
migrate workloads; placeholders (zero digests, `.invalid` hosts, TEST-NET-1 peers, `dps-migrate-0`, zero
`DPS_IMAGE_DIGEST`) all warn and fail `--fail-on-warn` — a fully parameterised release passes 0/0 (test in tmp); applying
a placeholder render as is fails loudly (pull, DNS, egress); `DPS_PG_HOST` = `dps-db` externalName (deny rule); CronJob
schedule/time zone/deadlines; Route edge TLS + redirect; PDBs; `enableServiceLinks: false`; the dev mocks are isolated
(ingress from app/worker only, egress DNS only); README commands (`keys_cli` flags, pull secret, KEK rotation) match the code.

## 8. Files

- Manifests `/Users/neelu/document_intelligence/dps-wt-fos/deploy/openshift/`; checker `…/tools/openshift_check.py`;
  tests `…/tests/deploy/test_openshift.py`; app code of B1 `…/dps/app.py`.
- Scratch proofs (not in the repo): `/private/tmp/claude-501/-Users-neelu-document-intelligence/d1f0660d-411d-4447-9d4e-0331e8df3a77/scratchpad/s5r2/`
  — `exp_release_race.sh` + `.out` (B1), `prom/` (N1), `psa_r2.py`, `probe_m4.py`, `launch_probe2.py`, `render/`,
  `pytest-deploy.log`, `pytest-full.log`, `static.log`.
