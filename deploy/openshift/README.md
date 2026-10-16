# DPS on OpenShift (`deploy/openshift`)

Kustomize manifests for Data Privacy Services, in the Wega layout: a **base** that is identical in
every environment, **components** that overlays switch on, two **overlays** (`dev`, `prod`) and a
worked **release example**. Nothing here is applied as committed: the committed files carry
**placeholders** for what this repository cannot know (registry digests, hosts, network ranges, a
per-release Job name), and the deploy agent's **release overlay** — an untracked kustomization over
`overlays/prod` (or `overlays/dev`) — sets every one. `tools/openshift_check.py` warns on each
placeholder and, with `--fail-on-warn`, refuses a release overlay that still carries one.

```
deploy/openshift/
  base/                      dps-app, dps-worker, dps-browser-worker (Deployments), dps-migrate-0 (Job),
                             dps-migrate-maintain (CronJob: audit partitions, daily),
                             Services dps-app + dps-db (ExternalName), Route, PDBs, ServiceAccounts,
                             ConfigMap dps (configmap-dps.env: every non-secret setting)
  components/network-policies   default-deny + one allow per path a role uses
  components/monitoring         PodMonitor + PrometheusRule (user-workload monitoring)
  components/dev-mocks          tokenizer mock + Azure Read v3.2 OCR mock (dev only)
  overlays/dev               namespace dps-dev: 1 replica each, mocks, stub LLM, TLS egress relaxed
  overlays/prod              namespace dps-prod: app 3, worker 2, browser-worker 1, no mocks
  release-example/           a release overlay with every parameter set (documentation values)
```

Render: `kustomize build deploy/openshift/overlays/prod` (kustomize ≥ 5.0; `oc kustomize` works too).

## 1. What runs

| Object | Kind | Image / command | Port | Mounts (secrets) |
|---|---|---|---|---|
| `dps-app` | Deployment | `data-privacy-services`: `uvicorn dps.app:create_app --factory … --no-access-log`, after init container `wait-for-schema` | `http` 8400 | `dps-pg-app`, `dps-job-kek`, `dps-api-keys`, `dps-coin`, `dps-tokenizer`, `dps-ocr`, `dps-s3`, `dps-connector-refs`; PVC `dps-exports` (connection tests) |
| `dps-worker` | Deployment | `data-privacy-services`: `python -m dps.worker`, after `wait-for-schema` | `ops` 9400 | `dps-pg-app`, `dps-pg-worker`, `dps-job-kek`, `dps-api-keys`, `dps-coin`, `dps-tokenizer`, `dps-ocr`, `dps-s3`, `dps-connector-refs`; PVC `dps-exports` |
| `dps-browser-worker` | Deployment | `data-privacy-services-browser`: `python -m dps.browser_worker`, after `wait-for-schema` | `ops` 9401 | `dps-pg-app`, `dps-pg-worker`, `dps-job-kek`, `dps-api-keys`, `dps-s3`, `dps-connector-refs` (LLM, OCR and tokenizer are switched off here) |
| `dps-migrate-<release>` | Job | `data-privacy-services`: `python -m dps.migrate` (`DPS_MIGRATIONS_MODE=auto`) | — | `dps-pg-owner` only |
| `dps-migrate-maintain` | CronJob, daily 03:17 UTC | `data-privacy-services`: `python -m dps.migrate --maintain` (the migrate Job's pod, §6) | — | `dps-pg-owner` only |
| `dps-app` | Service + Route | edge TLS, HTTP redirected, router timeout 300 s | 8400 | — |
| `dps-db` | Service (ExternalName) | the platform PostgreSQL 16 (never deployed by DPS) | 5432 | — |

**Start order.** Compose starts the app and the workers only after its `migrate` one-shot has
completed; Kubernetes has no such order, and a release applies the migrate Job together with the
Deployments. So each of `dps-app`, `dps-worker` and `dps-browser-worker` has an init container
`wait-for-schema` that runs `python -m dps.migrate --verify` every 5 s until it passes: the
migrations its image ships, read with the app login (`dps-pg-app`; no DDL, no other secret), in the
pod's own image, security context and `/tmp`. A new pod therefore starts only once the release's Job
has committed. Without that wait an app pod that started first would keep its job platform down
until restarted (the app verifies the schema once, at boot: every database route answers 503
`db_unavailable` while the pod stays Ready), and the workers would exit on `MigrationDrift` and
crash-loop.

Every pod runs under OpenShift's `restricted-v2` SCC: no `runAsUser` (the namespace's arbitrary UID),
`runAsNonRoot`, `RuntimeDefault` seccomp, read-only root filesystem, no privilege escalation, all
capabilities dropped, no service-account token, `/tmp` an `emptyDir` and `HOME=/tmp`. Secret files
are mode `0440` (decimal `288` in the manifests): readable through the pod's `fsGroup`, which the SCC
assigns. Every secret is a **file** named by a `DPS_*_FILE` variable — the production posture guard
refuses a literal secret environment variable — and every image is pinned by digest.

The app and both workers boot through the same posture guard (`dps/posture.py`), so each needs the
static API key file (`DPS_API_KEYS_FILE`; sha256 entries are enough) and the job KEK; both workers
open the API pool as well as the worker pool at start (`dps/worker.py`, `dps/browser_worker.py`), so
they hold the app login and the worker login, each in its own file. The browser
worker sets `DPS_LLM_PROVIDER=""`, `DPS_OCR_PROVIDER=off`, `DPS_TOKENIZER_PROVIDER=""`: the scanner
calls none of them and holds none of their credentials.

Evidence packs record the image that produced them (`DPS_IMAGE_DIGEST`): the ConfigMap's value is the
app image's digest (app, worker, migrate), and the browser worker, which runs its own image and
stamps its scan packs, sets its own in its env (an `env` entry overrides the ConfigMap's). The release
overlay derives both from `images:` (§4).

## 2. What the platform provides first

1. A namespace (`dps-dev`, `dps-prod`) with **user-workload monitoring** enabled on the cluster (for
   the PodMonitor and the PrometheusRule), and no NetworkPolicy of its own: policies are additive,
   so one a project template adds (e.g. `allow-from-same-namespace`) re-opens what `default-deny`
   closes (the browser worker could then reach the app's port 8400). After the install,
   `oc get networkpolicy` lists only the policies of `components/network-policies`.
2. The **metadata database**: PostgreSQL 16, database `dps`, roles `dps_owner` (DDL), `dps_app`,
   `dps_worker_login` (contract §2.3; migration 001 also creates them when the owner may), TLS with a
   certificate for the host name DPS dials (`DPS_PG_HOST`, §4).
3. ConfigMap **`dps-ca-bundle`** with two keys: `pg-root.pem` (the database server's CA,
   `DPS_PG_SSL_ROOT_CERT`) and `org-ca.pem` (the organisation CA bundle for every TLS egress:
   `DPS_SSL_CERT_FILE` for the gateways, and `AWS_CA_BUNDLE` for the S3 client, which reads neither
   `SSL_CERT_FILE` nor `DPS_SSL_CERT_FILE`).
4. PVC **`dps-exports`** for `pvc` file targets (`DPS_EXPORT_ROOT=/mnt/dps-exports`): the worker
   writes the files, the app's connection test puts and deletes a probe object there. It is mounted
   by every app and worker replica, so it must be **ReadWriteMany**, and its storage must let the
   pod's `fsGroup` write (block volumes get it from the SCC; NFS needs a group-writable export).
5. An S3-compatible bucket `dps-artefacts` (`DPS_S3_BUCKET`), and network paths to every peer of §4.
   An `s3` file-target connection at another endpoint needs its own peer: add a rule (range + 443)
   to `egress-source-databases`, which selects the app and the worker.
6. The secrets, created from files (never a literal on a command line):

| Secret | Keys | Mounted by | Notes |
|---|---|---|---|
| `dps-pg-app` | `password` | app, worker, browser-worker | the `dps_app` login (`DPS_PG_PASSWORD_FILE`): both workers open the API pool at start too |
| `dps-pg-worker` | `password` | worker, browser-worker | the `dps_worker_login` login (`DPS_PG_WORKER_PASSWORD_FILE`) |
| `dps-pg-owner` | `password` | the migrate Job and CronJob `dps-migrate-maintain` only | the `dps_owner` (DDL) login |
| `dps-job-kek` | `kek` | app, worker, browser-worker | base64 of 32 random bytes; seals job keys and inline secrets |
| `dps-api-keys` | `api_keys.json` | app, worker, browser-worker | static keys, e.g. `[{"name":"ops","key_sha256":"<64 hex>","tenant_ids":["*"],"scopes":["admin"]}]`; day-to-day keys live in the database (§5) |
| `dps-coin` | `client_id`, `client_secret`, `scope` | app, worker | COIN OAuth2 client for the Stellar LLM gateway |
| `dps-tokenizer` | `token` | app, worker | bearer token of the bank tokenization service (any value when the release turns tokenization off with `DPS_TOKENIZER_PROVIDER=`) |
| `dps-ocr` | `key` | app, worker | `Ocp-Apim-Subscription-Key` of the OCR gateway |
| `dps-s3` | `access_key_id`, `secret_access_key` | app, worker, browser-worker | the artefact store |
| `dps-connector-refs` | one key per file | app, worker, browser-worker (optional) | connection `secret_ref` folders, see below |
| `dps-tokenizer-mock` | `token`, `key` | dev mock only | `token` equals `dps-tokenizer`'s in dev |

```bash
oc -n dps-prod create secret generic dps-pg-app --from-file=password=./dps_app.password
oc -n dps-prod create secret generic dps-job-kek --from-file=kek=./job.kek     # head -c32 /dev/urandom | base64
oc -n dps-prod create configmap dps-ca-bundle --from-file=pg-root.pem --from-file=org-ca.pem
```

Images from a registry other than the cluster's own need a pull secret, created from a registry
auth file and linked to every ServiceAccount that runs a DPS image:
`oc -n dps-prod create secret generic dps-pull --type=kubernetes.io/dockerconfigjson --from-file=.dockerconfigjson=./registry-auth.json`,
then `oc -n dps-prod secrets link <sa> dps-pull --for=pull` for `dps-app`, `dps-worker`,
`dps-browser-worker` and `dps-migrate`.

**Connector secret refs.** A connection's `secret_ref` names a folder
`DPS_SECRET_DIR/<tenant_id>/<ref>/` (`DPS_SECRET_DIR=/var/run/secrets/dps/refs`) holding `username`,
`password`, `dsn` (Oracle), `uri` (MongoDB), `access_key_id` + `secret_access_key` (S3),
`storage_state.json` (scans) or `ca.pem`; `_shared/<ref>/` with a `tenants` file serves several
tenants (HANDOFF §7). A Secret volume is flat, so the release overlay gives the `refs` volume an
`items` list mapping each key to its path, e.g. key `default.crm-ro.password` →
`path: default/dps/src/crm-ro/password` — or mounts the tree from the bank's secret store (CSI
secrets store, Vault agent) at the same path.

## 3. Deploy (prod)

1. `oc new-project dps-prod`; create §2's secrets, `dps-ca-bundle` and `dps-exports`.
2. Write the release overlay: copy `release-example/` outside the checkout, point `resources` at
   `deploy/openshift/overlays/prod` by a **relative** path (kustomize refuses an absolute one, e.g.
   `../data-privacy-services/deploy/openshift/overlays/prod`), and set every parameter of §4 (the
   example shows one JSON6902 `test` + `replace` per placeholder; keep the `test`s, and keep its
   `replacements` block, which derives `DPS_IMAGE_DIGEST` from `images:`).
3. Gate it: `python tools/openshift_check.py <release-dir> --fail-on-warn` → `0 denied, 0 warnings`
   (run from the repository's venv so it also builds each role's settings and runs the production
   posture guard on them; without `dps` importable it warns `settings-skipped`).
4. Apply: `kustomize build <release-dir> | oc apply -f -`.
5. Wait for the schema: `oc wait --for=condition=complete job/dps-migrate-<release> --timeout=10m`.
   Meanwhile every new app, worker and browser-worker pod waits in its init container
   `wait-for-schema` (`Init:0/1`; `oc logs <pod> -c wait-for-schema` names the pending migration
   files) and the previous release's pods keep serving (`maxUnavailable: 0`); each new pod starts
   within about 5 s of the Job's completion. If the Job fails, the new pods keep waiting and the
   rollout does not progress: read `oc logs job/dps-migrate-<release>`, fix the cause (forward),
   then delete that Job and apply the release again (a Job does not retry past `backoffLimit`).
6. `oc rollout status deploy/dps-app deploy/dps-worker deploy/dps-browser-worker`, then check that
   every app replica has its job platform: component `jobs` `ok` in
   `oc exec <pod> -c app -- python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8400/readyz').read().decode())"`
   for each `dps-app` pod. The app verifies the schema only at start; a replica that reports
   `migrations pending or drifted` does not recover by itself — restart it
   (`oc rollout restart deploy/dps-app`).
7. Bootstrap the first database key (printed once):
   `oc exec deploy/dps-app -- python -m dps.keys_cli create --name platform-admin --tenants '*' --scopes '*' --expires-in-days 90`
   and keep it in the secret manager (`DPS_BOOTSTRAP_API_KEY` stays unset everywhere).
8. Smoke through the route: `DPS_BASE_URL=https://<route host> DPS_API_KEY=<key> DPS_SMOKE_M2=off python tools/smoke_test.py`
   (`DPS_SMOKE_M2=off`: there is no demo source database in a bank namespace).

Each release renames the migrate Job (`dps-migrate-<release>`): a Job's pod template is immutable,
and the completed Job of the previous release stays as its record. The CronJob
`dps-migrate-maintain` keeps its name (a CronJob's template is mutable): the release updates its
image like the Job's. Once a release's Job has applied a migration, the previous image cannot run
against that schema (contract §2.1: migrations are never undone — fix forward): its pods would wait
in `wait-for-schema` for good (`applied but no longer shipped`), so `oc rollout undo` is only for a
release with the same migrations.

## 4. Deploy parameters (every placeholder)

`python tools/openshift_check.py deploy/openshift/overlays/prod` lists them as warnings; a release
overlay sets all of them. Values marked "same" must be equal (the release check denies a
difference, rule `db-host`).

| Parameter | Committed placeholder | Where it is set |
|---|---|---|
| app image | `image-registry.openshift-image-registry.svc:5000/dps-prod/data-privacy-services` @ `sha256:0000…0000` | release `images:` (`newName` + `digest`) |
| browser-worker image | `image-registry.openshift-image-registry.svc:5000/dps-prod/data-privacy-services-browser` @ `sha256:0000…0000` | release `images:` |
| `DPS_IMAGE_DIGEST` (ConfigMap `dps`) | `sha256:0000…0000` | **derived**: the release overlay's `replacements` copy the app image's digest from `images:` (app, worker, migrate Job and CronJob stamp it into evidence packs) |
| `DPS_IMAGE_DIGEST` (`dps-browser-worker` env: its container and its `wait-for-schema`) | `sha256:0000…0000` | **derived** the same way from the browser image's digest (its scan evidence packs); kustomize runs `replacements` after `images` in the same kustomization, so the block stays in the overlay that sets `images:` |
| `DPS_PG_HOST` | `postgres.example.invalid` | release `configMapGenerator: dps` — the platform PostgreSQL host, **same** as `dps-db`'s externalName |
| `dps-db` `externalName` | `postgres.example.invalid` | release patch on Service `dps-db` |
| `DPS_S3_ENDPOINT_URL` | `https://s3.example.invalid` | release `configMapGenerator: dps` |
| `DPS_STELLAR_BASE_URL` | `https://stellar.example.invalid` | release `configMapGenerator: dps` |
| `DPS_COIN_URL` | `https://coin.example.invalid/oauth2/token` | release `configMapGenerator: dps` |
| `DPS_AZURE_VISION_ENDPOINT` | `https://ocr-gateway.example.invalid` | release `configMapGenerator: dps` (with `DPS_OCR_HEALTH_PATH`, e.g. `/_gw/health` for the azure-gateway-api) |
| `DPS_TOKENIZER_URL` | `https://tokenizer.example.invalid` | release `configMapGenerator: dps` (or `DPS_TOKENIZER_PROVIDER=` to turn tokenization off) |
| Route host | `dps.apps.example.invalid` | release patch on Route `dps-app` |
| metadata DB peer | `192.0.2.10/32` TCP 5432 | NetworkPolicy `egress-dps-db` `/spec/egress/0/to/0` |
| Stellar LLM gateway peer | `192.0.2.1/32` TCP 443 | NetworkPolicy `egress-gateways` `/spec/egress/0/to/0` |
| COIN token mint peer | `192.0.2.2/32` TCP 443 | `egress-gateways` `/spec/egress/1/to/0` |
| OCR gateway peer | `192.0.2.3/32` TCP 443 | `egress-gateways` `/spec/egress/2/to/0` |
| tokenization service peer | `192.0.2.4/32` TCP 443 | `egress-gateways` `/spec/egress/3/to/0` |
| object store (S3) peer | `192.0.2.5/32` TCP 443 | NetworkPolicy `egress-object-store` `/spec/egress/0/to/0` |
| source / target databases | `192.0.2.32/27` TCP 1521, 2484, 5432, 27017 | NetworkPolicy `egress-source-databases` `/spec/egress/0/to/0` |
| scan targets (registered UAT hosts) | `192.0.2.64/27` TCP 443 | NetworkPolicy `egress-scan-targets` `/spec/egress/0/to/0` |
| migrate Job name | `dps-migrate-0` | release patch on Job `dps-migrate-0` (`/metadata/name` → `dps-migrate-<release>`) |

dev adds `image-registry.openshift-image-registry.svc:5000/dps-dev/azure-ocr-mock` (the OCR mock,
built from `../mock_azure_ocr`) and uses `…/dps-dev/data-privacy-services` and
`…/dps-dev/data-privacy-services-browser`; its tokenizer and OCR URLs point at the in-namespace mocks.

A peer may need more than one range or another port: add `ipBlock` entries or ports with JSON6902
`add` operations in the same release patch. An IngressController published with `HostNetwork`
reaches the app from the host network: add the peer
`namespaceSelector: {matchLabels: {policy-group.network.openshift.io/host-network: ""}}` to
`app-ingress-from-router` (and to `metrics-from-monitoring` if Prometheus runs there too). Tunables that are not placeholders (set them the same
way when the defaults do not fit): replicas, resources, the worker's `/tmp` size (it spools each
`files`-target partition before the upload), the Route's `haproxy.router.openshift.io/timeout`,
`DPS_S3_REGION`, `DPS_S3_BUCKET`, `DPS_LLM_MODEL`, `DPS_FPE_ENABLED` (FF1 after the licence review),
`DPS_RETENTION_DAYS`, `DPS_AUDIT_RETENTION_MONTHS` (default 13: the daily CronJob drops older audit
months, so set it to the bank's audit retention period).

## 5. The release check (`tools/openshift_check.py`)

| Level | Rules |
|---|---|
| **warn** (a placeholder or an example value) | `placeholder-digest` (an all-zero digest in an image or a setting), `placeholder-host` (a `.invalid` host anywhere, also inside a list), `placeholder-cidr` (a NetworkPolicy peer in `192.0.2.0/24`), `placeholder-job` (`dps-migrate-0`), `example-value` (the release example's `.example` hosts, TEST-NET-2/3 peers and `1…1`/`2…2` digests), `settings-skipped` |
| **deny** (always fails) | `pod-security`, `container-security`, `resources`, `probes` (Deployments; not the Job and CronJob), `image-digest`, `image-digest-setting` (a DPS container whose `DPS_IMAGE_DIGEST` is unset, not a literal, or not its own image's digest — the zero placeholder only warns), `literal-secret` (a literal secret variable — the posture guard's secret names, a library's credential (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `PGPASSWORD`) or a secret-like name —, a `DPS_*` from a `secretKeyRef`, an `envFrom` secret, a secret-named key in any ConfigMap), `secret-file` (a `*_FILE` outside a mounted Secret/ConfigMap), `owner-secret` (`dps-pg-owner` outside the migrate Job and CronJob `dps-migrate-maintain`), `service-account`, `default-deny`, `db-host` (`DPS_PG_HOST` not `dps-db`'s externalName), `schema-wait` (a Deployment that runs DPS without an init container that runs `python -m dps.migrate --verify` in the DPS container's image with the `dps` ConfigMap), `prod-mocks` (a mock or source system with `DPS_ENV` staging/prod), `posture` (each DPS container's settings — init containers included —, built from the ConfigMap and its env with dummy secret files, fail `Settings` or — the DPS container of a staging/prod Deployment — the production posture guard) |

Exit 0 = pass, 1 = a denial (or a warning with `--fail-on-warn`), 2 = the render failed. The
committed overlays pass with warnings only; `tests/deploy/test_openshift.py` (`pytest -m deploy`)
checks the overlays, the release example (only `example-value` warnings left) and the rules.
Optional schema check (needs network for the schemas):
`kustomize build deploy/openshift/overlays/prod | kubeconform -strict -summary -ignore-missing-schemas -`.

## 6. Operations

- **Alerts** (`components/monitoring`): `DpsRawPiiSentToLlm` (critical — must never fire),
  `DpsLlmExposureBlocked`, `DpsVerificationFailures`, `DpsTokenizerFailing`, `DpsNoDbKeysLoaded`
  (expected right after installation until §3 step 7), `DpsExactRowResamplesHigh` (info),
  `DpsWorkersAbsent` (critical), `DpsAuditMaintenanceStale` (below). The PodMonitor copies the role
  label onto every series (`app_kubernetes_io_component`).
- **Audit-log partitions** (CronJob `dps-migrate-maintain`): `dps_audit_log` has one partition per
  UTC month and no DEFAULT partition, and only the owner login creates them. The migrate Job makes
  this month and the next three; the CronJob runs `python -m dps.migrate --maintain` every day at
  03:17 UTC, which keeps three months ahead and drops the months older than
  `DPS_AUDIT_RETENTION_MONTHS` (13). Without it every audit write would fail about four months after
  the last release (`audit_flush_failed` in the logs; the audit search and report roll-ups stop).
  One run at a time (`concurrencyPolicy: Forbid`), each bounded to 30 minutes, 3 Jobs of history.
  `DpsAuditMaintenanceStale` (warning) fires when it has not succeeded for 3 days, or when a run
  has failed and none has ever succeeded; it reads kube-state-metrics' CronJob and Job series, which
  a rule in the namespace may query (not with the `leaf-prometheus` evaluation scope). OpenShift's
  kube-state-metrics drops `kube_cronjob_created`, so a CronJob that has never been scheduled is not
  seen: the day after installing, `oc get cronjob dps-migrate-maintain` shows a `LAST SCHEDULE`. To
  run it by hand:
  `oc create job --from=cronjob/dps-migrate-maintain dps-migrate-maintain-manual`.
- **KEK rotation** (HANDOFF §6): mount the new KEK in `dps-job-kek` as `kek` and the old one as
  `kek_previous`, add `DPS_JOB_KEK_PREVIOUS_FILE=/var/run/secrets/dps/kek/kek_previous` to the app and
  both workers (release patch, JSON6902 `add` to `/spec/template/spec/containers/0/env/-`), roll,
  then run `python -m dps.rekey` once (it re-seals through both pools, so it needs both logins: e.g.
  `oc exec deploy/dps-worker -- python -m dps.rekey`); `"unreadable": 0` → remove the previous KEK and roll.
- **Settings changes:** the ConfigMap `dps` has no name hash (the Wega layout), so pods keep the old
  values until `oc rollout restart deploy/dps-app deploy/dps-worker deploy/dps-browser-worker`.
- **Scaling:** workers scale horizontally (the queue is SKIP LOCKED with leases; every worker runs
  the reaper; retention is scheduled at most once per interval across replicas).
- **Probe and metrics endpoints** (`/health`, `/readyz`, `/metrics`) answer without a key on the app
  port and so are reachable through the Route; they carry component states and counters, never a
  value. Restrict them at the router if the Route is reachable beyond the bank's network.
- The image's entrypoint runs `dps.migrate` only for `DPS_MIGRATIONS_MODE=auto` with `uvicorn`; the
  ConfigMap sets `verify`, so only the Job changes the schema.
- **A pod in `Init:0/1`** waits for the schema its image needs (§1, start order): `oc logs <pod> -c
  wait-for-schema` prints one line per check, naming the pending or drifted migration files (names
  only; `migrations: failed (<error class>)` when the database cannot be reached). It needs the
  migrate Job of the same release to complete, or (`applied but no longer shipped`) a newer image.

## 7. dev

`overlays/dev` adds the two mocks (`dps-tokenizer-mock`, the app image running `components/dev-mocks/server.py`
— a byte-identical copy of `docker/tokenizer-mock/server.py`; `dps-azure-ocr-mock`), points
`DPS_TOKENIZER_URL` and `DPS_AZURE_VISION_ENDPOINT` at them over plain HTTP
(`DPS_EGRESS_TLS_REQUIRED=false`), uses the stub LLM and one replica of each role. NetworkPolicies
let only the app and the worker reach the mocks. Oracle, MongoDB, MinIO and the demo source
PostgreSQL are never deployed on OpenShift: they are bank systems, registered as connections.
