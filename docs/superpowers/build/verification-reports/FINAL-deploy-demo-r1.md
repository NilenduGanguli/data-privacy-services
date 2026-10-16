# FINAL — v1.0.0 candidate on `main` — LENS "deploy-demo", round 1

**Verdict: FAIL. Two blocking items.**
- **B1:** the ten-minute demo cannot pass. This is the gates verifier's B1, which I reproduced independently from the live stack and the code.
- **B2:** the committed showcase deck is the 15 October `fdemo` build. Its title slide reads **v0.2.0**, every footer names commit `b0bea88`, and it claims an end-to-end run (3:17) that the v1.0.0 tree cannot repeat.

The rest of my lens is green, with stronger evidence than the DoD asks for:
- kustomize dev, prod and release renders pass kubeconform `-strict` with **0 skipped** (OpenShift Route and Prometheus CRDs validated too);
- a realistic release overlay passes `openshift_check --fail-on-warn` with 0 denied and 0 warnings;
- both images build for **linux/amd64** from HEAD and pass the arbitrary-UID, read-only-root runtime tests on amd64 and arm64;
- the running stack is hardened;
- the deck test passes (43) and the deck is present and Keynote-safe.

| | |
|---|---|
| Date | 2026-10-16, 09:55–10:05 IST |
| Tree | `/Users/neelu/document_intelligence/data-privacy-services`, `main` @ `45be8ac` (`chore(release): v1.0.0`). `git status` was empty before and after. |
| Stack | The gates verifier's `full` stack (containers created 08:18:50 IST). I did not restart or rebuild it, did not run `up` or `down`, and did not run the full suite. `/readyz` was ready at the start and at the end. Six other FINAL verifiers (console, l1-docs, l3-scanner, privacy-ops, security, synthetic) were using it at the same time, so I did **not** re-run `e2e_demo.py` (§3). |
| Probes | `~/.cache/dps-build/reports/FINAL-deploy-demo-r1/`: renders, kubeconform log, `oscheck-*.log`, the release overlay (`release/`), build and runtime logs, `images.txt`, `stack-hardening.txt`, `manifest-security.txt`, `amd64-identity.txt`, `deck-test.log`, `deck-probe.log`, `dead-jobs.log`, `worker-finalize-failed.txt`, `other-job.log`, and the probe scripts |
| Repo writes | None. The pytest and probe runs used `PYTHONDONTWRITEBYTECODE=1` and `-p no:cacheprovider`. `python-pptx` went into a private `--target` dir in the probe folder. |
| Values | Only ids, counts, codes, key names and digests appear here. No customer value, key or secret was printed: the demo key file was read for key names only, and keys went straight into the client. |

## 1. OpenShift manifests (DoD 10) — PASS

| Check | Result |
|---|---|
| `kustomize build deploy/openshift/overlays/{dev,prod}` and `release-example` (kustomize v5.8.1) | exit 0. dev 33 objects; prod 26; release-example 26 |
| `kubeconform -strict -summary -ignore-missing-schemas` (v0.8.0, the DoD command) | dev 30 valid / 3 skipped; prod 23 / 3; release-example 23 / 3. **0 invalid, 0 errors** |
| **Stronger:** `-strict`, k8s **1.29.0** and **1.27.0** schemas, plus the datreeio CRD catalog (PodMonitor, PrometheusRule) and the OpenShift 4.14 strict `Route` schema. Nothing ignored. | dev **33/33**, prod **26/26**, release-example **26/26**, and the realistic release below **26/26** valid. **0 skipped, 0 errors** on both k8s versions |
| `pytest -m deploy tests/deploy` | **62 passed** (15.8 s) |
| `tools/openshift_check.py` on the committed overlays | dev: 0 denied, 28 warnings (placeholders: 13 digest, 8 cidr, 6 host, 1 job). prod: 0 denied, 28 warnings. release-example: 0 denied, 27 `example-value` warnings. All as documented. |
| **A realistic release overlay** (`release/`): a copy of `release-example` with these changes: `resources` given by a relative path to the repo's `overlays/prod`; `.bank.internal` hosts and 10.20.x CIDRs; job `dps-migrate-1-0-0`; the digests of my **amd64 builds** | `openshift_check --fail-on-warn`: **rc 0, 26 objects: 0 denied, 0 warnings**. `DPS_IMAGE_DIGEST` follows each image: the ConfigMap carries the app digest and the browser-worker plus its schema wait carry the browser digest. |
| Pod and container security in every render (dev, prod, release) | Every Deployment, Job and CronJob container, init containers included, has: `runAsNonRoot: true`; **no `runAsUser`** (the SCC assigns the arbitrary UID); `readOnlyRootFilesystem: true`; `allowPrivilegeEscalation: false`; `drop: [ALL]`; seccomp `RuntimeDefault`; `/tmp` on an `emptyDir` (`manifest-security.txt`) |

## 2. Images: amd64, arbitrary UID, read-only root — PASS

| Check | Result |
|---|---|
| `docker build --platform linux/amd64` from HEAD (clean tree) | `data-privacy-services:final-dd-r1-amd64`: **rc 0**, 233 s, `linux/amd64`, user 10001, id `sha256:7ecbae97…`. `data-privacy-services-browser:final-dd-r1-amd64`: **rc 0**, 192 s, `linux/amd64`, user 10001, id `sha256:fb5a54c7…`. |
| Identity under OpenShift-style flags (`--user 1000650000:0 --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true --network none`) | Both images: `dps 1.0.0`, `x86_64`, uid 1000650000, gid 0, HOME `/tmp` |
| `tests/test_image_runtime.py` + `tests/test_browser_image_runtime.py`, amd64 emulated (`DPS_TEST_IMAGE_PLATFORM=linux/amd64`, uid 1000770000:0, `--read-only`, cap-drop ALL) | **6 passed** (29.9 s), including the cross-image case (the app image reads what the browser image stored) and the architecture check |
| Same tests on the stack's arm64 `:local` images | **6 passed** (14.2 s). No probe volume left behind. |
| Running stack (`docker inspect` + `exec`) | app, worker, browser-worker, tokenizer-mock, migrate and seed-sources all run as user 10001 (gid 0) with `ReadonlyRootfs=true`, `CapDrop=[ALL]` and `no-new-privileges`. app, worker and browser-worker have a `/tmp` tmpfs. Writing `/` or `/app` fails with "Read-only file system" in app, worker and browser-worker. |
| `docker compose --profile full config -q` | valid |

## 3. Ten-minute demo (DoD 7) — **FAIL (B1)**

**The report on disk.** `reports/e2e-demo-report.{md,json}` is the gates verifier's run on this stack:
- schema `dps.e2e-demo.v1`, `dps_version` 1.0.0, commit `45be8ac`;
- **S1–S5 PASS, S6 FAIL** ("postgres run dead finalize_error"), S7–S12 NOT RUN;
- "FAIL: demo flow completed in 3:39"; `reports/demo-screens/` is empty.

**Independent reproduction (read-only, no new jobs)** (`dead-jobs.log`):
- Through the API, the demo tenant `retail-banking-apac` has exactly **3 dead runs**: `j_01M51EJW21P25HA8J8JC5YF0VE`, `…2BW9ENEXERBDAVWZZN` and `…2N1K3K6X80Q81NQJZC`.
- Each one is `run`, `obfuscate`, `table`, `dead`, phase `finalize`, `error_code=finalize_error`, `evidence_id=None`.
- The stored spec's `policy` is a sealed **label** (`is_policy_label` True; keys `name, sealed, sha256, version`).
- In process, `jobs_run._policy(ctx, RunSpec(stored spec), "retail-kyc")` raises **`PrepareFailure policy_invalid`** for all 3.

**Worker log** (`worker-finalize-failed.txt`): 3 × `finalize_failed error=PrepareFailure` per demo run, 04:12:56Z–04:16:06Z.

**B1 is still live.** A **4th** job hit the same path at 04:34:10Z, after the gates run, from another verifier: `j_01M51FT2GEVQB0HVGMJDH0QCHE`, tenant `default`, `synthesize` `table` run, policy a sealed label. It is `queued` in `finalize` with `finalize_error`. So synthesize runs with an `@name` or inline policy are broken as well (S8 of the demo uses an inline policy).

**Root cause, confirmed from the code** (`dps/layers/connectors/jobs_run.py`):
- `_finalize` (line 2156) builds `spec = RunSpec.model_validate(job.spec)`, the redacted spec, which since F-18 holds only the label.
- `_post_sample` (line 1925), `_identifier_check` and `_store_evidence`'s `policy_version` (line 2418) then call `_policy(ctx, spec, …)`, which ends in `ctx.engine.policy(label)` and so in `PrepareFailure`.
- `_best_effort` (line 2264) does not catch `PrepareFailure`, so the generic handler (line 1620) retries until the job is dead.
- `prepare` and the partitions use `_spec(ctx, job)`, which opens `spec_enc`, so they work.
- The other policy readers handle the label correctly: discovery (`discovery.py` 708–751) opens the sealed body, and the scanner uses `stored_policy` (`scanner/__init__.py` 115). **The L3 scan path (S9) is not affected by this defect.**

**Why I did not re-run `e2e_demo.py`:**
- The stack and code are byte-identical to the gates run (no rebuild), and the failure is deterministic for every `@name` or inline-policy run that reaches finalize.
- Re-running would have rewritten the `*_demo` targets and added dead jobs while six other verifiers were reading the same stack.
- The read-only evidence above reproduces B1 without any side effect.

**Not verifiable until B1 is fixed:** S7–S12, the 10:00 budget, `reports/demo-screens/`, and the 9 console-page tests. The measured parts suggest the budget is not at risk:
- S1–S5 took 12 s;
- S6 wrote 3 × 20,000 rows before finalize;
- the `fdemo` rehearsal ran end to end in 3:17.

## 4. Showcase deck (DoD 11) — tests PASS, deck present; **content stale (B2)**

| Check | Result |
|---|---|
| `tests/test_deck.py` (`python-pptx` 1.0.2 in a private target) | **43 passed** (21.8 s), including `test_the_committed_deck_is_a_complete_keynote_safe_build` |
| `docs/demo/dps-showcase.pptx` (957,729 B, tracked; last commit `b57ec86`) | 12 slides, 16:9 (12,191,695 × 6,858,000 EMU), every slide titled with speaker notes; **no chart parts**; fonts only `Helvetica Neue` (298 runs) and `Arial`; 4 media files (`deck-probe.log`) |
| **Content provenance** | Slide 1: "PRIVACY CONSOLE · **v0.2.0**" and "Built from the rehearsal of 15 October 2026: the scripted demo ran end to end in 3:17". Every footer: "rehearsal 2026-10-15 21:29 UTC · commit **b0bea88**". `b0bea88` is on `fdemo` with `pyproject` version 0.2.0. It predates F-18 and the release. |

**B2:**
- DoD 11 requires the deck to be rebuilt by `python tools/build_deck.py` from the **final** rehearsal, and its Keynote PDF export to be reviewed.
- The release step did not rebuild it (`release.md` §8 lists only the Keynote review as open).
- The current tree cannot produce a passing rehearsal (B1). The v1.0.0 deck therefore shows the wrong version, a non-release commit, and an end-to-end claim that v1.0.0 does not meet today. That breaks the deck's own rule that every number on a slide is measured.
- `build_deck.py` takes the version from the report's `dps_version`, so a rebuild from the fixed v1.0.0 rehearsal corrects all of it.

## 5. Blocking items and fixes

1. **B1 (shared with FINAL-gates-r1 B1).** In `_finalize`, use the opened spec (`_spec(ctx, job)`) for every policy or selection consumer: `_post_sample`, `_identifier_check`, and `_store_evidence`'s `policy_version`, which today is silently dropped under `contextlib.suppress`. Keep `job.spec` for what goes into the evidence and audit.
   - Red-first tests: an integration run (`db` + `pg_source`, and if possible Oracle and Mongo through the conformance harness) with a published `@name` policy and one with an inline body, through a worker, for both obfuscate **and synthesize**. Assert `succeeded`, a pack, and the pack's policy name and version.
   - Then rebuild the stack and re-run `demo_seed.py && e2e_demo.py --console`: S1–S12 PASS, under 10:00, screenshots in `reports/demo-screens/`.
2. **B2 (after B1).** From that passing rehearsal, rebuild the deck: `uv pip install -e ".[deck]"` (or a private target), then `python tools/build_deck.py --strict`. Re-run `tests/test_deck.py`, commit `docs/demo/dps-showcase.pptx` with an explicit pathspec, and do the Keynote PDF review (owner).

## 6. Non-blocking notes

- **N1** The DoD's kubeconform command uses `-ignore-missing-schemas`, which skips the Route, PodMonitor and PrometheusRule (3 per render). The stronger invocation in §1 validates them too and stays clean. Consider adding it to `deploy/openshift/README.md` §5, with the Route schema pinned locally because it needs network access.
- **N2** Kept for reuse: the amd64 images `data-privacy-services:final-dd-r1-amd64` and `data-privacy-services-browser:final-dd-r1-amd64`, built from `45be8ac`. Their layers will speed up the post-fix amd64 build. Remove them when no longer needed.
- **N3** I checked every other reader of `spec.policy` for the same mistake (§3). Only `_finalize` reads the redacted spec. `_relaxes_verification` (`targets.py` 642) runs only in `validate_run`, at submit, on the caller's spec.
