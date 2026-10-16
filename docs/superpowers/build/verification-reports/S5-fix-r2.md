# S5-fix-r2: blocking finding of S5-review-r2 (M5 Task 25, OpenShift manifests), branch `fos`

**Verdict: DONE.** B1 is fixed test-first, and the fix is proven at runtime in a container emulation of
OpenShift's restricted-v2. The cheap non-blocking items N1, N2, N5 and N6 are fixed in the same files;
N4 is closed by B1. Every gate is green.

- Commit `866da65` `fix(fos): each DPS Deployment waits in an init container for its release's schema`
  - Author `NilenduGanguli <nilendu.ganguli@gmail.com>`; `git interpret-trailers --parse` returns nothing.
  - 11 explicit paths; parent `0b6a1e0`; the tree is clean afterwards.
- Pushed by the owner's checked `tools/dev/autopush.sh` (author check, trailer check, gitleaks):
  `pushed fos 866da65`, `held=0`. I never ran `git push`.
- Worktree `/Users/neelu/document_intelligence/dps-wt-fos`. Every Python tool ran through `./lane.sh`.
  No compose project was started; the only containers were the proof's own (`dps-wt-fos-s5fix2-*`),
  all removed (0 containers, volumes or networks left).
- Logs, renders and proof scripts: `~/.cache/dps-build/s5-fix-r2/`
  - `red.log`, `red-n1n2.log`, `green-*.log`, `pytest-full*.log`, `static*.log`
  - `check-*.log`, `kubeconform*.log`, `psa.log`
  - `prom/`, `render/`
  - `proof/` (`setup.sh`, `pg.sh`, `args.py`, `proof.sh`, `cleanup.sh`, `proof.out`)

## 1. Test-first evidence

The tests changed first in `tests/deploy/test_openshift.py`. The red run had **15 failed, 44 passed,
1 skipped**, each failure for the intended reason:

- no Deployment had an init container (`set() == {'dps-app', 'dps-browser-worker', 'dps-worker'}`);
- `schema_wait()` found `[]`, expected `['wait-for-schema']`;
- `settings_problems()` had no `container` argument;
- `stamped_digests` lacked the three `*/wait-for-schema` keys;
- the digest denials counted 5, expected 7.

The N1 and N2 tests (added later) were red too: 2 failed. N2 denied 0 of 4 library credentials; N1 was
still on `kube_cronjob_created`. After the fix: **60 passed, 1 skipped**. The skip is `dps.browser_worker`,
which arrives with the M4 merge.

## 2. B1: the release's schema before the pods

**Root cause, re-read in the code.** `dps/app.py` `_JobPlatform._connect` treats `MigrationDrift` from
`verify_migrations` as "pending or drifted". It then sets `db_state="down"` and starts no retry: only an
exception starts `_reconnect`. Compose avoids this with `migrate: service_completed_successfully`.
Kubernetes has no such ordering, and the base manifests added none.

**The fix: init container `wait-for-schema` in `dps-app`, `dps-worker` and `dps-browser-worker`.**

| Field | Value |
|---|---|
| image | the pod's own image. The browser worker's is the browser image, so it verifies the migrations its own code ships. |
| args | `["sh", "-c", "trap 'exit 143' TERM INT; until python -m dps.migrate --verify; do sleep 5 & wait $!; done"]`. These are `args`, not `command`, so the app image's entrypoint stays; in verify mode it only `exec`s them. |
| `envFrom` | ConfigMap `dps` |
| env | `HOME=/tmp` and `DPS_PG_PASSWORD_FILE` only. The browser worker's init container also has its own `DPS_IMAGE_DIGEST`. |
| mounts | `tmp`, `ca`, `pg-app`. `--verify` opens the API pool, which uses the app login. No KEK, no API keys, never the owner login. |
| security context and resources | the main container's security context; requests 50m/64Mi, limits 500m/256Mi |

**Deviation from the review's text: the `trap` and `sleep 5 & wait $!`.** A shell running as PID 1
ignores SIGTERM, so a waiting pod that is deleted would hang for its whole grace period. Measured with
`docker stop -t 20`:

- the review's plain loop: 20 s, then SIGKILL (exit 137);
- with the trap: under 1 s (exit 143).

**Release example.** Its `replacements` also derive the browser init container's `DPS_IMAGE_DIGEST`:
`spec.template.spec.initContainers.[name=wait-for-schema].env.[name=DPS_IMAGE_DIGEST].value`.

**Release check (`tools/openshift_check.py`).**

- New deny rule **`schema-wait`**. It applies to any long-running workload (not a Job or CronJob) whose
  DPS container reads the `dps` ConfigMap. That workload needs an init container that:
  - runs a command line containing `python -m dps.migrate --verify`;
  - uses the DPS container's image;
  - reads the ConfigMap.

  A plain `--verify` without the loop also passes, because the kubelet retries a failed init container.
- `configuration_findings` now builds `Settings` for every DPS container, init containers included, via
  `settings_problems(..., container=...)`. The posture guard still runs only on each Deployment's DPS
  container. `image-digest-setting` and the hardening rules already covered init containers.

**Tests.** 5 new tests (8 cases) and 8 changed ones.

- The new tests check that:
  - every Deployment that reads the `dps` ConfigMap has exactly the wait, with its exact args, the main
    container's image, `envFrom`, mounts and security context, and resources no larger than the main
    container's (dev, prod, release example);
  - no Job or CronJob has an init container, and the app's and the worker's waits are identical;
  - the wait holds only the login its check opens (`_opens(db.verify_migrations) == {"app"}`), and
    `migrate._run` calls `db.verify_migrations`;
  - the release check denies a missing wait, a wait that verifies nothing, and a wait in another image;
  - the wait's `Settings` are built, and a bad value is denied as `Deployment/dps-worker container wait-for-schema`.
- The digest tests now cover the init containers. Without its own entry, the browser worker's wait
  inherits the app digest and is denied.
- The pod-security test checks probes on main containers only. The API server refuses probes on an init
  container, and the test now asserts that the init containers have none and no `restartPolicy`.

**Docs.**

- README §1: a new "Start order" paragraph.
- README §3:
  - step 5: new pods wait in `Init:0/1`, the previous pods keep serving, and a failed Job stops the rollout;
  - step 6: check component `jobs` on every app replica, because the app does not recover by itself
    (the review's fallback, kept as a check);
  - a rollback note: once a release has applied a migration, the older image waits for good
    (`applied but no longer shipped`), so fix forward.
- README §4 (the browser-worker digest row), §5 (the `schema-wait` rule; `posture` now includes init
  containers) and §6 (`Init:0/1`).
- HANDOFF §1 and §15, contract §11.1, and `M5-DEVIATIONS.md` section "S5 review r2 fix".

## 3. Runtime proof (no cluster)

`proof/proof.sh` (output `proof/proof.out`) runs each container of **this tree's rendered prod pods**
under Docker:

- an arbitrary UID 1000680000 with group 0 and the fsGroup, `--read-only`, `--cap-drop ALL`,
  no-new-privileges, and each container's cpu/memory **limits**;
- secrets as `root:fsGroup` 0440 files;
- a scratch TLS PostgreSQL 16 whose certificate names only `dps-db`;
- the app image `data-privacy-services:fimg` (ID `3526231aa034`; its 17 migration files are
  byte-identical to this tree's) and the browser image `data-privacy-services-browser:m4`
  (`de9239ea23e3`, 19 migrations).

**A1, control: the app without the wait (the manifests before this fix).**

1. An "old release" Job (this tree's migrations minus 045) applied 16.
2. The app started: `/readyz` 200 with `jobs: (False, 'migrations pending or drifted')`, and
   `GET /api/v1/jobs` **503** `db_unavailable`.
3. The release Job applied `045_policy_directory.sql`.
4. 25 s later, still **503**. The review's race is reproduced in containers.

**A2, the fix, same steps.**

1. The app's and the worker's `wait-for-schema` kept checking: 4 checks each in 20 s, every line
   `migrations_mode=verify: 045_policy_directory.sql: pending`.
2. The release Job applied 045.
3. The waits exited 0 four and one seconds later (`migrations: verified`).
4. The containers started after them were ready at once:
   - app `/readyz` 200 with `jobs: (True, 'ready')`, and `GET /api/v1/jobs` **200**;
   - worker `/readyz` 200 (db, migrations, kek, queue).
5. One verify under the wait's limits (0.5 CPU, 256 MiB) took 929 ms with a cgroup `memory.peak` of 34 MiB.

**B, the browser image.**

1. Its wait looped on an empty schema; every check named all files as pending.
2. `docker stop -t 30` on a second waiting instance returned in 0 s (exit 143).
3. The image's own `dps.migrate`, run in the Job's spec, applied 19.
4. The wait exited 0 five seconds later.
5. The browser worker then answered `/readyz` 200 (`api-core`, `browser`).

In that image `sh` is dash and `python` is `/opt/venv/bin/python`.

**Log scan** of all 13 container logs: KEK 0, API key 0, DSN 0, Traceback 0.

## 4. Non-blocking items

**Fixed:**

- **N1** `DpsAuditMaintenanceStale`. Its "never succeeded" branch used `kube_cronjob_created`, which
  OpenShift's kube-state-metrics drops. It now fires on
  `max by (namespace)(kube_job_status_failed{job_name=~"dps-migrate-maintain-.*"}) > 0 unless on (namespace) kube_cronjob_status_last_successful_time{…}`,
  with `for: 1h`. The summary was reworded.
  - `promtool check rules`: SUCCESS, 8 rules.
  - `promtool test rules`: SUCCESS on 6 cases, using OpenShift-like series (no `*_created`):
    - a failed run that never succeeded: silent at 80 min, fires at 100 min and at 5 days;
    - a CronJob that has not run yet: silent;
    - a success 4 days old: fires;
    - a success 1 day old: silent;
    - an old failed Job in the history followed by daily successes: silent;
    - a run still retrying: silent.
  - Residual gap, documented in README §6: a CronJob that is never scheduled at all.
- **N2** `literal-secret` now checks every key of every ConfigMap, not only `DPS_*`. It also checks the
  libraries' credentials (`LIBRARY_SECRETS`: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
  `AWS_SESSION_TOKEN`, `PGPASSWORD`), including in env entries. `AWS_CA_BUNDLE` stays allowed. The
  test shows 4 denials.
- **N4** is closed by B1: the browser worker now waits for the schema.
- **N5** README §2: the namespaces carry no NetworkPolicy of their own (project-template allows are
  additive).
- **N6** README §4 names `DPS_AUDIT_RETENTION_MONTHS`.

**Carried:**

- **N3**: the app-image containers, now including their waits, take `DPS_IMAGE_DIGEST` from the shared
  ConfigMap, so a rollback stamps the newer digest. The fix would be a per-container env entry derived
  by `replacements`.
- **N7–N11** as in r1.
- **Follow-up for main (F1/F4):** in verify mode the app should keep re-verifying until the schema is
  verified, or exit, as contract §2.1 says. The init container closes the race on OpenShift; it does not
  change `dps/app.py`.

## 5. Gates

| Gate | Result |
|---|---|
| `./lane.sh pytest -q -m deploy tests/deploy` | **60 passed, 1 skipped** (was 52) |
| `./lane.sh pytest -q` (full unit suite, run twice) | **2198 passed, 488 skipped** |
| `ruff format --check` / `ruff check` (dps tests tools) | 378 files formatted / all checks passed |
| `mypy dps` / `mypy tools/openshift_check.py` | no issues (134 files / 1 file) |
| `tools/openshift_check.py` | prod and dev: 0 denied, 28 warnings each; release example: 0 denied, 27 warnings (`example-value` only). Exit 0 for all three; `--fail-on-warn` exit 1 for all three. A fully parameterised release gives `check() == []` with the Settings and posture probe (test). |
| kubeconform `-strict` (v0.8.0), OpenShift **4.14 / 4.16 / 4.19** standalone-strict catalogs + datree CRDs | dev 33/33, prod 26/26, release 26/26 valid, **0 skipped** |
| kubeconform `-strict`, Kubernetes **1.27 / 1.29** + OCP Route + datree | the same, 0 skipped |
| kubeconform negative control | an unknown field in an init container is refused (Invalid 1) |
| kustomize 5.8.1 vs `kubectl kustomize` 5.7.1 | byte-identical for dev, prod and release |
| PSA restricted / restricted-v2 (the reviewer's `psa_r2.py`, which covers init containers) | 0 rule violations; only the 2 name heuristics already classified as non-secrets |
| `promtool check rules` / `test rules` | SUCCESS / SUCCESS (6 cases) |
| autopush (author, trailers, gitleaks) | `pushed fos 866da65`, held 0 |

## 6. Notes for the merge stage and other streams

- **M4 merge.** The browser worker's wait runs the browser image. That image has no ENTRYPOINT;
  `/usr/bin/sh` is dash and `python` is the venv's. `dps.migrate` comes from `/app` (the working
  directory) or from site-packages; both ship the 19 migrations. Once `dps.browser_worker` arrives, the
  skipped test activates. Nothing else in these files depends on M4.
- **Image digests.** The release example derives three entries now: the ConfigMap's, and the browser
  worker's for its container and for its wait. A release overlay must keep the whole `replacements`
  block.
- **fdocs (T27).** HANDOFF §1 and §15 changed again:
  - a new "Start order" bullet;
  - the alert's semantics;
  - `literal-secret`;
  - the r2 verification numbers and runtime proof.

  Keep them when reconciling.
- **Not done:** no live cluster apply (there is no cluster).

## 7. Files (all under `/Users/neelu/document_intelligence/dps-wt-fos/`)

- `deploy/openshift/base/deployment-app.yaml`, `deployment-worker.yaml`, `deployment-browser-worker.yaml`
- `deploy/openshift/release-example/kustomization.yaml`
- `deploy/openshift/components/monitoring/prometheusrule.yaml`
- `deploy/openshift/README.md`
- `tools/openshift_check.py`
- `tests/deploy/test_openshift.py`
- `HANDOFF.md`
- `docs/superpowers/plans/2026-10-11-m1-m5-contract.md`
- `docs/superpowers/build/M5-DEVIATIONS.md`
