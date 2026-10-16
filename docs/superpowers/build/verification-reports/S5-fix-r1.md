# S5-fix-r1: blocking findings of S5-review-r1 (M5 Task 25, OpenShift manifests), branch `fos`

**Verdict: DONE.** Both blocking findings (B1, B2) are fixed test-first. Seven cheap non-blocking items (N1–N6, N12) are fixed too. Every gate is green.

- Commit `0b6a1e0`: `fix(fos): audit partitions are maintained daily; each container stamps its own image digest`.
  - Author `NilenduGanguli <nilendu.ganguli@gmail.com>`, no trailer.
  - 13 explicit paths; the tree is clean afterwards.
  - Parent `d397a4e`.
- gitleaks (the autopush Docker command) on `d397a4e..0b6a1e0`: **no leaks found**.
- Pushed by the owner's checked autopush loop: `origin/fos` = `0b6a1e0`. I never ran `git push`.
- Worktree: `/Users/neelu/document_intelligence/dps-wt-fos`. Every Python tool ran through `./lane.sh`.
- Logs: `~/.cache/dps-build/s5-fix-r1/`.
  - `red.log`: 22 new or changed tests failing before the fix.
  - `green-*.log`
  - `pytest-full*.log`

## 1. Test-first evidence

I changed or added the tests in `tests/deploy/test_openshift.py` first. The red run had **22 failed, 30 passed, 1 skipped**, and each failure had the intended cause:

- the CronJob was missing;
- `oc.is_secret_setting`, `oc.SECRET_SETTINGS` and the rules `image-digest-setting` and `db-host` did not exist (`0 == 5`, `[] == [('deny', …)]`);
- `AWS_CA_BUNDLE` was missing from the ConfigMap;
- the release example had no `replacements`;
- the browser worker stamped `a…a` (the app digest) where `b…b` was expected — B2 itself;
- the alert was missing;
- the host regex found 1 of 3 hosts in a list.

After the fix: **52 passed, 1 skipped**. The skip is `dps.browser_worker`, which arrives with the M4 merge.

## 2. B1: audit partitions between releases

**Root cause.** These facts were re-read in the code:

- `dps_audit_log` (migration 007) has monthly RANGE partitions and no DEFAULT partition.
- `run_migrations` creates partitions for this month plus the next 3.
- `--maintain` does the same and also drops months older than `DPS_AUDIT_RETENTION_MONTHS`.
- The migrate Job runs once per release.

**Fix.** A new base CronJob, `deploy/openshift/base/cronjob-migrate-maintain.yaml`, named `dps-migrate-maintain`:

| Field | Value |
|---|---|
| args | `["python", "-m", "dps.migrate", "--maintain"]` |
| `schedule` | `"17 3 * * *"` |
| `timeZone` | `Etc/UTC` (the partitions are UTC months) |
| `concurrencyPolicy` | `Forbid` |
| `startingDeadlineSeconds` | 3600 |
| `successfulJobsHistoryLimit` / `failedJobsHistoryLimit` | 3 / 3 |
| `backoffLimit` | 3 |
| `activeDeadlineSeconds` | 1800 (with `Forbid`, a hung run would otherwise block every later run) |

- Its pod template is the migrate Job's, except for the argument. A test asserts that the two templates are equal apart from `--maintain`. That template has:
  - SA `dps-migrate`, no token, no service links;
  - only `dps-pg-owner` and `dps-ca-bundle`;
  - the same security context, resources and `/tmp`;
  - labels `name` + `component: migrate`, so `egress-dps-db` and `allow-dns` cover it (test via `_selected`).
- The name is stable: a CronJob's template is mutable, so the release updates its image through `images:`.

**Checker (`tools/openshift_check.py`).**

- `owner-secret` accepts `Job/dps-migrate-*` and exactly `CronJob/dps-migrate-maintain` (new `is_migrate_workload`).
- `probes` exempts Job and CronJob (`RUN_TO_COMPLETION`).
- A test shows that a renamed CronJob (`dps-reports`) and a Deployment with the owner volume are both denied.

**Alert (the reviewer's optional item).** `DpsAuditMaintenanceStale`, severity warning, `for: 1h`. It fires when either is true:

- `kube_cronjob_status_last_successful_time{cronjob="dps-migrate-maintain"}` is older than 3 days;
- `kube_cronjob_created` is older than 3 days and there has been no success.

This makes a failing CronJob loud long before the roughly 3-month horizon. It relies on OpenShift letting a user-namespace rule read platform metrics for its own namespace; that does not hold with the `leaf-prometheus` scope, which the README states.

**Docs.**

- README §1 (table row), §2 (owner secret row), §3 (CronJob name note), §6 (operations: what the CronJob does, the alert, a manual run with `oc create job --from=cronjob/…`).
- HANDOFF §1 and §15.
- Contract §11.1.
- M5-DEVIATIONS: new section "S5 review r1 fix".

**Proofs.**

- **Schemas:** `kubeconform -strict` passes with 0 errors and 0 skipped on dev 33/33, prod 26/26 and release-example 26/26, against:
  - Kubernetes 1.27 (OCP 4.14) and 1.29 schemas;
  - the OpenShift **v4.14 and v4.16** standalone-strict catalogs, plus datree CRDs.

  Negative control: a `timeZonee` typo is refused (`additional properties 'timeZonee' not allowed`), which shows the CronJob really is validated.
- **Renderers:** kustomize 5.8.1 and `kubectl kustomize` 5.7.1 render byte-identical output for dev, prod and release.
- **Alert rules:**
  - `promtool check rules`: SUCCESS, 8 rules.
  - `promtool test rules`: SUCCESS on five cases — last success 4 days ago fires; 1 day ago is silent; never succeeded 5 days after creation fires; created 1 day ago is silent; daily successes stay silent.
- **Runtime proof (no cluster).** I ran the rendered **prod CronJob pod spec** with Docker, image `data-privacy-services:fimg`:
  - **Emulation:** UID 1000680000:0 plus the fsGroup, `--read-only`, `--cap-drop ALL`, no-new-privileges, tmpfs `/tmp`. The owner secret was a `root:fsGroup` 0440 file. The database was a scratch TLS PostgreSQL 16 whose certificate names only `dps-db` (verify-full), with the platform roles from `docker/dps-db/initdb`.
  - **Empty database:** `migrations: applied 17`, then `audit partitions: ensured 3 months ahead, dropped 0`; partitions `2026_10`…`2027_01`.
  - **The B1 failure, reproduced:** an audit insert dated 2027-02-01 failed with `ERROR: no partition of relation "dps_audit_log" found for row`.
  - **Same-day rerun:** `applied 0 (none pending)`, dropped 0 (idempotent).
  - **Clock at 2026-11-14:** the same command, with `dps.db`'s clock moved, created `2027_02`, and the 2027-02-01 insert then succeeded (`INSERT 0 1`).
  - **Clock at 2027-12-02:** it created `2027_12`…`2028_03` and dropped `2026_10` (retention of 13 months enforced). The 2027-02 row was kept.
  - **Log scan of its output:** 0 lines with a DSN, a password or a traceback.
  - **Cleanup:** all `dps-wt-fos-s5fix*` containers, volumes and the network were removed.

## 3. B2: one image digest per container

- **Base:** `dps-browser-worker` now sets its own `DPS_IMAGE_DIGEST` env entry (the zero placeholder). An env entry takes precedence over `envFrom`. The ConfigMap's value stays the app image's digest, which app, worker, migrate Job and CronJob read.
- **Release example:** both values are derived from `images:` with kustomize `replacements`. The source is each Deployment's container image with `options: {delimiter: "@", index: 1}`; the targets are `data.DPS_IMAGE_DIGEST` and the browser worker's `env.[name=DPS_IMAGE_DIGEST].value`.
  - The `DPS_IMAGE_DIGEST` literal was removed.
  - The browser image now has a distinct example digest, `2…2`. The checker's `EXAMPLE_DIGESTS` flags it, like `1…1`.
  - I verified that kustomize runs replacements after `images` in the same kustomization.
  - An outer overlay that changes images again leaves the values stale, and the new rule denies that.
  - A tag-only image fails the render (`options.index 1 is out of bounds`).
- **New deny rule `image-digest-setting`.** It applies to every container that reads the `dps` ConfigMap or sets `DPS_IMAGE_DIGEST`. The effective value (ConfigMap, then env) must:
  - be set;
  - be a literal (a `valueFrom` is denied);
  - equal the container's own `@sha256:` digest.

  The zero placeholder only warns (`placeholder-digest`). An image without a digest is left to `image-digest`.
- **Proofs:**
  - The reviewer's own overlay (`s5r1/release-real`: app `a…a`, browser `b…b`, `DPS_IMAGE_DIGEST=c…c`) used to give `0 denied, 0 warnings`. It now gives **4 denied** (app, worker, Job and CronJob containers) **+ 1 warning** (the browser worker's zero placeholder), exit 1 even without `--fail-on-warn`.
  - A fully parameterised release (the example with real-looking values) gives `oc.check(...) == []` with the Settings/posture probe and `--fail-on-warn` exit 0. The browser worker stamps `b…b`; everyone else stamps `a…a`.
  - Removing the browser worker's entry denies exactly that container.
- **Docs:** README §1 (provenance paragraph), §3 step 2 (keep the `replacements` block), §4 (two derived rows), §5 (rule). HANDOFF §15, contract §11.1, deviation log.

## 4. Non-blocking items

**Fixed (cheap, same files, each tested):**

- **N1:** `AWS_CA_BUNDLE=/etc/dps/ca/org-ca.pem` in the ConfigMap, equal to `DPS_SSL_CERT_FILE`, with a test. README §2.3 updated. The code-side `verify=` is still F1/F2's.
- **N2:** README step 2 and the example header now say "relative path".
- **N3:** README §3 step 5 now says that the worker exits with `MigrationDrift` and restarts until the Job completes, and that the app shows `jobs: migrations pending or drifted`.
- **N4:** `literal-secret` also matches `SECRET_SETTINGS`. A test keeps it a superset of `dps.posture.SECRET_ENV` and shows denials for `DPS_COIN_CLIENT_ID`, `DPS_S3_ACCESS_KEY_ID` and `DPS_JOB_KEK_PREVIOUS`.
- **N5:** new deny rule `db-host`: `DPS_PG_HOST` must equal `dps-db`'s externalName.
- **N6:** the host regexes now end at `(?![A-Za-z0-9.-])`, so hosts inside lists are found.
- **N12:** README now covers the pull secret: a `dockerconfigjson` built from a file, plus `oc secrets link … --for=pull`.

**Carried:**

- **N7:** `--proxy-headers` without `--forwarded-allow-ips` is an F1 decision.
- **N8:** M4 merge follow-ups — role-aware posture, which lets the browser worker drop `dps-api-keys`; `DPS_SCAN_STORE_UNMASKED_ORIGINALS`; dev `scan_registered_hosts`.
- **N9:** topology spread, an opt-in exports PVC, and `preStop`.
- **N10:** already carried.
- **N11:** the flaky `test_one_audit_event_per_stream`, for F4. It did not recur in my 2 full runs.

## 5. Gates

| Gate | Result |
|---|---|
| `./lane.sh pytest -q -m deploy tests/deploy` | **52 passed, 1 skipped** |
| `./lane.sh pytest -q` (full unit suite, 2 runs) | **2190 passed, 488 skipped** (was 2174 plus 16 new tests) |
| `ruff format --check dps tests tools` / `ruff check dps tests tools` | 378 files formatted / all checks passed |
| `mypy dps` / `mypy tools/openshift_check.py` | no issues (134 files / 1 file) |
| `tools/openshift_check.py` on committed prod / dev | 0 denied, 24 warnings each, exit 0; `--fail-on-warn` exit 1 |
| Release example | 0 denied, only `example-value` warnings (23) |
| kubeconform, kustomize/kubectl, promtool | see §2 |
| PSA restricted (the reviewer's `psa_check.py`) on all three renders | 0 rule violations; the only hits are the name heuristics the reviewer already classified as non-secret |
| gitleaks on the commit | no leaks found |

## 6. Notes for the merge stage and other streams

- **M4 merge:** `dps/layers/scanner/inventory.py:734` on m4 reads `settings.image_digest`, which the browser worker's own env entry now supplies. The browser-worker test that skips today will activate.
- **fdocs (T27):** HANDOFF §1 and §15 changed again. Keep §15's new bullets (audit-log partitions, image digest per container, the verification numbers).
- **Alert caveat:** `DpsAuditMaintenanceStale` assumes that OpenShift user-workload rules can read kube-state-metrics for their namespace. That is the documented default (Thanos Ruler), but it was not tested on a cluster.
- **Not done:** no live cluster apply; there is no cluster.

## 7. Files

- `deploy/openshift/base/cronjob-migrate-maintain.yaml` (new)
- `base/kustomization.yaml`, `base/job-migrate.yaml`, `base/deployment-browser-worker.yaml`, `base/configmap-dps.env`
- `components/monitoring/prometheusrule.yaml`
- `release-example/kustomization.yaml`
- `deploy/openshift/README.md`
- `tools/openshift_check.py`
- `tests/deploy/test_openshift.py`
- `HANDOFF.md`
- `docs/superpowers/plans/2026-10-11-m1-m5-contract.md`
- `docs/superpowers/build/M5-DEVIATIONS.md`

Proof scripts (scratchpad, not committed): `…/scratchpad/s5fix/ocpbin/{setup.sh,args.py}`, `…/s5fix/prom/{rules.yaml,test.yaml}`, `…/s5fix/render/`.
