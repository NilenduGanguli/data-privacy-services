# S3 security finder — lens "crypto-custody" (fsec = main: M0–M3 + M5)

Date: 2026-10-14. Branch `fsec` @ `64c46e3` (worktree `/Users/neelu/document_intelligence/dps-wt-fsec`, clean;
no repository file was modified). Probes and their outputs: `/Users/neelu/.cache/dps-build/reports/S3-crypto-custody/`.

**Verdict: 12 findings — 4 MEDIUM, 8 LOW. No critical/high.** Every finding below has a probe (or, where named,
a direct code path) as evidence; probe outputs print booleans and counts only (no key, seed, KEK or value).

## Scope and method

Lens: D2 stateless key custody (no key material stored, value-free idempotency caches, job KEK sealing and
rotation, key purge at terminal states), AES-SIV / FF1 usage and the FPE domain guard, HKDF info separation,
SEC-4 external-token binding and the detokenization audit, synthesis seeds, RSA wrap; plus M5 plan Task 17
S-items in this lens (S2, S11, S13; S1's key-material aspect).

Read: `dps/engine/transform/{crypto,fpe,methods,apply,external}.py`, `dps/secrets.py`, `dps/rekey.py`,
`dps/engine/service.py`, `dps/layers/api/{idempotency,common,ndjson,deps}.py`, routers `transform`, `jobs`,
`groups`, `schema`, `dps/layers/connectors/{submit,targets,jobs_run,tabular}.py`, `dps/jobs.py`, `dps/retention.py`,
`dps/groups.py`, `dps/connections.py` (sealing), `dps/engine/batch.py` (L2 transformer, reveal_row,
pre-tokenization), `dps/engine/synth/{keys,surrogates,personas,reshape,mostly}.py`, `dps/observability.py`,
`dps/config.py`, `dps/posture.py`, `dps/worker.py`, console key handling (`frontend/src/lib/jobs.ts`, wizard).

Environment: offline probes via `./lane.sh python …`; DB probes via the repo's own pytest fixtures against this
lane's `dps-db` (`docker compose up -d dps-db` in dps-wt-fsec, port 5483; throwaway `dps_test_<pid>` database,
dropped by the fixtures). The fsec `dps-db` (and a `source-pg` another S3 finder started) were left running
because the fsec stack is shared by the parallel S3 finders. Tokenizer: the repo's vaultless mock
(`docker/tokenizer-mock/server.py`) in-process on an ephemeral port, POSTs counted server-side.

| # | Sev | Finding | Evidence |
|---|---|---|---|
| 1 | MEDIUM | `/obfuscate` (and L2 jobs) is an unrestricted tokenization oracle under DPS's bank-service credentials for any profile string → SEC-4 bypass by dictionary for low-entropy data | `probe_tokenize_oracle.py` |
| 2 | MEDIUM | `/synthesize` (and L2 free-text spans) emit the bank's real, unbound external tokens as "synthetic" values (D-A breach; oracle with `synthesize` scope only) | `probe_synth_external.py` |
| 3 | MEDIUM | Retry and job-group admission bind only the public `kid`, not the DEK (kcv never stored) | `probe_kid_only_binding.py`, `probe_db_custody_test.py` |
| 4 | MEDIUM | The "returned once" job/group key is lost when the answer fails after commit (strict audit refusal → 503); the job still runs, the replay never returns the key | `probe_db_custody_test.py` |
| 5 | LOW | `external_tokenize` caches tokens by (entity, value) without the profile → a column is protected under another column's profile | `probe_profile_cache.py` |
| 6 | LOW | Task 17 **S11** unaddressed: no `Cache-Control: no-store` on L1 responses carrying DEKs, seeds, revealed originals | `probe_s11_cache_control.py` |
| 7 | LOW | Task 17 **S2** unaddressed: `repr(Settings)` prints the KEK, previous KEK, API keys, passwords; engine dataclasses print the SEC-4 binding key | `probe_s2_repr.py` |
| 8 | LOW | Synthesis seeds are never wrapped (`wrap_key` silently ignored; job groups have no wrap) although a seed de-pseudonymises D-A keys | `probe_seed_wrap_and_rotation.py` (A) |
| 9 | LOW | KEK rotation: sealed blobs are opened with the current KEK only (contract §6.3: previous "tried on open") | `probe_seed_wrap_and_rotation.py` (B) |
| 10 | LOW | Unbounded key custody for paused / stalled run jobs (key_enc kept indefinitely, never expires) | `probe_db_custody_test.py` |
| 11 | LOW | SEC-4 tag binding is not injective (`entity|profile` joined with `|`, hint entity types unvalidated) | `probe_seed_wrap_and_rotation.py` (C) |
| 12 | LOW | Task 17 **S13** unaddressed: `tests/test_no_values_in_logs_m5.py` missing (in-lens scan found no leak) | `probe_s13_logscan.py` |

---

## 1. MEDIUM — Tokenization oracle: any `obfuscate` caller tokenizes arbitrary values under any profile with DPS's bank credentials

Files: `dps/engine/transform/apply.py` (`_tokenize_prepass`), `dps/engine/transform/external.py` (`HttpTokenizer`),
`dps/engine/policy.py` (`MethodSpec.params` free-form, `profile` unchecked), `dps/engine/batch.py` (`_pretokenize`, L2).

SEC-4 (D-B) closed the confused deputy on **de**tokenization: `/reveal` refuses a token without a valid bind tag.
The forward direction uses the same privileged credentials with no restriction: an inline policy
`treat: {X: {method: external_tokenize, profile: <any string>}}` plus `declared_fields` (any path → any entity
type, built-in names allowed) makes DPS call the bank service for every value the caller sends, under any
profile, and return the tokens. The wire contract requires the service to be deterministic per
(profile, entity type, value), so a caller can build a codebook for any enumerable domain and invert tokens
the bank's own systems store — without `reveal` scope and without any bind tag. No per-tenant profile
allow-list exists (`grep` finds none); tokenize batches are audited only as request counts.

Probe (`probe_tokenize_oracle.py`, output `.out`): a key with **only `obfuscate`** (its `/reveal` → 403); the
"bank KYC system" tokenizes one customer's DOB itself (profile `dob`); the attacker posts two `/obfuscate`
requests with all 18,262 dates 1950–1999 → 37 tokenize calls, 0 detokenize calls, 8.6 s →
`stored bank token inverted without reveal scope or bind tag: True`. The same works through L2 run jobs
(`jobs` scope; a table of candidate values, `external_tokenize` via snapshot override) and via finding 2
(`synthesize` scope). Enumerable high-value fields: DOB, PIN/CVV-like codes, postal codes, short account
numbers; PAN middle digits per BIN are ~10^6–10^9 (days at the measured rate, bounded only by the service).

Fix: (a) per-tenant allow-list of tokenizer profiles (and entity types) set by admins (`dps_tenant` column or a
setting map), refused at plan time (`external:profile_not_allowed`, 422) in L1, NDJSON, documents and L2
`validate_run`; (b) HANDOFF guidance and posture check: DPS gets dedicated data elements whose tokens never
coincide with tokens bank systems store; (c) audit every tokenize batch (tenant, key id, entity, **profile**,
count) like detokenize; (d) optionally refuse `external_tokenize` for low-entropy entity types or apply a
minimum-domain rule like FPE's. Test: oracle probe → refused.

## 2. MEDIUM — Synthesize mode emits real, unbound bank tokens as "synthetic" data

Files: `dps/engine/service.py` (`PrivacyEngine.synthesize` → `apply_plan` with any `synthesize.overrides` method),
`dps/engine/planner.py` (`build_plan(mode="synthesize")` accepts every method), `dps/engine/batch.py`
(KEYLESS check only for structured columns; free-text spans are planned with the policy's synthesize methods).

L2 structured columns are limited to `KEYLESS_METHODS`, but L1 `/synthesize` (JSON, NDJSON, `/documents/synthesize`)
and L2 free-text spans are not. `external_tokenize` needs no DEK in synthesize mode (`_external` only skips the
bind tag), so the output carries the bank service's deterministic token of the **real** value: joinable to the
bank's tokenized datasets and detokenizable by the bank — a linkable real identifier in "synthetic" output
(decision D-A; spec "no path back to the original"), and the oracle of finding 1 with only `synthesize` scope.
The leakage sweep passes (the token is not the original).

Probe (`probe_synth_external.py`): key with only `synthesize`; `/synthesize` with
`synthesize.overrides: {SSN: external_tokenize, profile ssn}` → 200, `methods_used={'external_tokenize': 1}`,
`'synthetic' ssn equals the bank's real token for the customer: True`, `verification passed: True`.

Fix: in synthesize mode refuse every method outside `KEYLESS_METHODS` in `build_plan`/`PrivacyEngine.synthesize`
(422 `method_needs_key` or a new `synthesize_method_invalid`) and in `BatchTransformer` span planning; tests for
L1 JSON/NDJSON/documents and an L2 text column.

## 3. MEDIUM — Retry and job-group admission check the public `kid` only, never the DEK

Files: `dps/layers/connectors/submit.py` (`seal_retry_secret`: `if km.kid != job.kid`), `dps/layers/connectors/targets.py`
(`_check_group`: `km.get("kid") != kid`); the kcv that binds a DEK to its kid is computed but never stored
(`submit_run` stores `kid` only; `dps_job_group` has `kid` + seed fingerprint). Seeds are bound (fingerprint); DEKs are not.

`GET /jobs/{id}` returns the kid to any `jobs` caller. `key_material_from_caller` computes a kcv when none is
given, so `{kid: <job kid>, dek: <anything>}` passes. Consequences, shown by probes:
- `probe_db_custody_test.py` (real API, real DB): a key with scopes `jobs, detect` (not admin) reads the failed
  job's kid and `POST /jobs/{id}/retry` with its own DEK → **200, queued**; the sealed `key_enc` now holds the
  owner's kid with a different DEK (`sealed_dek_is_owner_dek=False`).
- `probe_kid_only_binding.py`: rows written after such a retry (real FF1, `fpe_digits` + `date_shift`) revealed
  by the owner with `reveal_row(..., kid=<pack kid>)` → `refused=0 wrong_values_returned_silently=100/100` — the
  kid check `reveal_row` relies on to catch a wrong key (FF1 has no integrity) passes, and FF1/date-shift
  decrypt to plausible wrong values. `_check_group` accepts the same forged material (`True`).
- The retrier can reveal the post-retry partitions with its own DEK (if it can read the target), and the evidence
  pack attests the owner's kid. Aggravation (auth lens, code path): the retry route needs only `jobs` scope and
  does not re-check `admin` for `in_place`/`append` on a production-tagged connection, so a non-admin can restart
  an admin-approved production write under its own key.

Fix: store the kcv (public) with the job (`spec.consistency.kcv` / column) and the group (migration in the M5 range,
`dps_job_group.kcv`); require `crypto.kcv_matches(km, stored_kcv)` in `seal_retry_secret` and `_check_group`
(409 `key_material_mismatch` / `group_key_mismatch`); jobs created before the change keep the kid-only rule.
Re-check admin on retry of a production `in_place`/`append` job (auth lens). Tests: forged DEK with the right kid → 409.

## 4. MEDIUM — The once-only key is lost when the answer fails after the job/group was committed

Files: `dps/layers/api/routers/jobs.py` (`run`: `submit_run` → `create_job` commits, **then** `audit_action`),
`dps/layers/api/deps.py` (`audited`: body first, audit after; strict refusal → 503), `dps/layers/api/routers/groups.py`.

Production requires `DPS_AUDIT_STRICT=true`; `DbAuditSink.write` raises `AuditUnavailable` when its queue is full.
The job (key sealed in `key_enc`) or group (kid + fingerprint) is already committed; the answer becomes 503 and
the DPS-generated key/seed — returned nowhere else — is gone. A retry with the same Idempotency-Key replays the
job **without** the key (by design), and the queued job runs under a key nobody holds (an `in_place`
reversible obfuscation becomes irreversible; a group is unusable). Same outcome for a lost HTTP response.

Probe (`probe_db_custody_test.py`, real API + DB, sink refusing the first `job.create` / `group.create`):
`[run] first answer status=503 code=audit_unavailable key_delivered=False | job committed=True state=queued
key_enc_sealed=True | replay status=200 … key_in_replay=False`; `[group create] status=503 …
group_row_persisted=True`.

Fix: write the creation audit **before** committing (same transaction as `create_job`/the group insert, or a
pre-commit "requested" event whose refusal aborts the transaction); on any failure after commit, cancel the
job / delete the group and purge `key_enc` before answering. For lost responses: a two-phase start (job not
claimable until the caller confirms receipt by presenting the kcv) or require caller-supplied key material for
`in_place`/`append`. Tests: refusing sink → no job/group row, or job canceled with key purged.

## 5. LOW — Token cache ignores the profile: one column protected under another column's profile

Files: `dps/engine/batch.py` (`_pretokenize`: groups by (entity, profile) but caches `{(entity, value): token}`),
`dps/engine/transform/apply.py` (`_tokenize_prepass`, `_external` lookup by `(entity_type, value)`),
`batch.external_reveal_cache` / `_reveal_value` (same keying).

Per-column snapshot overrides can give two columns of one entity type different profiles (e.g. a PCI-restricted
and an ops data element). Both profiles are called, but the later one's tokens overwrite the earlier one's in
the cache, so one column is written with the other profile's tokens while the evidence `reveal_map` names its
own profile. Probe (`probe_profile_cache.py`): `card_pci (plan profile pan-restricted): tokens under its own
profile=0/20 under the other column's profile=20/20`. Data meant for the restricted data element is
detokenizable by holders of the ops element; with an FPE-style service the key holder's L2 reveal under the
named profile returns wrong values, and the round-trip check (`tokenize(detokenize(t)) == t`) cannot notice.

Fix: key every cache by `(entity_type, profile, value)` / `(entity_type, profile, token)` and pass the profile
through `TransformContext.external` lookups and the L2 reveal cache; test with two columns, one entity, two profiles.

## 6. LOW — Task 17 S11 unaddressed: no `Cache-Control: no-store` on key-bearing L1 responses

File: `dps/app.py` (`_SecurityHeaders` sets nosniff/DENY/no-referrer/HSTS only). Only `/jobs/run`, `/job-groups`,
admin key routes, artefacts/evidence/reports set it by hand.
Probe (`probe_s11_cache_control.py`): `cache-control=None` on `/obfuscate` (body has the clear DEK),
`/synthesize` and `/schema/synthesize` (seed), `/reveal` (body is the original record), the NDJSON `/obfuscate`
stream (header line has the DEK); `/documents/*` by code. Low: these are POSTs, which compliant caches do not
store without explicit freshness — defence in depth, as the plan item intends. (Also reported by the
auth-tenancy and ssrf-egress finders — dedupe.)
Fix: the plan's `if path.startswith("/api/"): headers.setdefault("Cache-Control", "no-store")` (+ `Pragma: no-cache`)
and `tests/security/test_headers.py`.

## 7. LOW — Task 17 S2 unaddressed: secret reprs

Files: `dps/config.py` (only `tokenizer_token` has `repr=False`), `dps/engine/transform/apply.py` (`ApplyOptions`
dataclass: `binding_keys`), `dps/engine/transform/methods.py` (`TransformContext`: `binding`).
Probe (`probe_s2_repr.py`, sentinels): `repr(Settings)` shows `job_kek`, `job_kek_previous`, `api_keys`,
`bootstrap_api_key`, `coin_client_secret`, `pg_password`, `pg_worker_password`, `pg_migration_password`,
`s3_secret_access_key`, `azure_vision_key` (all True); `repr(ApplyOptions)` / `repr(TransformContext)` show the
SEC-4 binding key bytes (HKDF of the KEK). No current sink was found (`HandlerContext` has a safe `__repr__`;
logs drop unknown fields and tracebacks), so this is latent: any future `%r`, debugger dump, error reporter or
`--showlocals` run prints the KEK.
Fix: `Field(default="", repr=False)` (or `SecretStr`) for every secret setting; `field(repr=False)` for the two
dataclass fields; `tests/security/test_secrets_hygiene.py::test_settings_repr_hides_secrets`.

## 8. LOW — Synthesis seeds are never wrapped, though a seed de-pseudonymises D-A keys

Files: `dps/engine/service.py` (`synthesize` ignores `options.wrap_key`), `dps/layers/api/ndjson.py` (`_header`
puts the clear seed in the header), `dps/layers/connectors/submit.py` (`submit_run` returns `seed_out` clear;
`_wrap` covers the DEK only), `dps/layers/api/routers/groups.py` (`GroupRequest` has no wrap option and does not
forbid extra fields; DEK and seed always clear).
Probe (`probe_seed_wrap_and_rotation.py` A): `/obfuscate`+wrap → DEK wrapped; `/synthesize`+wrap → seed in clear,
no error; NDJSON header carries the clear seed; `KeyPseudonyms` inverse with the seed recovers 1000/1000 real
customer ids from their pseudonyms (`feistel_invert`) — the seed is a key, but gets none of the DEK's wire
protection.
Fix: wrap the seed with the same RSA-OAEP key when `wrap_key` is given (`synthesis_seed: {alg, wrapped_seed,
wrap: {type, fingerprint}}`), accept `wrap_key` on `POST /job-groups`, `extra="forbid"` so a wrap request is
never silently ignored; document the seed as key material.

## 9. LOW — KEK rotation: workers and connections never try `DPS_JOB_KEK_PREVIOUS`

Files: `dps/layers/connectors/jobs_run.py` (`_secret`, `_spec`: `open_json(ctx.kek, …)`), `dps/layers/connectors/discovery.py`,
`dps/connections.py` (`load_credentials`), `dps/worker.py` (`HandlerContext.kek` is one key);
`deps.request_fingerprint` keys idempotency fingerprints to the current KEK only.
Contract §6.3: "`DPS_JOB_KEK` plus optional `DPS_JOB_KEK_PREVIOUS` tried on open". Only `rekey` and SEC-4 use
the ring. Probe (B): a blob sealed under the previous KEK with settings `{job_kek=new, job_kek_previous=old}` →
`SealError` (B1 False) while `kek_ring` has both keys. In the runbook window (roll with new+previous, then
`dps.rekey`), every job claimed fails `secret_unavailable` at prepare or per partition, finalizes `failed` and
its key is purged (caller must resubmit it; DPS-generated keys may be gone); inline-credential connections fail;
from the first roll on, a replay of a pre-rotation `Idempotency-Key` gets 422 `idempotency_key_reused`
(`request_fingerprint` uses the current KEK only, `find_by_idempotency` compares with the old fingerprint).
HANDOFF documents the trap, but the as-built behaviour contradicts the binding contract.
Fix: open with `kek_ring` (current first) everywhere a blob is opened; carry the ring in `HandlerContext`; try both
KEKs in `find_by_idempotency`; test the rotation window end to end.

## 10. LOW — Unbounded key custody for paused / stalled run jobs

Files: `dps/jobs.py` (`pause` keeps `key_enc`; `expires_at` only set at terminal states), `dps/retention.py`
(`keys` purge: `state = ANY(_TERMINAL)` only).
Probe (`probe_db_custody_test.py`, real retention pass): a run job paused 400 days → `still present=True
state=paused key_enc_kept=True expires_at_set=False`; a terminal job's key is purged (defence in depth works).
D2's "sealed for the job's lifetime only" has no bound: a paused (or quota-starved `waiting`) job escrows the DEK
indefinitely — recoverable by anyone with the KEK and DB access.
Fix: `DPS_JOB_PAUSE_MAX_HOURS` (or similar); the reaper cancel-finalizes paused/stalled jobs past it (purging
`key_enc`), or purge on pause and require the key on resume (as retry does); evidence states custody duration.

## 11. LOW — SEC-4 bind tag is not injective over (entity type, profile)

Files: `dps/engine/transform/crypto.py` (`external_binding_tag`: HKDF info `external_tokenize|{entity}|{profile or ''}`),
`dps/engine/transform/apply.py` (`RevealHint.entity_type: str` unvalidated; `_is_bound`; detokenize groups by the
hint's entity/profile), `dps/layers/api/common.py` (audit names the hint's entity).
Obfuscate-time entity names are regex-checked, but profiles are free-form and reveal-hint entity types are not
checked. Probe (C): a tag issued for (`ACCT`, profile `p|q`) verifies for a hint claiming (`ACCT|p`, `q`); DPS
then calls the bank detokenizer with `('ACCT|p', 'q')` and audits entity `ACCT|p`. `None` and `""` profiles also
bind identically but are sent differently. Exploitability depends on the bank service accepting the crafted
profile names (low), but the tag does not bind what D-B says it binds, and the audit records a caller-chosen entity.
Fix: injective encoding (length-prefixed or JSON array) of `[entity, profile]` in the HKDF info with a tag
version (dual-verify during migration), validate hint entity types with the custom-entity regex / taxonomy,
bind `None` vs `""` distinctly.

## 12. LOW — Task 17 S13 unaddressed: no M5 log-scan test

`tests/test_no_values_in_logs_m5.py` (and `tests/security/`) do not exist. In-lens stand-in
(`probe_s13_logscan.py`, real pipeline at DEBUG): tokenizer outage, unbound-token reveal, bad kcv, bad seed,
bad wrap PEM, wrong-KEK open → 1 log line (`tokenizer_unavailable`), no DEK, seed, KEK or value. Fix: add the
plan's test (copula, metrics, tokenizer, keys, PDF, netguard, regex guard) plus the crypto paths above.

---

## Checked and sound (no finding)

- **AES-SIV**: 64-byte HKDF subkey per method/entity(/path), entity type (or `entity|domain`) as AD; tokens fail
  closed on wrong key/type. **FF1** (`fastfpe`): domain guard `radix**len ≥ 10^6` on digits, letters (≥5) and
  digit groups (≥6 or none), `phone` and email local parts; fallback to `det_token` noted; leading-zero cycle walk
  bounded. Reveal compares kid and kcv (constant time) before decrypting.
- **HKDF**: kid salt, `dps.v1|…` labels, length bound into the info (`|L<n>`), KCV on its own label; KEK-derived
  binding key and keyed fingerprints on distinct labels/salts. Custom entity names cannot contain `|`.
- **Sealing**: AES-256-GCM, random 96-bit nonce, version byte, AAD per table+row; detail-free `SealError`;
  `redact_spec` strips key material/seed/wrap key; public `Job` never carries `key_enc`; replays never return keys.
- **Purge at terminal states**: finalize (`purge_secret=True`), dead-letter (`retry_job`, reaper), cancel; retention
  nulls stragglers (probe confirms); RUNTIMES closes idle runtimes after 120 s.
- **Idempotency**: L1 cache keeps scope hash, body SHA-256, trace id, time only; job fingerprints are KEK-keyed HMACs.
- **RSA wrap**: OAEP-SHA256, ≥2048 bits, e=1 refused by the library (`probe_rsa_wrap.py`); clear DEK not returned
  when wrapped. **S1 (key part)**: 422 bodies never echo key material (`probe_s1_422_keys.py`; the plan's
  `code: invalid_request` field is absent — logs/errors lens).
- **SEC-4 detokenize path**: every server reveal path (JSON, NDJSON body, streamed array) checks every tag before
  the first call and audits each batch before it (strict refusal → 503); L2 reveal is client-side only.
- **Seeds**: 32 bytes enforced; surrogate RNGs seeded from HMAC(seed, label) (MT state never exposes the seed);
  key pseudonyms are a 4-round HMAC Feistel (no practical inversion shown without the seed).
- **Console**: key material/seed files stay in React state (`TRANSIENT`), never in localStorage; tools write key
  files 0600.

## Out-of-lens notes (for the other finders)
- Retry of a production `in_place`/`append` job needs only `jobs` scope (no admin re-check) — see finding 3.
- S8 (no key-admin escalation) is implemented (`dps/auth_db.check_grant`, `tests/test_auth_db.py::test_no_privilege_escalation`).

## Re-run
From `/Users/neelu/document_intelligence/dps-wt-fsec`:
`./lane.sh python <probe>.py` for each offline probe; DB probes:
`PYTHONPATH=. DPS_TEST_REQUIRE_SERVICES=1 ./lane.sh pytest -q -s -p tests.fixtures.services -p tests.fixtures.db
-p tests.fixtures.sources -p tests.fixtures.m3_services -c pyproject.toml --rootdir=.
/Users/neelu/.cache/dps-build/reports/S3-crypto-custody/probe_db_custody_test.py` (needs `docker compose up -d dps-db`).
