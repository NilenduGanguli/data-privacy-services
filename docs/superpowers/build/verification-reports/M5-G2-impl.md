# M5 G2 — SEC-4 (D-B), D-A, and the L2 external_tokenize reveal path (implementation report)

Worktree `/Users/neelu/document_intelligence/dps-wt-m5`, branch `m5`. Date 2026-10-13.
Author of all commits: NilenduGanguli <nilendu.ganguli@gmail.com>. No trailers. Nothing pushed, no tags.

## Starting state

Clean worktree at `1a73de2` (T8). No earlier attempt and no uncommitted work. T6 `cae6a88`, T6B `7453d79`,
T7 `b7762e6`, T7B `080087a` and T8 `1a73de2` were all present. Baseline `./lane.sh pytest -q` with no
containers: 1542 passed, 176 skipped.

## Commits (one per task)

| Commit | Message |
|---|---|
| `553ce70` | fix(security): bind external tokens to the caller's key before any detokenize call and audit each batch (SEC-4) |
| `cb9aafc` | fix(reveal): L2 reveal of external_tokenize columns with the key holder's own tokenizer client, key-checked first and tokenized back |
| `e231483` | fix(synth): synthetic tables never carry linkable real identifiers — keyed group pseudonyms for keys, in-place refusal, kept columns reviewed (D-A) |

## 1. SEC-4 / D-B: external tokens bound to the caller's key (`553ce70`)

**The plan's formula was wrong.** D-B specifies `tag = base32(HMAC(subkey(key, "external_tokenize|<entity>|<profile>"), token))[:26]`.
That tag is keyed by the caller's DEK alone. The caller holds its DEK: L1 returns it, or the caller brings
its own. So the caller can compute the tag for any token, and the confused deputy would stay open. As
built, the tag is `base32(HMAC(HMAC(B, subkey(key, "external_tokenize|<entity>|<profile>")), token))[:26]`.
`B` is a server-held binding key: HKDF of the job KEK with label `dps.v1|xtb`
(`crypto.binding_key_from_kek`). This needs no new setting. The binding keys are the KEK ring
(`secrets.kek_ring`): `DPS_JOB_KEK`, then `DPS_JOB_KEK_PREVIOUS`. A KEK rotation therefore keeps old tags
verifiable while the previous KEK is kept. The test
`test_a_foreign_token_with_a_valid_looking_hint_is_refused_and_the_tokenizer_not_called` includes the
plan's DEK-only tag as a forged case. That forged tag is refused.

What changed:
- **Obfuscate side.** The tag is in `RevealHint.reverse_params["bind"]`. `methods._external` returns an
  `Outcome` that carries the tag in obfuscate mode. `methods.external_tag()` computes it, and
  `TransformContext.binding` holds the binding key.
- **No KEK configured.** `external_tokenize` obfuscation fails closed with a 400 (`tokens cannot be
  bound … DPS_JOB_KEK`). The plan preview shows the reason `|external:unbound`
  (`build_plan(..., external_bound=)`).
- **Reveal side.** `apply._detokenize_prepass` checks every external hint's tag in constant time, against
  each binding key, before the first detokenize call. A single unbound token refuses the whole request.
  This raises `TokenNotBound`, which the service turns into `CodedError` **403
  `external_token_not_bound`**. The message carries no values, and the JSON route and the NDJSON stream
  both render it.
- **Audit per batch.** `ApplyOptions.on_detokenize` is called once per (entity, profile) batch,
  **before** that batch is sent. `common.detokenize_auditor(request)` writes the event:
  - fields: `action external.detokenize`, `outcome requested`, tenant, API key id, `findings_by_type
    {entity: n}`, `counts {tokens: n}` and the trace id;
  - the event is written on the event loop, because the engine runs on a worker thread and
    `DbAuditSink`'s queue is loop-bound;
  - a strict sink that refuses the event stops the reveal with 503 `audit_unavailable`, and nothing is
    sent to the service.
- **Where it is wired.** `/reveal` (JSON), the JSON-array stream and the NDJSON-body stream.

Tests: `tests/test_external_binding.py`, 7 tests. They run against the real vaultless mock
(`docker/tokenizer-mock/server.py`) in a thread, with a **server-side request counter**:
- **Forged hints are refused with 403 and the server receives 0 `/v1/detokenize` requests.** Cases:
  - no tag;
  - the plan's DEK-only tag;
  - a made-up tag;
  - a tag replayed from another token;
  - a non-string tag;
  - another profile;
  - one forged hint among legit ones;
  - a tag from another deployment's KEK.
- **Rotation.** An old KEK kept as previous still verifies.
- **No KEK.** Obfuscation fails and reveal refuses, with 0 requests.
- **API.** The audit has one event per batch, and the dump of all events contains no value and no `tk_`
  token. An unbound NDJSON item ends the stream with an `external_token_not_bound` error line.
- **Strict sink.** A refused batch event gives 503 and 0 requests.

`tests/test_external_tokenize.py` now sets a test `job_kek`. The `tests/test_api.py` `_BrokenEngine.reveal`
test double takes the new keyword, and its assertions are unchanged. Contract §5.3 and §8.3 were amended,
and HANDOFF §8 now has a SEC-4 trap (a KEK rotation and the PREVIOUS key).

## 2. L2 reveal of `external_tokenize` columns (`cb9aafc`)

L2 stores no hints, so L2 external tokens have no tag, and L1 `/reveal` refuses them by design. The bound
path is on the key holder's side, in `tools/reveal_rows.py`:
- **The key holder's own credentials.** New flags `--tokenizer-url`, `--tokenizer-token-file` and
  `--tokenizer-ca-file`. https is required unless the URL points at the local machine
  (`TokenizerUrlRefused`). DPS's own `DPS_TOKENIZER_*` settings are never read: the tool's engine is built
  with `tokenizer=None`.
- **The key is checked first.** The tool checks the key against the pack's `consistency.kid` before any
  service call.
- **Batched and checked.** `dps.engine.batch.external_reveal_cache(rows, *, reveal_map, shape, tokenizer)`
  makes one detokenize call per entity type and profile per `--chunk-rows` (default 1000). Every answer
  must tokenize back to its token.
- **Refusal without a tokenizer.** `reveal_row(..., external=)` refuses an external column by path when
  no tokenizer is given.
- **Output per chunk.** Rows are written per chunk. A refusal writes none of that chunk's rows. Before
  this change, rows up to the refused line were already written.
- **Service errors.** An `ExternalServiceError` is reported as a value-free refusal instead of a
  traceback.

Tests: `tests/test_reveal_rows_external.py`, 6 tests:
- a round trip in 3 chunks, which is exactly 3 detokenize requests;
- refusal by path when no tokenizer is given;
- another key: 0 requests;
- wrong credentials (HTTP 401) and a tampered token are refused with no values in the error;
- the https rule;
- a service whose answer does not tokenize back.

The contract §6.2 paragraph and HANDOFF were updated.

## 3. D-A: synthetic tables never carry linkable real identifiers (`e231483`)

### Which columns are pseudonymised (`targets._identifiers`)

A column is pseudonymised when all of these hold:
- it is projected and is not a free-text column;
- it has an entity type;
- its synthesize method is `keep`;
- **and** it is a key, a join identifier (`LINK_ENTITIES` = `CUSTOMER_ID`, `EMPLOYEE_ID`, `CASE_ID`,
  now one constant shared with discovery's `JOIN_ENTITIES`), or of MEDIUM+ sensitivity.

Every other classified column is surrogate'd, nulled and so on as before, so it is not copied.

### The pseudonym (`dps/engine/synth/keys.py`, pure Python, no extra)

`KeyPseudonyms(seed)` maps `v → p⁻¹(p(v)+1 mod N)`, where `p` is a keyed Feistel permutation keyed by the
**synthesis seed and the entity type**. HMAC-SHA256 is the round function, with counter-mode output for
wide halves, and the Feistel network is cycle-walked into the domain. The map has these properties:
- **injective**, so keys stay unique;
- **deterministic**;
- **consistent across the tables of one job group**: one seed gives the same pseudonym for the same
  customer, so foreign keys still join;
- **no fixed points**: one cycle runs through the whole domain. A plain permutation would map about one
  value in N to itself, which is a copy.

Integer tiers do not depend on the column type, so `integer` and `bigint` FK columns agree:
- below 10⁹ maps to below 10⁹;
- longer values keep their digit count, up to 18 digits.

Text keeps its length and the ASCII character class at each position. Other characters are kept. A uuid
maps to a uuid.

### Wiring

- `BatchTransformer(..., pseudonyms=)` applies the pseudonyms in synthesize mode only, never on a planned
  or sampled column. It counts them as `key_pseudonym`. A pseudonym that does not fit its column fails the
  batch with `incompatible_method`.
- `RunPlan.pseudonyms` holds them. `resolve_target` writes them in place.
- The evidence `columns[]` gets a `key_pseudonym` entry per identifier.
- In `prepare_tabular`, pseudonymised columns are dropped from the exact-row reject columns. Otherwise a
  row that copies every other column would pass, because its pseudonym never equals the real value.

### Refusals at submit (`validate_run`, synthesize)

| Code | When |
|---|---|
| `synthesize_in_place_sensitive_key` (422) | the table's key has an entity type and the run is `in_place` |
| `pk_strategy_invalid` | an offset or prefix append of such a key; append with `generate` is still allowed |
| `synthesize_pseudonym_unsupported` (422) | an identifier no pseudonym fits: integer columns of fewer than 9 digits such as `smallint`, dates, floats |

### Kept-column review

This lane predates main's `review_required` (main `89a9e98`). For synthesize runs, two pieces are added:
- `jobs_run.synthetic_kept_columns`: the columns written that are unclassified, not a key, not planned,
  not pseudonymised and not sampled.
- `kept_review`: MEDIUM+ findings, with numeric columns counted only above discovery's bar, as in main's
  `scan_written`.

The review runs on the **preflight** output, which covers every target including files (files have no
post-write sample). It also runs on the **post-write sample** of database targets. Any flagged column
sets `verification.review_required` to true, fills `review_columns` with the names, and sets `passed` to
false.

### Source-vs-target comparison (counts only), from the integration tests on this lane's source-pg

From `tests/connectors/test_synthesize_identifiers.py::test_a_job_group_replaces_every_identifier_and_keeps_the_joins`.
Setup: customers (400, copula) and accounts (900, surrogate-only, FK `customer_id`, key `account_number`)
under one seed.

| Check | Result |
|---|---|
| survivors: `customers_synth.customer_id` | **0** |
| survivors: `accounts_synth.customer_id` | **0** |
| survivors: `accounts_synth.account_number` | **0** |
| key uniqueness | 400 distinct customer keys, 900 distinct account keys |
| FK join `accounts_synth ⋈ customers_synth` | **900 / 900** rows |
| target FK set vs `KeyPseudonyms(seed)` of the source FKs | equal |
| any source key, account number, national id or e-mail anywhere in either target, any column | **0** |
| evidence | both packs `passed: true`; `key_pseudonym` on `$.customer_id` and `$.account_number`; no source value |

Other checks:
- **Copula** (`test_synthesize_copula.py`, 1 500 rows): the target key set equals the pseudonym set and
  is disjoint from the source keys. On each sensitive column, no row keeps its own value. No source
  national id, tax id, e-mail or mobile number appears anywhere in the target. 0 synthetic rows equal a
  source row on the non-key columns. Fidelity is ≥ 0.85 and `exact_row_matches` is 0, both unchanged.
- **Linked** (`test_linked_files.py`): unchanged and green. It already generated new keys and nulled
  unmodelled columns.
- **In-place refusal**: refused before a job row exists; the run-job count is unchanged.
- **Review**: a PII column reviewed as "not personal" gives `review_required: true`, `passed: false`,
  `review_columns: ["contact"]` and `synthetic_kept.kept_column_hits > 0`. No e-mail appears in the
  pack.

### Tests changed

- `test_synthesize_copula.py` now re-keys the target through `KeyPseudonyms`.
- These three integration tests exercise offset-append mechanics: `test_resume_after_kill[append_offset_named]`,
  `test_run_failures::test_same_table_append_refuses_an_offset_inside_the_key_span` and
  `test_run_modes::test_append_offset_to_the_same_table`. D-A now refuses an offset append on a sensitive
  key, so they review the demo key as not personal (`tests.connectors.helpers.KEY_NOT_PERSONAL`). Their
  resume and offset assertions are unchanged.

### New tests

- `tests/test_key_pseudonyms.py` (7)
- `tests/test_kept_review.py` (2)
- `tests/connectors/test_targets.py` (+3)
- `tests/connectors/test_synthesize_identifiers.py` (3)

## Gates (final, at `e231483`)

| Gate | Result |
|---|---|
| `./lane.sh ruff format dps tests tools` | 272 files unchanged |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | no issues (111 files) |
| `./lane.sh pytest -q`, run before the docs-only edits of the D-A commit | **1734 passed, 12 skipped** in 183 s |

The pytest run used this lane's dps-db, source-pg and MinIO containers, so every db, integration and
pg_source test ran. That includes M2 run modes, resume-after-kill, run failures, copula, linked and the
new D-A suites.

The 12 skips are:
- console browser checks (need the app image);
- the azure-ocr-mock profile (3);
- tesseract (2);
- `mostlyai` (synth-ml extra, ~2 GB torch; 2);
- presidio and the NER spaCy model (2).

Other gate notes:
- **Frontend**: untouched (`git diff 1a73de2 -- frontend` is empty), so npm was not run.
- **Log scan**: covers the integration, binding and reveal suites run with `-s` (output in
  `/Users/neelu/.cache/dps-build/m5g2/logscan.txt`). It searched for the test SSNs, `tk_`, source
  customer ids (`1000000x`, `100001xx`), `ABCPE`, `@example`, `holder.`, and the bearer tokens:
  **0 matches** for every pattern. The audit events in the API test are asserted value-free.
- **Containers**: brought down with `docker compose --profile sources down` (this lane's project only).

## Deviations (all recorded in `docs/superpowers/build/M5-DEVIATIONS.md`, committed with each task)

- D-B tag formula changed, as described above. "Key id" in the audit means the API key id. The tag binds
  the caller key, entity and profile, not the tenant or path.
- D-A "personal": LOW attributes kept by policy (`keep_non_sensitive`) stay. D-A's "append" means append
  with `generate` (offset is refused). Pseudonym domain tiers and the fixed-point-free construction were
  added. The kept-column review is synthesize-only in this lane and also runs on the preflight, so files
  are covered.
- Process note: `test_key_pseudonyms.py` was written before the implementation but first run after it.
  It would have failed on the import of new modules. The other D-A, SEC-4 and L2 tests were seen failing
  for the expected reason first.

## Open issues / for the reviewer and integration

1. **Integration with main's `review_required`** (`89a9e98`: all modes, console, `verification_outcome`):
   merge the lane's synthesize-only kept review into main's one rule. Keep the preflight scan for files
   targets, which main does not sample.
2. **Console**: the wizard still offers *In place* for synthesize, and the server now refuses it with
   `synthesize_in_place_sensitive_key`. `frontend/src/lib/jobs.ts` has no plain-language text yet for
   `synthesize_in_place_sensitive_key`, `synthesize_pseudonym_unsupported` or `external_token_not_bound`.
   That belongs to the m5ui lane / WP-F4.
3. **Privacy decision for the owner**: LOW attributes (city, gender, age, postal code, …) kept under
   `keep_non_sensitive` are still copied in surrogate-only synthesize runs (the copula models them). If
   D-A's "any column classified as personal" must include them, extend `_identifiers` or refuse them.
4. **Small-domain text keys**: pseudonyms never map a value to itself, but in a tiny domain they may equal
   *another* real key by chance. Integer keys below 10⁹ map into a 10⁹ domain; at test scale (1 300 keys)
   0 collisions were observed.
5. **KEK rotation**: once `DPS_JOB_KEK_PREVIOUS` is dropped, external tokens bound under the old KEK can
   no longer be revealed through DPS (HANDOFF trap).
6. **Still open from Task 8**:
   - an L2 `external_tokenize` policy on a deployment without an adapter fails at the preflight with an
     `internal_error` retry instead of a coded refusal at submit;
   - the `tokenizer-mock` container has never run in a container (no m5 image; Task 24);
   - the MOSTLY SDK paths are unverified without the synth-ml extra.
7. **Tenant binding**: SEC-4 does not bind external tokens to the tenant. A party holding another
   tenant's key material and hints can reveal the rest of that output anyway. If cross-tenant
   detokenization must be prevented independently, add the tenant to the tag (the engine would need the
   tenant).
