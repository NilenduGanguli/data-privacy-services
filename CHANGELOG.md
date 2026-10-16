# Changelog

All notable changes to Data Privacy Services (DPS). Build and deploy facts live in `HANDOFF.md`, the
API in `docs/technical/api-m0.md` … `api-m5.md`, the design in
`docs/specs/2026-10-11-data-privacy-services-design.md`.

<!-- release (M5 Task 28): set the date, fill the M5 finish-stage sentence from the merged
security review, images, OpenShift and demo, and drop known limitations those merges closed. -->

## v1.0.0 — unreleased

**M0 — Privacy Engine, Layer 1 API and Playground (`v0.1.0-m0`).** One engine for every entry
point: detect (structural key names, a pattern catalogue with checksum validation for national ids,
cards, IBANs and phones, name cues, optional Presidio NER, client declarations) → plan (a policy
gives each finding one method and the reason) → transform (AES-SIV deterministic tokens, FF1
format-preserving encryption behind `DPS_FPE_ENABLED`, HMAC pseudonyms, keyed date shift,
locale-aware surrogates, masks, buckets, generalisation, redaction) → verify (a leakage sweep that
fails closed when an original survives) → report (counts, methods, fallbacks, verification — never
a value). Keys and synthesis seeds go back to the caller and are never stored (D2); free text is
rewritten through the LLM gateway (Stellar, Vertex or a stub) as typed placeholders only (P1). Six
policy presets (`gdpr-pseudonymise`, `dpdp-pseudonymise`, `pci-dss`, `analytics-safe`,
`synthetic-realistic`, `synthetic-strict`), `X-API-KEY` keys with tenant grants and scopes, a
per-key rate limit, value-free audit, Prometheus metrics with `dps_llm_raw_pii_sent_total` (must
stay 0), the production posture guard and the Privacy Console's Playground. Exit evidence: 588 unit
tests, the smoke test 15/15 and a `linux/amd64` image; SEC-1 (the COIN token mint verifies TLS) and
the session-only console key followed.

**M1 — documents and Layer 1 complete (`v0.2.0-m1`).** `POST /documents/{detect,obfuscate,
synthesize}` for PDF text layers, scanned pages and images (OCR through the Azure Read v3.2
contract or local Tesseract), DOCX, TXT and CSV, with per-page findings and boxes and a true
redacted PDF (image-only, checked by structure and an OCR re-read that refuses the PDF when an
original is still readable); synthesis from a schema, DDL or sample through a reusable generator
spec (`/schema/*`); the entity taxonomy and a recognizer test; NDJSON streaming; `Idempotency-Key`
on the Layer 1 routes (a replica-local fingerprint cache with no key material); the opt-in T3 LLM
metadata labelling (field names and value shapes only); a measured detector evaluation; the
console's Document tab and Policies page. Exit evidence: 1 009 unit tests, the M1 integration gate
131 passed, the smoke test 25/25 and the console check 84/84.

**M2 — jobs and PostgreSQL (`v0.3.0-m2`).** The metadata database (schema `dps`, tenant row-level
security, contract roles, checksummed migrations), a durable PostgreSQL job queue (SKIP LOCKED
claims, leases, fencing, a reaper, dead letters) and the `worker` role; the PostgreSQL connector with
read-only sessions and validated selections; discovery and reviewed classification snapshots; four
write modes — `table`, `files` (Parquet, CSV or JSON Lines to S3 or a volume), `in_place` and `append` (key
strategies with a collision pre-check) — with typed confirmation for production connections (D3);
partitioned, checkpointed runs with a write ledger so a killed worker resumes without writing a row
twice; hashed evidence packs; job groups that share one caller-held key; artefacts on a volume or
S3; the console's jobs list, five-step wizard, job detail, evidence pack and reports list. Hardening
on `main`: tenant-scoped secret references (SEC-2), admin for any production-connection change and
credentials restated for an endpoint change (SEC-3), discovery that never keeps mixed or unnamed
personal data as "not personal". Exit evidence: the integrated verification passed with 1 665 unit
tests, M1 139 and M2 170 integration tests, the smoke test 34/34, resume-after-kill for every write
mode, a per-column source-vs-target comparison with 0 surviving values, and 3 004 rows/s for one
worker on 200 000 rows.

**M3 — Oracle and MongoDB (`v0.4.0-m3`).** Oracle (python-oracledb thin; keyset or ROWID
partitions; CLOB, `CHAR` and `TIMESTAMP(9)` handled exactly) and MongoDB (documents transformed by
leaf path, untouched values byte-identical, writes in replica-set transactions) behind the M2
conformance suite in every write mode; one customer space across PostgreSQL, Oracle and MongoDB
under one job-group key, with a counts-only consistency check that joins the masked targets; the
Oracle and MongoDB paths of the wizard, job groups on the jobs pages, `smoke_test.py --m3` and the
benchmark tool per engine. On `main` the M3 code adopted SEC-2/SEC-3, Oracle `tcps`, the
review-required gate on document paths and D-A (keyed ObjectId pseudonyms, the identifier check by
path). Exit evidence: lane verification passed (per-column source-vs-target 0 survivors on every
Oracle and MongoDB target, 3/3 consistency, no values in logs at DEBUG), 942 rows/s on Oracle and
860 rows/s on MongoDB for 20 000 customers.

**M4 — UI scanner (`v0.5.0-m4`).** The `browser-worker` role (Playwright Chromium in its own image)
crawls a registered UAT site within budgets, classifies every field with the shared detectors,
fills it with synthetic or obfuscated personas and shows the original and filled pages side by side
with an inventory, an export and masked captures that fail closed — and never submits anything
(D4): a scope proxy routes every hop, beacon, worker and socket APIs are removed on filled pages and
every request is aborted before a page closes (D-C). Exit evidence (lane verification on branch
`m4`): 2 363 unit and 232 integration tests, the smoke test 45/45, a demo scan of 21 pages with 0
requests other than GET received by the demo site, OCR of the masked captures finding 0 source
values. <!-- release: confirm after the M4 merge and its post-merge verification on main. -->

**M5 — hardening and showcase (ships as `v1.0.0`).** Tabular synthesis for L2 synthesize jobs: a
Gaussian copula with constraints (ranges, positivity, uniqueness, dependent fields), exact-row
rejection in flight, and fidelity (KS/TVD, correlation delta), privacy (exact-row matches,
nearest-neighbour distance ratio, quasi-identifier uniqueness, a membership-inference proxy) and
utility (train on synthetic, test on real) measured on a preview before anything is written; an
optional MOSTLY AI engine with differential privacy and linked generation; synthetic tables that
never carry a linkable real identifier (D-A: keyed pseudonyms, never-issued government-id ranges
and a post-write identifier check). The `external_tokenize` method calls the bank's tokenization
service, with reveal bound to the caller's key (SEC-4). DB-backed API keys with rotation, expiry and
an operator CLI; tenants with quotas, caps and registered hosts; the admin API (keys, tenants, audit
search with CSV, queue, posture, retention runs); report roll-ups; evidence and roll-up PDFs; a
policy store with drafts, immutable versions and `@name` references; strict verification at boot,
secrets from files only, TLS-only egress, KEK rotation (`dps.rekey`) and a runtime posture report;
a per-tenant LLM exposure counter with an egress verifier; the console's Overview, Reports & audit,
Evidence pack synthetic section, Admin and versioned Policies pages. Exit evidence: lane
verification passed in round 3; on `main` the post-merge verification passed with copula targets
at fidelity 0.970, 0 exact rows, NNDR 1.50 and 0 real identifiers in 20 000 rows. The finish stage
adds the security review, the images and compose `full`, the OpenShift manifests, the ten-minute
demo with its deck, and this documentation. <!-- release: fill from the merged finish streams. -->

### Known limitations

Privacy and synthesis (owner decisions recorded by the verifications):

- Synthetic names and RFC 2606 e-mails come from finite pools: a synthetic value can equal a real
  customer's value on an unrelated row (about 870 names per 20 000-row run on the demo seed).
- Surrogate synthesize (`options.tabular: none`) copies non-personal columns verbatim (timestamps,
  amounts), so a synthetic row can be linked back to its source row through them; the copula path
  breaks that link (0 exact non-key rows).
- The copula does not keep dependent fields consistent across columns unless a `when` constraint
  says so (e.g. `id_type` and `national_id`).
- FF1 pseudonyms of customer ids land in the issued space: a masked id can equal another real
  customer's id (FF1 never maps a value to itself).
- Government identifiers without a never-issued range (passport, driving licence, voter id, INE,
  BN, MRZ, unrecognised national-id shapes) make every synthesize that writes them
  `review_required`; `files` targets and same-table synthesize appends are not read back by the
  identifier check (`not_run`), and a concurrent foreign writer can affect its count.
- The evidence pack calls a key pseudonym `"reversible": false` although the job's synthesis seed
  inverts it; the quasi-identifier `unique_overlap` is reported, not gated; the "Post-write sample:
  N in kept columns" chip counts sampled and pseudonymised columns.
- An `in_place` synthesize of a table whose classified column has a declared foreign key fails
  closed only after its partition retries, instead of a coded refusal at submit.

Platform and operations:

- A metadata database lost after the app started is not re-probed: routes that need it answer 500
  `internal_error` (not 503 `db_unavailable`) and `/readyz` keeps its startup value until the
  database returns or the pod restarts.
- MongoDB connector input errors (`object_invalid`, `database_required`, `path_unsupported`) map to
  502 like any other connector code (contract §9), not 422.
- One worker process is bound by one Python interpreter; scale with replicas. The identifier check
  scans every written identifier, which dominates finalize time at bank scale.
- Daily quotas reset at 00:00 UTC (the mockup says IST); a key prefix shows 4 characters (24 of
  ~256 bits); an evidence pack's `expires_at` can be stale after a tenant's retention changes
  (retention keys off the job's expiry).
- Roll-up and usage rows of tenants that never ran a job (Layer 1 only) do not age out.
- `.env.example` lacks the M1, M3 and M5 variables (the build tooling could not write `.env*`
  files); `HANDOFF.md` §3 is the reference.
- The one-shot compose services inherit the image's health check and show "unhealthy" after they
  exit 0.

Security hardening left for the owner (value-free, not exploitable from outside; see
`docs/security/` for the finish-stage review):

- A tenant-scoped admin can lift its own tenant's quota, caps and retention; a key's lifetime is
  not capped by its creator's; create and rotate commit before the audit write (a strict-sink 503
  loses the minted key — rotate again); publishing has no optimistic check against a concurrently
  replaced draft; the policy-version immutability trigger has no TRUNCATE leg (owner only).
- The files-only secret rule checks the process environment by upper-case name: a literal secret
  in a `.env` file or a lower-case variable passes it (an operator misconfiguration).
- The audit CSV guard misses a leading space, newline or full-width `＝` (no guarded column is
  attacker-controlled); `/reports/*` aggregates answer to a `detect`-only key (value-free).
- A refused selection's `SelectionError` keeps sqlglot's parse error (which quotes the SQL) as its
  implicit `__context__`; nothing in DPS walks it, but an error reporter that does would see it.

Console:

- The Overview labels the most-used policy, not the tenant's adopted one; after rotating or
  disabling one's own key the top bar keeps the old caller pill; the Policies page learns it is
  read-only from the first refused write.
- An append offset that collides with existing keys is found only when the job prepares (the
  wizard checks its form, not the table).
- Evidence packs sealed before a fix keep their original wording (packs are immutable and hashed).

UI scanner (M4, from its lane verification):

- URL dates with doubled separators (`/dob/12__5__90`) survive URL redaction; bare unlabelled names
  are not detected (detector recall); some attribute- and CSS-drawn strings (generated content,
  `<track>`, canvas, video posters) are outside the capture census; passive images on routed filled
  pages can carry an encoded fill value; requests an inert popup's CSP refuses are not counted as
  blocked.
