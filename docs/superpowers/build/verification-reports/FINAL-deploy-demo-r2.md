# FINAL — v1.0.0 candidate on `main` — LENS "deploy-demo", round 2

**Verdict: PASS. No blocking item.** Both round-1 blockers are closed:
- **B1 (finalize could not open the sealed policy):** fixed. The gates verifier's demo run on this stack passes S1–S12 in **3:02**. I checked the report against the live API: every job, evidence pack and scan it names exists, with the stated state and counts.
- **B2 (stale deck):** fixed. The committed deck is now the v1.0.0 build. I traced each number on it to a live evidence pack of the `c7ad14f` rehearsal.

The rest of my lens is green on `d033dd3`:
- kustomize dev and prod build;
- kubeconform `-strict` is clean, with 0 skipped in the stronger run;
- both images build for amd64 from HEAD, and their code is byte-identical to HEAD;
- the images pass the arbitrary-UID and read-only-root runtime tests on amd64;
- the deck test passes (43), the deck is present, and it renders all 12 slides.

| | |
|---|---|
| Date | 2026-10-16, 12:30–12:41 IST |
| Tree | main checkout, `main` @ **`d033dd3`** (= `origin/main`). The tracked tree is clean. The only untracked entry is `docs/screenshots/v1.0.0/`, which belongs to another verifier; I left it untouched. `git status` was the same at the start and at the end. |
| Stack | The gates verifier's `full` stack. I did not restart or rebuild it. I did not run `up` or `down`, and did not run the full suite. `/readyz` reported `ready: true` at the end, with 9/10 components ok (`ner` is patterns-only, by design). Other round-2 verifiers were using the stack at the same time, so I did **not** re-run `e2e_demo.py` (§3). |
| Probes | `~/.cache/dps-build/reports/FINAL-deploy-demo-r2/`: <br>• scripts: `build_amd64.sh`, `kube.sh`, `sec_probe.py`, `ident_in.py`, `ident_cmp.py`, `demo_xcheck.py`, `deck_probe.py`, `deck_provenance.py` <br>• outputs: renders, `kubeconform.log`, `oscheck-*.log`, `release/` (the realistic release overlay), build logs, `runtime-amd64.log`, `ident-*.txt`, `stack-hardening.txt`, `manifest-security.txt`, `bundle-compare.txt`, `pytest-deploy.log`, `deck-test.log`, `deck-probe.log`, `deck-provenance.log`, `deck-render/`, `demo-xcheck.log`, `demo-screens.txt`, `readyz-end.txt` |
| Repo writes | None. Every pytest and probe ran with `PYTHONDONTWRITEBYTECODE=1`, and every pytest also with `-p no:cacheprovider`. `python-pptx` came from round 1's private target, `FINAL-deploy-demo-r1/pydeps`. The other packages in that target have the same versions as `.venv`: typing_extensions 4.16.0, pillow 12.3.0, lxml 6.1.3. |
| Values | Only ids, counts, states, digests and file hashes appear here. The probes loaded the demo key file through `tools.demo_client.load_keys` (mode 0600 checked). Keys went straight into the client; no key or value was printed. |

## 1. OpenShift manifests (DoD 10) — PASS

| Check | Result |
|---|---|
| `kustomize build deploy/openshift/overlays/{dev,prod}` and `release-example` (kustomize v5.8.1) | exit 0. dev 33 objects, prod 26, release-example 26. All three are **byte-identical to round 1's renders**: `deploy/` did not change after `45be8ac`. |
| DoD command `kubeconform -strict -summary -ignore-missing-schemas` (v0.8.0) | dev 30 valid, 3 skipped; prod 23 valid, 3 skipped; release-example 23 valid, 3 skipped. **0 invalid, 0 errors, rc 0** each. |
| **Stronger:** `-strict` with k8s **1.29.0** and **1.27.0** schemas, the datreeio CRD catalog (PodMonitor, PrometheusRule) and the OpenShift 4.14 strict `Route` schema. Nothing is ignored. | dev **33/33**, prod **26/26**, release-example **26/26** valid on both versions, plus the realistic release (below) **26/26**. **0 skipped, 0 errors.** |
| `pytest -q -m deploy tests/deploy` | **62 passed** (13.6 s) |
| `tools/openshift_check.py` on the committed overlays | rc 0 for each. dev: 33 objects, 0 denied, 28 warnings. prod: 26 objects, 0 denied, 28 warnings. release-example: 26 objects, 0 denied, 27 warnings. All warnings are the documented placeholders or example values. |
| Realistic release overlay (`release/`) | It is round 1's overlay (`.bank.internal` hosts, 10.20.x CIDRs, job `dps-migrate-1-0-0`) with **this round's amd64 digests**. <br>• `openshift_check --fail-on-warn`: **rc 0, 26 objects, 0 denied, 0 warnings**. <br>• The ConfigMap's `DPS_IMAGE_DIGEST` is the app image's digest (`sha256:8a6634ee01b6…`). <br>• The browser-worker and its schema wait carry the browser image's digest (`sha256:d754d96e6dc1…`). <br>• All 6 app-image references use the app digest, and both browser references use the browser digest. |
| Pod and container security in every render (dev, prod, release-example, release) | All **34 containers**, init containers included, have: <br>• `runAsNonRoot: true`, with **no `runAsUser`** (the SCC assigns the UID); <br>• `readOnlyRootFilesystem: true`; <br>• `allowPrivilegeEscalation: false`; <br>• `drop: [ALL]`; <br>• seccomp `RuntimeDefault`; <br>• `/tmp` on an `emptyDir`. <br>See `manifest-security.txt`. |

## 2. Images: amd64, arbitrary UID, read-only root — PASS

| Check | Result |
|---|---|
| `docker build --platform linux/amd64` from the main checkout at `d033dd3` (tracked tree clean). `.dockerignore` excludes `docs/`, so the untracked screenshots dir stays out of the build context. | • `data-privacy-services:final-dd-r2-amd64`: **rc 0**, 201 s, `linux/amd64`, user 10001, id `sha256:8a6634ee01b6…`. <br>• `data-privacy-services-browser:final-dd-r2-amd64`: **rc 0**, 217 s, `linux/amd64`, user 10001, id `sha256:d754d96e6dc1…`. |
| **Code identity** (sha256 of each file, image vs `git show HEAD:<path>`) | • App image `/app` (`dps/`, `tools/` without `tools/dev`, `pyproject.toml`, `README.md`): **206/206 identical**, 0 missing, 0 extra. <br>• Browser image `/app`: **184/184**. <br>• The browser image's installed `site-packages/dps`: **182/182**. <br>• Frontend bundle (5 files): the amd64 image and the running stack's app are byte-identical. |
| Identity under OpenShift-style flags: `--platform linux/amd64 --user 1000650000:0 --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true --network none` | Both images report `dps 1.0.0`, `x86_64`, uid 1000650000, gid 0, HOME `/tmp`. Writing `/`, `/app` or `/app/dps` (and the browser image's `/opt/venv`) fails with "Read-only file system". `/tmp` is writable. |
| `tests/test_image_runtime.py` and `tests/test_browser_image_runtime.py` on the r2 amd64 images (`DPS_TEST_IMAGE_PLATFORM=linux/amd64`) | **6 passed** (30.6 s), including: <br>• the arbitrary-UID read and write rules; <br>• `/health` served under an arbitrary UID; <br>• a new UID reading and replacing what the previous one stored; <br>• the app image serving what the browser worker stored. <br>No test container was left behind. |
| Running stack (`docker inspect` and `exec`) | **Hardened:** <br>• app, worker, browser-worker, tokenizer-mock, migrate and seed-sources run as 10001 with `ReadonlyRootfs=true`, `CapDrop=[ALL]` and `no-new-privileges`; the OCR mock runs as `10001:0` with the same settings. <br>• app, worker and browser-worker have a `/tmp` tmpfs. <br>• A write to `/` or `/app` fails in app, worker and browser-worker. <br>• 0 restarts, 0 OOM kills. <br>**Code identity of the containers that produced the demo report:** app 206/206, worker 206/206, browser-worker 184/184, all identical to HEAD. |

## 3. Ten-minute demo (DoD 7) — PASS

**The report on disk** (`reports/e2e-demo-report.{md,json}`) is the gates verifier's round-2 run on this stack:
- schema `dps.e2e-demo.v1`, `dps_version` 1.0.0, commit `d033dd3` (= HEAD);
- generated 06:52:32Z;
- **S1–S12 PASS: 12 passed, 0 skipped, 0 not passed**;
- the API path (S1–S11) took 2:49;
- **"demo flow completed in 3:02"**: wall time, measured by `time.monotonic()` around the whole run. That is **182.4 s against a 600 s budget**. The step times add up to the same total.

**Live cross-check (`demo_xcheck.py`, GETs only, `demo-xcheck.log`): RESULT PASS, 0 FAIL.**

| Step | What I checked live |
|---|---|
| S5 | The 3 discovery jobs are `succeeded`. |
| S6 | The 3 run jobs (postgres, mongo, oracle) are each `succeeded`, mode `obfuscate`, in the report's group: <br>• 20,000 rows written each; <br>• partitions 4/4 each, 0 failed; <br>• each job's `evidence_id` equals the report's. <br>For each of the 3 packs: <br>• **`pack_sha256` recomputed** with `dps.layers.connectors.evidence.pack_sha256` matches the stored value; <br>• `job_id` matches; <br>• label "pseudonymised — still personal data"; <br>• **`policy` = `retail-kyc` version `2`** (round 1's B1 had silently dropped the version); <br>• verification passed with 0 hits. |
| S8 | The synthesize job is `succeeded` with 20,000 rows. The pack hash recomputes, and the evidence id matches. |
| S9 | Scan `s_01M51QTF55F9F7SR2DZGBRBAM4` is `succeeded`: 21 pages visited, 63 fields, 3 personas, **`non_get_allowed` 0**. The evidence id matches, and the pack hash recomputes. |
| S11 | The dry-run retention job has its `retention.run` audit row, outcome `dry_run`. |
| S12 | `reports/demo-screens/`: 18 PNGs (9 pages × 2 themes), all 1440×900, written at 12:25 IST, which is inside the run. |

**Why I did not re-run `e2e_demo.py`:**
- At the same time, l1-docs, l2-engines, privacy-ops and synthetic (round 2) were working on this stack, and one was running `pytest -m slow tests/connectors/test_resume_after_kill.py`.
- A re-run rewrites the `*_demo` targets.
- S9's D4 check uses deltas of the demo site's shared `/__received` counters, so a concurrent scan would corrupt both my result and theirs.
- The code identity of the stack and its live records tie the on-disk report to HEAD without that risk.
- The 3:01 rehearsal of `c7ad14f` (`FINAL-fix-r1/e2e-demo.log`) agrees with it.

## 4. Showcase deck (DoD 11) — PASS (Keynote PDF review stays with the owner)

| Check | Result |
|---|---|
| `tests/test_deck.py` | **43 passed** (5.4 s), including the committed-deck Keynote-safe build test |
| `docs/demo/dps-showcase.pptx` | Present and tracked, 955,653 B. The working file's sha256 `7da9d21a…` equals the `HEAD` blob. Last commit `d033dd3`. <br>12 slides at 16:9 (12,191,695 × 6,858,000 EMU), each with a title and speaker notes. <br>**No chart parts.** Fonts are Helvetica Neue (298 runs) and Arial only. 4 media files. |
| Content | • Slide 1: "PRIVACY CONSOLE · **v1.0.0**", "Built from the rehearsal of 16 October 2026: the scripted demo ran end to end in **3:01**". <br>• Every footer: "rehearsal 2026-10-16 05:34 UTC · commit **c7ad14f**". That is the last code commit; `d033dd3` changed only `HANDOFF.md` and the `.pptx`. |
| **Provenance of the numbers** (`deck_provenance.py`, live packs of the 05:34Z rehearsal jobs) | • Throughput on the slide 1,338.8 / 455.8 / 322.1 rows/s = the packs' `rows_per_s` for postgres, mongo and oracle (`j_01M51K9F35…`, `…3F75…`, `…3SR1…`; 20,000 rows each). <br>• Synthetic 0.97 / 0 / 1.44 / 0.51 / 0.98 = the pack of `j_01M51KBCA48…`: fidelity 0.968, exact rows 0, NNDR 1.4373, MIA AUC 0.5078, utility 0.9804. <br>• The scanner slide (21 pages, 63 fields, 3 personas, 175 GETs, 60 sends blocked, 0 non-GET) and the evidence slide (5/11 posture, 5 packs, 32 audit rows, 401) match the rehearsal log. |
| Rendering | LibreOffice headless (`deck-render/`) gives a 12-page PDF. The contact sheet shows every slide complete: drawn-shape charts, screenshots, footers. This only adds to the owner's Keynote PDF review; it does not replace it. |

## 5. Blocking items

None.

## 6. Non-blocking notes

- **N1** (as in round 1) The DoD kubeconform command skips the Route, PodMonitor and PrometheusRule (3 per render). The stronger run in §1 validates all three and stays clean. It could go into `deploy/openshift/README.md` §5, with the Route schema kept locally.
- **N2** Every evidence pack on the compose stack has `image_digest: null`, because compose does not set `DPS_IMAGE_DIGEST`. On OpenShift the release overlay sets it, and the release check enforces it (§1). This is expected locally.
- **N3** The compose-only `demo-secrets` one-shot runs as `0:0` without a cap drop. It writes the documented demo literals into the secrets volume, and it is absent from the OpenShift manifests. This is not an image or runtime property of DPS.
- **N4** No `v1.0.0` git tag exists yet (`git describe`: `v0.3.0-m2-322-gd033dd3`). Tagging is the owner's step.
- **N5** Images kept: `data-privacy-services:final-dd-r2-amd64` and `data-privacy-services-browser:final-dd-r2-amd64`, the amd64 builds of `d033dd3` whose digests the realistic release uses. Round 1's `final-dd-r1-amd64` images, built from `45be8ac`, are now stale. Remove both pairs with `docker image rm` when no longer needed.
