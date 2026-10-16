# S5-review-r3: M5 Task 25 (OpenShift manifests) on branch `fos`, after fix round 2

**Verdict: PASS.** No blocking defect. Every gate is green when I re-run it. The round-2 blocking
finding (the start-order race) is fixed, and my own runtime test confirms the fix. The worktree
setup is correct. Six new non-blocking items are listed in §6. One of them (R3-N1) matters at
merge time: once `fsec` lands, the prod app and worker refuse to boot unless `DPS_TOKENIZER_PROFILES`
is set. The tests catch this at the merge, so it cannot pass silently.

- **Reviewer:** an independent subagent, 2026-10-14.
- **Worktree:** `/Users/neelu/document_intelligence/dps-wt-fos`, branch `fos`.
- **Head:** `866da65`, which equals `origin/fos`.
- **Base:** main `64c46e3`. Current main is `144cc65`; its only new commits are docs, and they do not overlap with `fos`.
- **Commits reviewed:** `b7f8112`, `6f1fa33`, `d397a4e`, `0b6a1e0`, `866da65`.
- **Read:**
  - RESUME §0–§3, §10;
  - programme plan §2.5–§7;
  - M5 plan Task 25 and DoD;
  - contract §2.3, §11.1, §12.2;
  - M5-DEVIATIONS (Task 25, S5 r1 fix, S5 r2 fix);
  - HANDOFF §1, §7, §15;
  - S5-impl, S5-review-r1/r2, S5-fix-r1/r2.
- **Repository files:** none modified; the tree is clean.
- **Scratch resources:** my containers, volumes and network (`dps-wt-fos-r3*`) were all removed, and 0 are left. I touched no other compose project.
- **Artefacts:** `~/.cache/dps-build/reports/S5-review-r3/` holds:
  - renders;
  - logs;
  - the probe scripts and the runtime script;
  - the promtool files.

## 1. Worktree preparation (the S5 setup brief)

| Item | Result |
|---|---|
| Worktree/branch | `dps-wt-fos` on `fos`, from main; clean; `fos` == `origin/fos` |
| `.env` | Every port of the brief: 8419, 5513, 5514, 9100, 9101, 1529, 27025, 5096, 8429, 8439, 9419. It also sets `COMPOSE_PROJECT_NAME=dps-wt-fos` and `DPS_BROWSER_WORKER_METRICS_PORT=9420`. Together these cover every host-port and image-tag variable this `docker-compose.yml` interpolates. The other `${DPS_*}` are settings pass-throughs with defaults; `DPS_PG_PORT` and `DPS_EXPORT_ROOT` are not interpolated here. |
| `.env.lane` | The same `DPS_TEST_*` names as m3's (contract §12.2) on the lane's ports. `DPS_WORKER_METRICS_PORT` is deliberately not exported, because it is also a `Settings` field. |
| `lane.sh` | Byte-identical to m3's and executable. It is ignored through `info/exclude`. `.env*`, `.venv` and `node_modules` are git-ignored. |
| venv / frontend | Python 3.12.13 with every extra except `synth-ml`, which brings torch and is never in an image. `frontend/node_modules` is a real directory, not a symlink. |

## 2. Gates I ran myself

| Gate | Result |
|---|---|
| `kustomize build` v5.8.1: base, dev, prod, release-example | 15 / 33 / 26 / 26 objects, exit 0. `kubectl kustomize` (v5.7.1) output is byte-identical for all four. |
| `kubeconform -strict` v0.8.0: k8s 1.29.0 defaults + OCP v4.14 Route schema + datree CRDs | dev 33/33, prod 26/26, release 26/26; **0 errors, 0 skipped**. The verbose run lists every kind as validated, including Route, PodMonitor, PrometheusRule and CronJob. |
| `kubeconform -strict` with the full OpenShift **v4.14 and v4.16** standalone-strict catalogs + datree CRDs | dev 33/33, prod 26/26, release 26/26 valid, 0 skipped |
| The plan's literal command (`-ignore-missing-schemas`) on k8s 1.27.0 and 1.29.0 | dev 30 valid + 3 skipped, prod 23 + 3 skipped, 0 errors. The skipped objects are Route, PodMonitor and PrometheusRule, which have no default schema. |
| kubeconform negative control | An unknown field in the app's `wait-for-schema` and in the CronJob's container gives **2 invalid**, so strict validation reaches both. |
| `./lane.sh pytest -q -m deploy tests/deploy` | **60 passed, 1 skipped**. The skip is `dps.browser_worker`, which arrives with M4. The result is the same with `KUSTOMIZE="kubectl kustomize"`. |
| `./lane.sh pytest -q` (full unit suite) | **2198 passed, 488 skipped** in 55.9 s. The skips are service-backed tests, since no stack is running. |
| `ruff format --check`, `ruff check` (dps, tests, tools); `mypy dps`; `mypy tools/openshift_check.py` | 378 files formatted; all checks passed; 134 files clean; 1 file clean |
| `tools/openshift_check.py` on prod / dev / release-example | 0 denied each. Warnings: 28 / 28 / 27. Prod's 28 are 8 `placeholder-cidr`, 11 `placeholder-digest`, 8 `placeholder-host` and 1 `placeholder-job`; release-example's are all `example-value`. Exit 0 on all three; `--fail-on-warn` exits 1 on all three. |
| A fully parameterised release overlay of my own (outside the checkout, relative `resources`) | `--fail-on-warn` with the Settings + posture probe: **26 objects, 0 denied, 0 warnings, exit 0**. The parameter list is complete. |
| promtool 3.14.0 (`prom/prometheus`) on the rendered PrometheusRule | `check rules` gives SUCCESS, 8 rules. The fixer's 6 `test rules` cases also give SUCCESS; their rules file equals the committed render's rules. |
| gitleaks (the autopush command) on `64c46e3..866da65` | 5 commits, **no leaks found**. The DoD #9 grep (AKIA / private keys) over the fos files finds nothing. |
| Authorship | All 5 commits have author and committer `NilenduGanguli <nilendu.ganguli@gmail.com>`. `git interpret-trailers --parse` is empty, and the identity comes from the global config. No file under `dps/` is changed. |

## 3. The round-2 blocking finding (B1: the start-order race): fixed and verified

**What I read:**

- **The init container `wait-for-schema` in all three Deployments.**
  - It runs the pod's own image, so the browser worker's wait uses the browser image.
  - It uses `args` rather than `command`, so the image entrypoint is kept. fimg's entrypoint (with `umask 0002`) says "set `args`, not `command`", and the manifests comply.
  - The command is `sh -c "trap 'exit 143' TERM INT; until python -m dps.migrate --verify; do sleep 5 & wait $!; done"`.
  - Its env is the `dps` ConfigMap plus `HOME` and `DPS_PG_PASSWORD_FILE`. The browser worker's wait also gets its own `DPS_IMAGE_DIGEST`.
  - It mounts only `tmp`, `ca` and `pg-app`, and keeps the pod's security context.
  - Resources are 50m/64Mi requests and 500m/256Mi limits.
- **`--verify` reads the ledger through `db.verify_migrations`**, which opens the API pool with the app login. The test `_opens(db.verify_migrations) == {"app"}` pins this.
- **New deny rule `schema-wait`** in the checker. `configuration_findings` now builds `Settings` for the init containers too.
- **README §1 and §3 steps 5–6, the rollback note, §6, HANDOFF §15, contract §11.1 and the deviation log** are all consistent with the code.

**My own runtime proof.** It uses this tree's rendered prod pod specs.

- **Emulation:**
  - UID 1000680000 with group 0, the fsGroup added as a supplemental group, a read-only root, `--cap-drop ALL`, no-new-privileges and each container's cpu/memory limits;
  - secrets as `root:fsGroup` 0440 files;
  - a scratch PostgreSQL 16 with verify-full TLS, whose certificate names only `dps-db`.
- **Images:** `data-privacy-services:fimg` (T24's image, umask entrypoint) and `data-privacy-services-browser:m4`.

| Step | Result |
|---|---|
| A. The app's wait on an empty schema, then SIGTERM | It kept checking. Each output line is value-free, for example `migrations_mode=verify: 001_schema_and_roles.sql: pending; …`. `docker stop -t 20` ended it in under 1 s with exit **143**: the trap works under dash as PID 1. |
| B. App, worker and browser waits running; the release Job container runs (owner login) | `migrations: applied 17`. The app and worker waits exited 0 with `migrations: verified`, **3.3 s and 3.4 s** after the Job finished. |
| C. The browser wait (its image ships 19 migrations) | It kept waiting on `030_scans.sql: pending; 031_tenant_hosts.sql: pending`. After the browser image's own migrate run applied those 2, it exited 0 (`verified`). |
| D. An app-image pod (17 migrations) starting after 030/031 were applied | It waits for good with `030_scans.sql: applied but no longer shipped; …`. This matches the README's fix-forward note. It also shows that the app and browser images must ship the same migration set (R3-N3). |
| Log scan of every container log | 0 hits for traceback, DSN, `password=` or private key |

The app's main-container recovery after the wait was proven by the fixer (A1/A2, `GET /api/v1/jobs` 503 → 200). Steps A–C independently confirm the part the fix adds.

**Round-2 non-blocking items marked fixed:**

- **N1 alert.** I read the KSM v2.9.2 source (`kube-state-metrics` version in OCP 4.14).
  - `kube_job_status_failed` has a series only once the Job carries the Failed condition, with a `reason` label.
  - `kube_cronjob_status_last_successful_time` exists only after a first success.
  - The CMO 4.14 and 4.16 denylist drops `^kube_.+_created$` and none of the metrics the rule uses.
  - So the new expression can fire on OpenShift.
- **N2 every ConfigMap key:** tested.
- **N5:** README §2.1.
- **N6:** README §4.
- **N4:** closed by B1.

## 4. Independent hunt for defects

- **Privileged pods: none.**
  - My own PSA `restricted` (k8s 1.29 rules) plus `restricted-v2` checker covers init containers. It found 0 violations across the 7 dev pods, 5 prod pods and 5 release pods.
  - In detail: no host namespaces, ports or `hostPath`; no pinned UID, GID or fsGroup; `runAsNonRoot`; RuntimeDefault seccomp; drop ALL and add none; no privilege escalation; read-only root; allowed volume types only; no service-account token; every image digest-pinned.
- **NetworkPolicies: complete.** Computed per workload by my `netpol_r3.py`:
  - every pod is isolated for both ingress and egress;
  - no rule has an empty or missing peer list, is port-less, uses `0.0.0.0/0` or has an empty selector.
  - Effective egress per role:

    | Role | Egress allowed |
    |---|---|
    | app, worker | DNS (53/5353), DB 5432, the 4 gateways on 443, the object store, the source DBs (1521/2484/5432/27017) |
    | browser worker | DNS, DB, object store, scan targets on 443 |
    | migrate Job and CronJob | DNS + DB |

  - Ingress: the app accepts the router on 8400 and monitoring. The workers accept monitoring only. The dev mocks accept the app and the worker only, and their egress is DNS only.
  - The init containers share their pod's DNS and `egress-dps-db` rules.
- **Secrets in ConfigMaps: none.**
  - My scan of every ConfigMap value and env literal (PEM, AKIA, URL userinfo, `password=`-style pairs, long base64/hex, JWT) found 0 hits in prod, release-example and base.
  - The only dev hit is the mock's code default inside `server.py`. The mock's real token comes from `secretKeyRef` of `dps-tokenizer-mock` (dev only).
  - `Settings` sets `hide_input_in_errors=True` and strips `_FILE` contents, so a waiting init container never prints a secret.
- **Placeholders that silently reach prod: none.**
  - Every zero digest, `.invalid` host, TEST-NET-1 peer and the name `dps-migrate-0` warns, and `--fail-on-warn` refuses each of them.
  - The derived `DPS_IMAGE_DIGEST`s match their images in the release example:

    | Container | Digest |
    |---|---|
    | Every app-image container, including the two app-image waits, the Job and the CronJob | `1…1` |
    | Both browser-worker containers | `2…2` |

  - The remaining settings are documented tunables: `DPS_S3_REGION`, `DPS_S3_BUCKET`, `DPS_LLM_MODEL`, retention.
  - Applying a placeholder render as is fails loudly: pull, DNS and egress errors, and an immutable Job on the next release.
- **Negative controls of the checker:**
  - Denied, as they should be: a privileged init container, `hostNetwork`, a missing `default-deny`, a KEK literal in the ConfigMap, a worker without the schema wait, and an `http://` tokenizer in prod (posture, three roles).
  - Not denied: see R3-N2.
- **Against the current `m4` head `a0e8d26`** (read-only export, Settings and role-aware posture of m4):
  - every DPS container of the prod, release and dev renders builds `Settings`, and no `DPS_*` name is unknown;
  - `posture_problems(role=…)` returns `[]` for app, worker and browser-worker;
  - the manifests survive the M4 merge as it stands now.
- **Against the current `fsec` head `fde5a94`:** the app and worker of prod and release-example fail the posture guard (R3-N1). The browser worker passes.
- **Dev mocks under the same emulation** (read-only root, arbitrary UID):
  - The Azure OCR mock (`document_intelligence-azure-ocr-mock:fimg`) answers `/health` 200. A Read v3.2 analyze returns 202 and then `succeeded`, and the test text is recognised.
  - The tokenizer mock (app image with `server.py` mounted read-only) answers `/health` 200.
  - Neither logged an error.

## 5. Plan acceptance

- **M5 Task 25: met.**
  - Every planned file exists. Prod has 3 replica patches instead of 1, and dev-mocks gains `networkpolicies.yaml`; both are logged.
  - The Wega layout uses `disableNameSuffixHash`.
  - The placeholders are listed in the README; `test_placeholders_are_documented` checks it.
  - All roles are present: app, worker, browser-worker, the migrate Job, plus the audit CronJob.
  - `dps-db` is an ExternalName, and the Route uses edge TLS.
  - The 9 plan tests are present (one renamed: `…_migrate_workloads_…`), plus 51 more.
  - HANDOFF §1, §7 and §15 and contract §1 and §11.1 are updated; the deviation log is complete; the commit message is the plan's.
- **DoD #10: met.** `kustomize build` of dev and prod succeeds, `tests/deploy` is green, and kubeconform reports 0 errors.
- **Programme plan §2.6, F2 row (the T25 part): met.**
  - "kustomize build dev+prod + kubeconform clean" was re-run above.
  - The arbitrary-UID run was emulated by the fixer and by me; there is no cluster.
  - amd64 images and the smoke test belong to T24 (fimg).

## 6. Findings

**Blocking: none.**

**Non-blocking, new in this round:**

- **R3-N1. Merge stage, from fsec F-11: `DPS_TOKENIZER_PROFILES` will be required in prod.**
  - `fsec` commit `e89ece8` adds a posture rule: with `DPS_TOKENIZER_PROVIDER=http`, staging/prod must set `DPS_TOKENIZER_PROFILES`. The rule exists only on `fsec`, which is not merged yet.
  - `configmap-dps.env` has `DPS_TOKENIZER_PROVIDER=http` and no `DPS_TOKENIZER_PROFILES`. Probing this tree's prod and release renders with fsec's code makes `dps-app` and `dps-worker` fail: "DPS_TOKENIZER_PROFILES must list the entity types and profiles each tenant may tokenize when the tokenizer is enabled".
  - After both branches merge, the pods would refuse to boot. `test_prod_configuration_passes_the_posture_guard` and the checker's `posture` deny will flag it at the merge.
  - **Fix whichever merge comes second:**
    - add the variable to `configmap-dps.env` with a committed placeholder that the checker warns on, so that `--fail-on-warn` forces a deliberate value;
    - add a README §4 parameter row;
    - add a literal to the release example.
    - Do not commit a permissive `{"*": [...]}` default.
- **R3-N2. Release-gate gaps.** Each was verified on a modified prod render, and the committed manifests have none of them.
  - An added allow-all NetworkPolicy (`egress: [{}]`) gives **0 denied**. Port-less rules, `0.0.0.0/0` peers and empty namespace or pod selectors are not checked either. On a cluster this would silently void `default-deny`.
  - Credentials embedded in a URL setting (`DPS_STELLAR_BASE_URL=https://user:pass@…`) in the ConfigMap give 0 denied, even with the posture probe. This is a plaintext secret in a ConfigMap.
  - `hostPort` is not denied. restricted-v2 refuses it at admission, so it would fail loudly.
  - Suggestion: deny rules for allow-all/any-CIDR/port-less peers and for URL userinfo in ConfigMap values and env literals.
- **R3-N3. Nothing alerts on a missing browser worker, and nothing says the two images must match.**
  - `DpsWorkersAbsent` covers only `component=worker`. A browser worker that is stuck in `Init:0/1` or crash-looping leaves scans queued with no alert. OpenShift's platform alerts do not cover user namespaces.
  - Each pod's wait verifies its own image's migrations (step D). So a release that bumps only one image with a new migration leaves every restarted pod of the other image waiting for good.
  - Suggestion: an `absent(up{app_kubernetes_io_component="browser-worker"} == 1)` alert, and a README line that both images are built from one commit.
- **R3-N4. The deploy gate can pass vacuously.** Without `kustomize`, `kubectl` or `oc` on PATH, `DPS_TEST_REQUIRE_SERVICES=1 pytest -m deploy` gives **3 passed, 58 skipped**. This is the plan's own skip rule, but DoD #10 and the DoD preamble ("a missing service fails, never skips") expect a real run. Suggestion: fail instead of skip when `DPS_TEST_REQUIRE_SERVICES=1`.
- **R3-N5. Docs and operations nits:**
  - README §2.2 names only the three login roles. The NOLOGIN groups `dps_app_rw` and `dps_worker` and the required memberships are only in contract §2.3. A failure there is loud (the Job fails, or the RLS posture refuses), but bank DBAs need them in the guide.
  - `DpsAuditMaintenanceStale` goes silent if the CronJob is deleted, because both of its series vanish. Add an `absent(kube_cronjob_info{cronjob="dps-migrate-maintain"})` branch.
  - README step 5's `oc wait --for=condition=complete` sits out the full 10 minutes when the Job has failed.
  - A migration that runs longer than the default `progressDeadlineSeconds` (600 s) makes `oc rollout status` error just after it, although the rollout then proceeds.
  - Carried r2-N3 (rollback provenance): the README could say "roll back by re-applying the previous release overlay, not `oc rollout undo`", which keeps the ConfigMap's `DPS_IMAGE_DIGEST` consistent.
- **R3-N6. Least privilege after M4.** m4's role-aware posture passes for the browser worker without API keys; my probe shows `serves=False` skips that rule. Once M4 merges, drop `dps-api-keys` from `dps-browser-worker`. This is r1/r2 N8 restated with current evidence. It is residual risk because Chromium runs without a sandbox.

**Carried (still open, owners as before):**

- r2-N3: rollback provenance.
- N7: `--proxy-headers` without `--forwarded-allow-ips` (F1).
- N8, the rest of it:
  - pass the role to the checker's posture probe;
  - `DPS_SCAN_STORE_UNMASKED_ORIGINALS`;
  - dev's `scan_registered_hosts` fallback, which allows `localhost`.
- N9:
  - spread;
  - opt-in RWX PVC;
  - `preStop`;
  - the app's grace of 30 s against the 300 s Route timeout.
- N10: `/health`, `/readyz` and `/metrics` are reachable through the Route (F1).
- N11: the flaky test (F4).
- For main (F1/F4): the app verifies migrations only once at boot (contract §2.1).

## 7. Notes for the merge and release stages

- **fsec:** R3-N1. Expect append-only doc conflicts in HANDOFF, M5-DEVIATIONS and the contract with fsec, fimg, fdemo, fdocs and m4. Keep both sides. fos merges into current main without overlap.
- **M4:**
  - `test_every_role_runs_a_module_the_package_has` and the browser-worker login test activate after the merge. The manifests already pass m4's posture.
  - The browser image must keep shipping `dps/migrations`, the same set as the app image, because its wait verifies its own image.
- **T24 (fimg):** keep `ENTRYPOINT` with `args` in the manifests. The `umask 0002` entrypoint already runs in every DPS container here.

## 8. Files

- Manifests: `/Users/neelu/document_intelligence/dps-wt-fos/deploy/openshift/`
- Checker: `/Users/neelu/document_intelligence/dps-wt-fos/tools/openshift_check.py`
- Tests: `/Users/neelu/document_intelligence/dps-wt-fos/tests/deploy/test_openshift.py`
- Review artefacts: `~/.cache/dps-build/reports/S5-review-r3/`
  - `render/`
  - `check-*.log`, `pytest-*.log`, `static.log`
  - `runtime-init-container.out` with `setup.sh`, `run.sh`, `dockerargs.py`, `cleanup.sh`
  - `psa_r3.py`, `netpol_r3.py`, `scan_cm.py`, `probe_m4.py`, `probe_fsec.py`
  - `prom/`
