# S1 — main after the M4 merge — ADAPTATIONS verifier, round 1

**Verdict: FAIL — 2 blocking findings (A1, A2), both in the `40d8b34` select fix this lens had to verify.**

This report was first written at 21:29 with a PASS. On resume (22:03) I re-checked it against the
parallel captures-privacy r1 lens, which finished at 21:40 with select shapes my first pass did not
cover. I reproduced two of them independently through the real Chromium extraction on `8ea220a`
(`resume_select_label.txt`), and they fail this lens:

- **A1 — regression of `40d8b34`, discovery semantics broken.** A closed select whose selected option
  has an **empty `label` attribute** (`<option value="a1" selected label="">…`) draws the option's
  text, but `selectDrawn` reads only `o.label` (`dps/layers/scanner/dom.py:208`), which is `""`.
  So `FieldFacts.drawn == []` and `shown_texts` returns nothing.
  - Under a neutral label, a select drawing an e-mail is classified **not personal** and is **not
    masked**. That is exactly what main's discovery rule (`27cf286`: "a field showing personal data
    is never not personal") forbids.
  - Under "Account number", it is classified `ACCOUNT_NUMBER` but still **not masked**.
  - Before the fix (`cbbac38`), `shown_text` used `OptionFacts.drawn` (`label or text`), which gives
    the e-mail. So that select was classified and masked; now it is neither.
  - With `value=""` as well (a B1a shape plus the empty label), the select is still not personal and
    not masked.
  - This is the same defect as captures-privacy r1 **B1**, which has the OCR, crawler and
    deployed-image proof.
- **A2 — the B1a fix is incomplete for real values that start like a placeholder.**
  `draws_placeholder` (`classify.py:403-418`) checks only that the text starts with something like
  `PLACEHOLDER_OPTION = ^\W*(choose|select|pick|please|--)` (`classify.py:376`).
  - A B1a-shape select (`value=""`) drawing a real name ("Pickering …", `autocomplete=name`) or a
    real e-mail ("pickering…@…") is classified and boxed. But it counts as a "placeholder", so its
    value is **never swept** (`swept=0`, `capture.shown_values`), and its other copies on the page
    stay readable.
  - This contradicts the fix's own docstring ("a value behind an empty value (B1a) … [is] never [a]
    placeholder").
  - It also corrects my first report's N6, whose "no leak" reasoning held only for a text like
    "Select 4111…".
  - This is the same defect as captures-privacy r1 **B2**, which has the OCR proof, including the
    disabled-option regression of the masked capture.

**The rest of this lens holds**, and the running code is still the tree at the resume
(`code_identity_resume.txt`: 177 files, 0 differing in app, worker and browser-worker).

Every M4 adaptation holds on main, in process and on main's running stack:
- SEC-5;
- SEC-2 for scanner secrets and seeds;
- M5 database keys and the `scan` scope on every scans route;
- M5 posture for the browser-worker role;
- M5 `@name` policy references;
- main's discovery semantics in scanner classification.

The merge `5959a75` kept both sides of every conflict.

The two open S2 r3 findings:
- **Console B1 (`932075a`): fixed.** Its Vitest cases pass.
- **Captures-privacy B1a–c (`40d8b34`): fixed for the three shapes as reported.** They are classified, masked and never treated as placeholders. I checked this in memory and through the real Chromium extraction.
  - But the fix regresses the empty-`label` select (**A1**).
  - It also leaves B1a-shape values that start like a placeholder unswept (**A2**).

The gates verifier's report (`S1-M4-gates-r1.md` §4) carries the OCR, crawler and deployed-image proofs of those fixes.

The non-blocking notes are in §4: five carried from earlier rounds, N7 (new), and N8 (listbox, pre-existing, reproduced). N6 was promoted to A2.

I changed no repository file.

| | |
|---|---|
| Date | 2026-10-15, 21:05–21:30 IST; resume check 22:03–22:15 IST (HEAD, status and code identity unchanged; stack still 11/11 healthy) |
| Tree | Main checkout, `main` @ **`8ea220a`** (= `origin/main`). `git status` was empty at the start and at the end, and HEAD did not move. |
| Running code | The sha256 of all 177 tracked `dps/**` files and `pyproject.toml` is identical in `app`, `worker` and `browser-worker`: 0 differing at the start and 0 at the end (`code_identity.txt`, `code_identity_end.txt`). |
| Stack | Left as the gates verifier left it: five profiles, all 11 services healthy. I did not restart, rebuild or `down` it. Other lens verifiers were running scans in tenant `default` at the same time. |
| Probes | `~/.cache/dps-build/reports/S1-M4-adaptations-r1/`: scripts and outputs. They hold codes, counts, ids and booleans only. A final scan of the outputs found 0 bootstrap literals, 0 raw-key-shaped strings and 0 `dek`/`seed` fields. |

## 1. Lens results

| # | Item | Result | Evidence |
|---|---|---|---|
| 1 | **SEC-5**: `storage_state_ref` is resolved by main's `resolve_secret_ref(tenant_id, ref, settings)`, checked at ScanSpec accept, and refused across tenants | **PASS** | `sec5_inprocess.txt` (80/80), `live_probe.txt` |
| 2 | **SEC-2** for scanner secrets and seeds | **PASS** | `sec2_volume.txt`, `live_probe.txt` (custody) |
| 3 | **M5 database keys and the `scan` scope** on every scans route, plus the `/jobs`, `/evidence`, `/artefacts` and `/reports` kind rule | **PASS** | `live_probe.txt` (147/147), `openapi_routes.txt` |
| 4 | **M5 posture rules, browser-worker role** | **PASS** | `posture_roles.txt` (41/41) |
| 5 | **M5 `@name[:vN]` policy references in ScanSpec** | **PASS** | `live_probe.txt` (accept, pin, end to end) |
| 6 | **Discovery semantics in scanner classification**: mixed PII is never "not personal" | **FAIL (A1)**. Mixed PII and value rules hold, but an empty-`label` select showing an e-mail is "not personal" | `discovery_semantics.txt` (16/16), `select_extraction.txt` (7/7), `live_addendum.txt`, **`resume_select_label.txt`** |
| 7 | **Merge `5959a75` kept both sides**, diffed against both parents | **PASS** | `merge/*` |
| 8 | **S2 r3 open findings** (console B1, captures-privacy B1a–c) | Console B1 **fixed**. B1a–c **fixed as reported**, but `40d8b34` regresses `label=""` (**A1**) and leaves placeholder-prefixed B1a values unswept (**A2**) | `vitest_console_b1.txt`, `discovery_semantics.txt`, `select_extraction.txt`, **`resume_select_label.txt`** |

### 1.0 Blocking findings (resume check, `resume_select_label.py` / `.txt`)

**Setup.** Real extraction on `8ea220a`: host Chromium 140.0.7339.16, the same build as the
browser-worker image, whose `dps/**` is byte-identical to the tree. Each page runs through
`extract_page` → `classify_fields` → `sensitive_prefilled` → `draws_placeholder` →
`capture.shown_values`. Values are made up, and only entity types, counts and booleans are printed.

| Page (one select, made-up value) | Classified | Masked | Placeholder | Swept | Note |
|---|---|---|---|---|---|
| CTRL: no `label` attribute, neutral label, e-mail, `value="a1"` | EMAIL | yes | no | 1 | correct |
| `label=""`, neutral label, e-mail, `value="a1"` | **none** | **no** | no | 0 | **A1**. `FieldFacts.drawn` n=0, while `OptionFacts.drawn` holds the value |
| `label=""`, `value=""`, neutral label, e-mail | **none** | **no** | no | 0 | **A1** (B1a shape) |
| `label=""`, "Account number", account digits, `value="a1"` | ACCOUNT_NUMBER | **no** | no | 0 | **A1**: classified by label, never boxed |
| `value=""`, "Beneficiary name" `autocomplete=name`, "Pickering …" | PERSON_NAME | yes | **yes** | **0** | **A2**: its copies elsewhere are never swept |
| `value=""`, neutral label, "pickering…@example.org" | EMAIL | yes | **yes** | **0** | **A2** |
| CTRL: real placeholder "Choose…" under "Account number" | ACCOUNT_NUMBER | yes | yes | 0 | correct |
| Listbox `size=3`, nothing selected, "Account number" (info) | ACCOUNT_NUMBER | **no** | no | 0 | captures-privacy r1 **B3** (pre-existing since M4, masking), reproduced; see N8 |

**A1 — fix (test-first; weaken nothing):**
- In `selectDrawn`, push `o.label || o.text` (`dom.py:208`). That is HTML's "label of an option", as `OptionFacts.drawn` (`models.py:89`) already does.
- Add a `shown_texts` and classify unit case, plus a real-extraction case with `label=""`:
  - under a neutral label (it must be classified by value and masked);
  - under "Account number" (it must be masked);
  - with a whitespace-label control.
- Reword `api-m4.md` from "when it has one" to "when it is not empty".

**A2 — fix:**
- Sweep whatever a select draws, and keep the placeholder rule only for the obfuscated-capture trigger.
- Otherwise make `draws_placeholder` exact: a whole short placeholder phrase, with no digits and no `@`, from a listed, enabled option.
- Add unit cases ("Pickering …" with an empty value, disabled with a value, past the list) and the captures-privacy r1 `r4_ph_*` pages.

Both findings are the same root causes as captures-privacy r1 B1/B2. One fix closes both lenses.

### 1.1 SEC-5

**Code (read).**
- `dps/layers/scanner/__init__.py::_storage_state` is the only place a scan reads a storage state. It calls `resolve_secret_ref(job.tenant_id, ref, ctx.settings)`, and every refusal becomes `secret_unavailable`.
- `validate_scan(..., tenant_id=)` runs `check_secret_ref` for the caller's tenant.
- `ScanAuth.storage_state_ref` takes `SECRET_REF_PATTERN`, and `..` is refused by the model.
- `dps/secrets.py` is unchanged on both sides of the merge (identical to its merge base).
- In `dps/`, only `secrets.py` touches the secret folder. The scanner, `connections.py` and `postgres.py` (for `ca.pem`) all go through it.

**In process** (`sec5_inprocess.py`). I built a throw-away `DPS_SECRET_DIR` and ran 35 cases through both the accept path (model + `validate_scan`) and the browser-worker's `open_runtime`, which had a real sealed `JobSecret`.
- The folder held three tenants (north, south, east), a `_shared` folder with an allow-list, and a ref whose first segment is the tenant itself.
- It also held directory and file symlinks out of the tenant and shared folders, an allow-list file that links outside, a top-level file, non-JSON and array states, and a folder with credentials but no state.

Results:
- **80/80 checks passed.**
- A ref naming another tenant's folder → 403 `secret_ref_foreign_tenant`. A shared folder that does not list the tenant, or does not exist → 403 `secret_ref_not_shared`. A hidden, reserved or empty segment, or a symlink leaving the folder → 422 `secret_ref_invalid`.
- `..`, an absolute path, upper case, a newline or a space → model refusal (`auth_invalid`).
- Every such ref, if a stored row were tampered to carry it, fails at load with `secret_unavailable` or `spec_invalid`.
- **Foreign reads: 0.**
- A spy recorded exactly one resolver call per load, always with (the job's tenant, the stored ref, `ctx.settings`). Registered hosts were read for the job's own tenant only.
- Reserved or malformed tenant ids (`_system`, upper case, empty, 70 characters) are never resolved.

**Live** (tenant `s1m4a`, a database key with only the `scan` scope):

| Case | Result |
|---|---|
| `default/dps/ui/<demo>`, `default`, `default/dps/src/bank-pg-ro` | 403 `secret_ref_foreign_tenant` |
| `_shared/ui/<demo>`, `_shared/default/…` | 403 `secret_ref_not_shared` |
| `dps/.hidden/x`, `dps/_reserved/x`, `dps//ui` | 422 `secret_ref_invalid` |
| `../…`, `dps/../../…`, an absolute path, upper case | 422 `auth_invalid` |
| A ref and an inline state together | 422 `auth_invalid` |
| Tenant B's key naming tenant A | 403 |

- None of the 12 refusals echoed the ref, a tenant name or the session name.
- 0 scans were queued, and exactly +12 `scan.create` rejected audit rows were written.
- `default`'s relative session name `dps/ui/demo-onboarding`, sent from `s1m4a`, is accepted because it names a folder in `s1m4a`'s own namespace. The live browser-worker then **fails it: `secret_unavailable`, 0 pages, 0 navigations, key purged**. The stored spec holds only the ref (`storage_state: null`).

**Controls in `default`**, the only tenant holding the demo session, both started at `/profile`:
- With the ref: `succeeded`, and page 1 is `/profile` `done` with no auth redirect. The evidence records `source.auth = storage_state_ref`.
- Without the ref: page 1 is `redirected/auth_redirect` to `/login`.

So the same ref logs in for its own tenant and fails closed for any other.

### 1.2 SEC-2 for scanner secrets and seeds

**Resolved compose** (profiles `scanner` and `sources`):
- `app`, `worker` and `browser-worker` mount the `dps-secrets` volume at `/var/run/secrets/dps` **read-only**, with `DPS_SECRET_DIR` set to that path.
- All three have a read-only root and `cap_drop: ALL`.
- The browser-worker waits for `demo-secrets` (`service_completed_successfully`).
- `demo-secrets` runs in profiles `sources`, `scanner` and `full`. It copies the demo session from the read-only `/seed/ui` bind into `default/dps/ui/demo-onboarding/`.

**Live containers:**
- The mount is `RW=false` in all three, and the folder is not writable.
- The only top-level folder is `default`. `default/dps` holds `s3 src tgt ui`, and `ui` holds `demo-onboarding/storage_state.json`.
- No `storage_state.json` exists outside the volume in any of the three containers.
- All three run as uid 10001.

**Repository and images:**
- `git ls-files docker/secrets` lists only the tenant-scoped demo literal, and its sha256 equals the copy in the volume.
- `.dockerignore` excludes `docker/secrets`.
- `Dockerfile.browser` copies only `pyproject.toml`, `README.md` and `dps/`. `Dockerfile` adds the console, `tools/` and the entrypoint, never `docker/secrets`.

**Custody of scan keys and seeds** (20 `dek`/`seed` strings returned by the accepted probe scans, held in memory only):
- **0** occurrences in 215 rows of the probe tenants across `dps_job`, `dps_audit_log`, `dps_scan_page`/`field`/`request`/`event`, `dps_evidence` and `dps_artefact`.
- **0** occurrences in 534 app, worker and browser-worker log lines of the window. Those lines also hold 0 raw probe keys and 0 bootstrap literal.
- **0** occurrences in S1's evidence JSON and HTML, its JSON and CSV exports, or its inventory.
- `key_enc` was purged on all 8 terminal probe scans, including the canceled one.
- The create answer carries `Cache-Control: no-store`.
- An idempotent replay answers 200 with `replay: true` and no key or seed.

### 1.3 M5 database keys and the `scan` scope

I walked all 10 operations under `/api/v1/scans` in the live OpenAPI. For each operation, I tried the probe keys below (all database keys) and no key at all.

| Key | Result |
|---|---|
| `scan` key | Never 401 or 403. Cancel of the finished S1 → 409 `not_cancelable`. |
| `jobs`+`detect` key | **403 on 10/10** |
| No key | **401 on 10/10** |
| Tenant B's `scan` key | **404 `scan_not_found` on 7/7** of the `{scan_id}` routes |
| Tenant B's key naming tenant A | **403 on 10/10** |

**Multi-tenant `scan` key:**
- With no `X-DPS-Tenant` → 403.
- Naming A → 200 with A's scans.
- Naming B → 200 without them.
- Naming an ungranted tenant → 403.

**The kind rule on `/jobs`**, with S1 running and S2 held queued by `max_running=1`. The `jobs`+`detect` key got **403 `scope_required` on all 12** of these:
- `GET /jobs/{id}` and `/partitions`;
- `cancel`, `pause`, `resume` and `retry`, on both scans.

The 8 refused controls were audited `job.<action>` rejected under that key's id. S1 stayed running and S2 stayed queued. For the same key:
- `GET /jobs` lists 0 scans, `?state=queued` omits S2, and `?kind=scan` → 403.

A `jobs`+`scan` key lists both scans. The `scan` key then cancelled S2 through `/scans/{id}/cancel`.

**Main's retry gate meets M4's kind rule** (`live_addendum.txt`). A `jobs`+`scan` key posting `POST /jobs/{failed scan}/retry` gets 409 `job_not_retryable`, with or without a body, and the job is unchanged.

**Evidence, artefacts and reports for S1:**
- `/evidence/{id}`, `/html`, `/pdf` and `/artefacts/{screenshot}`: `scan` key 200, `jobs`+`detect` key **403 `scope_required`**, tenant B 404.
- `GET /evidence?job_id=S1` lists nothing for the jobs-only key, and neither does `/reports/evidence`. The `jobs`+`scan` key sees the pack in both.

**Key lifecycle:**
- A disabled key goes 200 → **401 at once**.
- Rotation with grace 0: the old key is 401 at once. The successor gets 200 on `/scans` and still 403 on `/jobs`.
- `created_by` on every `s1m4a` scan and every audit row of the scan routes names the probe database key.

**M5 hosts:**
- Registered hosts `[portal.example.org]` → 422 `host_not_registered`.
- `[DEMO-SITE]` → accepted.
- Restored to `[]`. Posture reports `scan_fallback_hosts` `['localhost','127.0.0.1','demo-site']` (env local).

### 1.4 M5 posture, browser-worker role (`posture_roles.py`)

I started from a production configuration that the app, the worker and the browser-worker all accept, then broke one rule at a time: 29 mutations.

- **`worker` == `app` on every mutation.**
- The browser-worker is refused exactly like the app, except for the five components it never runs:
  - no API keys;
  - the Azure OCR key;
  - "OCR auto resolves to none";
  - the tokenizer token;
  - `CORS *`.

  Its problems are always a subset of the app's.
- An `http` OCR, tokenizer or S3 endpoint still refuses it through the TLS-egress rule.
- The browser-worker's own M5 deployment (the KEK only, no API, OCR or tokenizer secret) passes its role, while the app refuses the same configuration on exactly those three secrets.
- All **12** literal `SECRET_ENV` variables refuse all three roles, by name and never by value.
- An unknown role raises `ValueError`. `local` and `dev` apply no rule.
- `amain` raises `PostureError` before it touches the database, the ops port or Chromium. With a passing configuration it goes on to its I/O.

### 1.5 M5 `@name` policy references (live)

**Setup:** in `s1m4a` I adopted `gdpr-pseudonymise` as v1, then published v2 with `min_score` 0.55. v2 is active, and the two hashes differ.

**At accept:**
- `@<name>` → 202, and the stored spec pins **v2** (0.55).
- `@<name>:v1` → 202, pins **v1** (0.6).
- `@<name>:v2` → 202, pins v2.
- `@s1m4r1-nope` and `:v9` → 404 `policy_not_found`. `@Bad Ref` → 400 `invalid_policy_ref`.
- The same name from tenant B, or from `default` → 404.
- Only the 3 resolvable references were queued, and every refusal was audited `scan.create` rejected.

**End to end:**
- S1 was accepted with `@<name>:v1` while v2 was active, and ran in the live browser-worker: `succeeded`.
- **Its evidence names the store policy, version 1, and its `policy.hash` equals the store's v1 hash, not v2's.**
- Demo-site counters were +0/+0/+0/+0 over the S1 window and the whole probe window (16/4/2/2 before and after), even with other verifiers' scans running at the same time.

### 1.6 Discovery semantics in scanner classification

**Shared constants:**
- `classify.py` and L2 `discovery.py` use the **same objects** from `structural.py`: `CHANNEL_NAMES`, `MIXED_VALUE_NAMES` and `MIXED_NAME_SCORE`.
- `MIXED_PII` and `FREE_TEXT` are the snapshot constants on both sides.

**L2 parity:**
- An L2 `contacts(channel, value)` table and a form with Channel and Value fields are both mixed by name at bar 0.6, and neither is at 0.8.
- "Loan value" is no contact value on either side.

**Grouping:**
- "Preferred contact method" + "Your contact details" → `MIXED_PII`, and so does "Contact type" + "Contact".
- Fields in another form or frame → not mixed. Both fields outside any form → `MIXED_PII`.
- A password input is classified by its type, and its value is never read.

**Property sweep:** 16,716 fields = persona values of all 5 locales × 4 neutral labels × 7 shapes.

The shapes:
- a pre-filled input;
- a sentence in a textarea;
- a classic select;
- the three select shapes of `40d8b34`: an empty `value` (B1a), the selected option at index 350 of 400 (B1b), and a customizable select (B1c);
- a drawn `label` attribute.

Results:
- **2,044 fields show a MEDIUM+ detector hit at the bar.** By entity: CARD_PAN, CUSTOMER_ID, EMAIL, IBAN, PHONE and PERSON_NAME.
- **All 2,044 are classified (0 "not personal") and all 2,044 are masked** (`sensitive_prefilled`).
- None of them reads as a placeholder.
- Each shape contributes 292 fields.

**Negatives** stay "not personal": plain text, a quantity, a country choice, a hidden input holding a test card number (never read), a file input, and a "Choose…" select.

**Real extraction** (`select_extraction.py`, host Chromium 140.0.7339.16, the same build as the image):
- The B1a (×3), B1b and B1c pages are each extracted with the drawn value, classified personal (`ACCOUNT_NUMBER` or `EMAIL`), masked, and not a placeholder.
- A "Choose…" select under "Account number" is classified by its label, masked and a placeholder.
- A neutral country select stays unclassified and unmasked.

**Live:**
- For both `default` and `s1m4a`, `/reports/summary` `layers.ui.personal_fields` equals the window's pages' `personal_field_count`, which equals the personal field rows: 778 = 778 = 778 and 26 = 26 = 26.
- The full demo scans in today's listing each show 63 fields, 51 of them personal. In each, 2 of the personal fields were classified from what they show (`value:` evidence). The database stores no field value, so this is a listing, not a check.

## 2. Merge `5959a75` kept both sides (`merge/`)

I worked in an isolated `--shared` clone under `merge/clone`, never in the repository.

**The recomputed merge** (`git merge-tree` of `d2fdc3f` and `cbbac38`, base `144cc65`):
- It has **one textual conflict**, contract §8.1.
- `5959a75` equals that auto-merge tree in every file except the conflict resolution.
- The resolution keeps main's amendment ("every write to a production-tagged connection … and the retry") and m4's job-kind rule (`automerge_vs_5959a75.diff`).
- No other edit was made in the merge.

**Line survival** (`both_sides.txt`):
- Main side: 2,634 non-blank lines added since the base. m4 side: 28,612.
- In `5959a75`, 1 main line and 4 m4 lines are absent. All five are the reflowed §8.1 conflict lines, whose words are all present.
- 7 files were changed by both sides: HANDOFF, the contract, `jobs.py`, connectors `evidence.py`, `Admin.tsx`, `EvidencePack.tsx` and `EvidencePack.test.tsx`. Both hunks of each are present (`both_sides_hunks.txt`).
  - `jobs.py` keeps `_owned`, `_check_kind` and `exclude_kind`, and main's `check_retry_scope` on retry.
  - `evidence.py` keeps main's `column_key` counts and m4's `_scan_section`.

**Hotspots** (`hotspots.txt`):
- Every plan §2.5 hotspot changed by one side only equals that side's version in the merge.
  - m4-only: `pyproject.toml`, `config.py`, `posture.py`, `app.py`, `docker-compose.yml`, `Dockerfile.browser`, `App.tsx`, `Layout.tsx`, `api.ts`, `types.ts`, `README.md`, `jobs.py` (dps) and `reports.py`.
  - Unchanged on both sides: `Dockerfile`, `tests/conftest.py`, `.env.example` and `secrets.py`.
- Migrations: 19 files with no duplicate number (001–010, 020, 030–031, 040–045).

**Merge to HEAD:**
- 38 side lines are gone at HEAD. All were removed by the two fix commits, in the files they fix: `classify`, `capture`, `dom`, `models`, `personas_fill` and `crawler` (`40d8b34`); `Reports.tsx` and the duplicate `EvidenceOut` import (`932075a`); plus doc lines.
- Only the scanner imports any file the main side changed, and only `connectors/evidence.py`. Its S1 pack renders, with HTML and PDF both 200.
- Commits `5959a75`, `932075a`, `40d8b34` and `8ea220a` are authored and committed by `nilendu.ganguli@gmail.com`, with 0 trailers.

## 3. The S2 r3 open findings (this lens)

- **Console B1 (`932075a`):** the four affected Vitest files pass on main: 56/56 (`vitest_console_b1.txt`). These are `reports.test.ts`, `EvidenceTable.test.tsx`, `EvidencePack.test.tsx` and `Reports.test.tsx`. The commit also removes the merge's duplicate `EvidenceOut` import and nothing else of either side. The gates verifier checked all 22 failed packs on the live Reports page at 1440 and 390.
- **Captures-privacy B1a–c (`40d8b34`):**
  - `shown_texts` now reads what a select draws, whatever its option's value. `EXTRACT_JS` reads every selected option apart (`FieldFacts.drawn`), and a customizable select's button text is read and boxed.
  - In this lens, the B1 shapes are classified personal and masked in 2,044/2,044 sweep fields and in 7/7 real extractions.
  - The OCR, crawler and deployed-image proofs are in `S1-M4-gates-r1.md` §4.2.
  - **Resume correction.** The sweep built `FieldFacts` in memory, so it never exercised `selectDrawn`'s `o.label`. The 7 extractions had no `label=""` page and no value that starts like a placeholder. Two counterexamples fail at HEAD:
    - **A1**: `label=""` is not personal and not masked, a regression from `cbbac38`.
    - **A2**: a value-bearing B1a select whose text starts with pick, please, select, choose or `--` counts as a placeholder and is not swept.
    - See §1.0.

## 4. Notes (non-blocking)

- **N1 — carried (S2 r2 N1): `/reports/summary` shows scan briefs to `detect`-only keys** (owner decision).
  - Live: `recent_jobs` held 6 scans for a `detect`-only key, and `layers.ui.last_scan` was present.
  - Nothing more is reachable with those ids: `GET /scans/{id}` → 403.
  - The contract §9 M5 row documents this.
- **N2 — carried (S2 r2 N2): the browser-worker never verifies migrations, but posture requires `DPS_MIGRATIONS_MODE=verify` in staging/prod.**
  - Compose runs it with `off` in local, where no rule applies.
  - Already handled on the `fos` branch: its configmap sets `verify`, and an init container waits for `python -m dps.migrate --verify`.
- **N3 — carried (S2 r2 N3, cosmetic):** the `demo-site` compose service still lacks its `env: local` label.
- **N4 — carried (S2 r2 N4): the policy store's usage counts ignore scans.**
  - `policy_store._job_counts` counts `kind = 'run'`, as its docstring says.
  - Live: 5 scans pinned v1 or v2 of the probe policy, but `jobs` = 0 and `jobs_pinned` = 0 for both versions.
  - No action depends on it, because versions are immutable and policies are not deletable.
- **N5 — carried: the `scan.finalize` audit row names a store policy by its body name (`s1m4r1-pol-…`), not `<name>@v<N>`.**
  - Runs do the same (`job.finalize`: `gdpr-pseudonymise` ×491). Contract §5.5's label is applied on the L1 routes only.
  - The evidence pack records the version.
- **N6 — promoted to blocking A2 on resume.** The "no leak" reasoning below holds only for a text like "Select 4111…", whose bare value is a different needle. It fails for a real value whose *whole* drawn text starts with the prefix, such as "Pickering …" or "pickering…@…": that text is the needle, and it is dropped from the sweep. The original note follows.
- **N6 (original) — `classify.PLACEHOLDER_OPTION` decides on the first word.**
  - A select with an empty-value option drawing "Select 4111 1111 1111 1111" counts as a placeholder.
  - It is still classified (`FREE_TEXT`) and **masked** in every stored screen (`sensitive_prefilled` ignores the placeholder rule).
  - Two consequences only: its drawn string is not swept for copies elsewhere, and on its own it does not trigger the page's obfuscated capture.
  - No leak: a copy of the bare value elsewhere would not match that needle anyway, and rendered entities are masked by their own detection.
- **N7 — new, informational: Admin › Posture (`posture_report`) lists 11 checks, none for the scanner's two boot rules** (`DPS_SCAN_STORE_UNMASKED_ORIGINALS=false`, `DPS_BROWSER_HEADLESS=true`). Boot still enforces both (§1.4).
- **N8 — reproduced, owned by captures-privacy r1 B3 (pre-existing since M4, not the merge): a listbox (`size>1` or `multiple`) with no option selected is never masked**, even when it is classified `ACCOUNT_NUMBER` by its label (§1.0, last row).
  - Its classification is correct, so this is a masking defect, outside this lens.
  - The same fix family as A1: what a select draws must be what is boxed.

## 5. State left on main's stack

- **Tenants:**
  - `s1m4a`: `max_running` reset to null, hosts `[]`.
  - `s1m4b`.
  - Policy `s1m4r1-pol-211738` v1 and v2 (policies are not deletable).
- **Scans:**
  - 8 in `s1m4a`: 6 failed by design (`secret_unavailable`), S1 `s_01M50444RKDKVG37QBB1765VWM` succeeded, S2 canceled.
  - 2 control scans in `default`: `s_01M5040KDXTTTN2XF40AMBC7WK` with the ref, `s_01M5040KECCFZDEZP95732JMDG` without it. Both started at `/profile` with `max_pages` 1.
- **Keys:** 9 probe database keys named `s1m4r1-…`, including the rotation successor and the two addendum keys. **0 active** (checked in the database).
- **Demo-site counters:** 16/4/2/2, unchanged by this round.
- **Repo:** clean at `8ea220a`. Nothing staged, committed or pushed. The resume check ran in process only (host Chromium, `set_content`, no network) and changed nothing on the stack.

## 6. Probe files (`~/.cache/dps-build/reports/S1-M4-adaptations-r1/`)

| File | What it checks |
|---|---|
| `code_identity.sh`, `code_identity.txt`, `code_identity_end.txt`, `code_identity_resume.txt` | Running code = tree (start, end, resume at 22:0x) |
| `resume_select_label.py` / `.txt` | **A1/A2** through the real Chromium extraction (8 pages: 2 controls correct, 5 defect lines, 1 listbox info line) |
| `sec5_inprocess.py` / `.txt` | SEC-5 accept + load + resolver spy (80/80) |
| `sec2_volume.txt` | Compose mounts, live mounts and layout, repo, images, demo-session hash |
| `live_probe.py` / `.txt` | SEC-5 live and controls, policy refs, hosts, S1/S2, `/jobs` rule, OpenAPI walk, evidence, artefacts, reports, keys, custody, D4 (147/147) |
| `live_addendum.py` / `.txt` | Scan retry → 409, `personal_fields` consistency, stored-scan listing |
| `posture_report_live.txt` | `scan_fallback_hosts` and the posture report's checks |
| `posture_roles.py` / `.txt` | Role differential (41/41) |
| `discovery_semantics.py` / `.txt` | Shared constants, L2 parity, grouping, 16,716-field sweep, negatives, placeholder rule (16/16) |
| `select_extraction.py` / `.txt` | B1a–c through the real Chromium extraction (7/7) |
| `vitest_console_b1.txt` | Console B1 Vitest files (56/56) |
| `openapi_routes.txt` | The scans, `/jobs/{id}` and evidence operations of the live OpenAPI |
| `merge/` (`both_sides.py`/`.txt`, `both_sides_hunks.txt`, `hotspots.txt`, `automerge_vs_5959a75.diff`, `merge_tree_out.txt`, `clone/`) | The merge kept both sides |
