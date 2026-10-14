# UI scanner API (M4)

Layer 3 of DPS: register a web front end, crawl it in a sandboxed Chromium, inventory and classify
every form field, flag privacy risks, render a **parallel view** of every form filled with synthetic
personas and with reversible tokens, and capture the pages that render personal data with that data
masked — **without ever submitting anything** (decision D4, below). This page is the reference as
built on branch `m4` (2026-10-14). The live schema is `/api/v1/openapi.json`.

Base path `/api/v1`, the same keys and headers as Layer 1 and the job platform
(`docs/technical/api-m0.md`, `docs/technical/api-m2.md`): `X-API-KEY`, `X-DPS-Tenant` (lower-case
tenant ids; `_…` is refused with 403 `tenant_reserved`), `X-Request-ID`.

**Conventions.**

- Every `/scans` route needs scope **`scan`** (403 `scope_required` otherwise) and the metadata
  database (503 `db_unavailable` while `DPS_PG_HOST` is unset or the database is down), except
  `POST /scans/personas/preview`, which is stateless and needs no database. All are rate-limited
  per key like Layer 1.
- Errors are `{"detail": str, "code": str}`; `detail` never quotes the request (no URL, query,
  label, storage state, key or value). 422 validation errors of the request model list
  `{type, loc, msg}` only (the scan spec refuses unknown keys at every level, so a `submit` field
  anywhere is a 422 too).
- Ids: scans are jobs of kind `scan` with the prefix `s_` (`s_` + 26-character Crockford ULID);
  artefacts `art_…`, evidence `ev_…`.
- Audit (`dps_audit_log`, value-free): `scan.create` (accepted, replayed and refused), `scan.cancel`,
  `artefact.read`; the browser-worker writes `scan.navigate` per page (page number, duration and
  counts only) and `scan.finalize`.
- Nothing a route answers carries page data. Page facts are stored **value-free** (redacted URLs and
  titles, labels as read with any detected value replaced by `<ENTITY>`, counts, entity types, risk
  codes); fill values are **synthetic** (persona values) or **tokens of persona values**. Values the
  page itself rendered are never stored; screenshots of pages that render personal data are stored
  only with that data masked (`DPS_SCAN_STORE_UNMASKED_ORIGINALS=false`, enforced in
  staging/prod by the posture guard).

## Routes

| Method · path | Request → response | Error codes (besides 401/403/429/503) |
|---|---|---|
| `POST /scans` | `ScanSpec` → **202** `{scan, key_material, synthesis_seed, replay: false}`, `Cache-Control: no-store`; honours `Idempotency-Key` (a replay is **200**, `Idempotent-Replay: true`, `{scan, key_material: null, synthesis_seed: null, replay: true}`) | the accept-time table below |
| `GET /scans` | `?state&limit&cursor` → page of scan jobs, newest first (`limit` 1–200, opaque `cursor`) | `cursor_invalid` 400 |
| `GET /scans/{id}` | → `{scan, summary}` (live counters; the console polls every 2 s while it runs) | `scan_not_found` 404 |
| `GET /scans/{id}/pages` | `?after&limit` → `{items: [ScanPage], next_cursor}` in `page_no` order; `next_cursor` is the last `page_no` of a full page, passed back as `after` | `scan_not_found` 404, `cursor_invalid` 400 |
| `GET /scans/{id}/pages/{page_no}` | → `{page, fields: [ScanField], requests: [RequestCount]}` (fields in overlay order) | `scan_not_found` 404, `page_not_found` 404 |
| `GET /scans/{id}/events` | `?after&limit` (default 100) → `{items: [ScanEvent], next_cursor}` in `seq` order (the navigation log) | as `/pages` |
| `GET /scans/{id}/inventory` | `?format=json` → `{items: [InventoryRow], risk_flags: [NumberedRiskFlag]}`; `?format=csv` → `text/csv` attachment `<id>-inventory.csv`. Built live from the tables, so it works while the scan runs | `scan_not_found` 404 |
| `GET /scans/{id}/export` | `?format=json\|csv` → the stored export (`dps.scan-export.v1`), attachment `<id>-export.<fmt>`, `Cache-Control: no-store` | `export_not_ready` 409 (not finalized yet), `scan_not_found` 404 |
| `POST /scans/{id}/cancel` | → the scan job. A scan still queued for its first attempt becomes `canceled` at once; a running one stops at its next page and is finalized (evidence, exports, key purge) | `not_cancelable` 409 (finishing or finished), `scan_not_found` 404, `idempotency_key_invalid` 422 |
| `POST /scans/personas/preview` | `{locale, count (1–5), synthesis_seed?}` → `{synthesis_seed, personas: [{persona, values}], notes}` | `seed_invalid` 422 |
| `GET /artefacts/{id}` | scope `jobs` or `scan`, then the owning job's kind (`scan` for scan artefacts) → the bytes (`image/png`, `application/json`, `text/csv`, `text/html`), attachment `<artefact id>.<ext>`, `Cache-Control: no-store` | `artefact_not_found` 404, `scope_required` 403 |
| `GET /evidence?job_id=s_…` · `GET /evidence/{id}` · `GET /evidence/{id}/html` | the scan's `dps.evidence.v1` pack (M2 routes; scope `scan` for scan packs) | `evidence_not_found` 404, `scope_required` 403 |

There is **no route** that submits a form, writes to a scanned site, or returns stored key material.

## `POST /scans` — the request (`ScanSpec`)

| Field | Default | Rules |
|---|---|---|
| `url` | — (required) | `http`/`https`, 8–2048 characters, no userinfo; its host must be **registered** for the tenant (`dps_tenant.registered_hosts`; in `local`/`dev` with none registered, `DPS_SCAN_REGISTERED_HOSTS`, default `localhost,127.0.0.1,demo-site`) |
| `scope.same_origin` | `true` | the crawl never leaves the start URL's origin |
| `scope.max_pages` | `DPS_SCAN_MAX_PAGES` (50) | 1 … `DPS_SCAN_MAX_PAGES` (more → `scope_invalid`) |
| `scope.max_depth` | `DPS_SCAN_MAX_DEPTH` (3) | 0 … 5 |
| `scope.allow` / `scope.deny` | `[]` | path globs (≤ 50 / ≤ 100); deny wins and holds for **every request** of the scan; a non-empty allow list holds for every navigation |
| `scope.iframes` / `scope.shadow_dom` / `scope.spa_clicks` | `true` | read same-origin iframes / open shadow roots; click in-scope elements without a plain `href` to find SPA routes |
| `auth.storage_state_ref` | — | a mounted Playwright `storage_state.json`: `DPS_SECRET_DIR/<tenant>/<ref>/storage_state.json` (tenant-scoped; `..` refused). The demo session is `dps/ui/demo-onboarding` |
| `auth.storage_state` | — | inline storage state, dev only (`DPS_INLINE_SECRETS_ALLOWED`); sealed with the key, never stored in the spec (`{"inline": true}` there). Not together with the ref |
| `mode` | `synthesize` | `synthesize` \| `obfuscate`: how the masked captures replace rendered data (persona surrogates, or tokens with a reversible map), the parallel view's default side and the evidence label |
| `fill.personas` | 1 | 1–50 personas generated (the export carries all of them) |
| `fill.locale` | `en_US` | `en_US`, `en_GB`, `en_IN`, `en_CA`, `es_MX` |
| `fill.display_persona` | 1 | which persona fills the screenshots and the stored `fill_*` values (≤ `personas`) |
| `parallel_view` | `true` | fill every form twice (synthesize, obfuscate) in fresh tabs and capture both |
| `capture.screenshots` | `true` | |
| `capture.har` | `false` | `true` → 422 `har_not_supported` (a HAR holds response bodies) |
| `consistency` | `{scope: "key"}` | M2's `ConsistencySpec`: `key_material` / `synthesis_seed` to reuse the caller's (same tokens and personas as an earlier scan), `wrap_key: {type: "rsa-oaep-256", public_key_pem}` to receive the key RSA-wrapped |
| `policy` | `DPS_DEFAULT_POLICY` | a preset name or an inline policy (method per entity type, detector threshold) |
| `purpose` | — | `{ticket, legal_basis}` recorded in the evidence pack |

```http
POST /api/v1/scans
X-API-KEY: …
Idempotency-Key: scan-demo-0001
Content-Type: application/json

{"url": "http://demo-site:8410/apply",
 "scope": {"max_pages": 25, "max_depth": 3, "deny": ["/logout", "/admin"]},
 "auth": {"storage_state_ref": "dps/ui/demo-onboarding"},
 "mode": "synthesize",
 "fill": {"personas": 5, "locale": "en_IN"}}
```

```json
HTTP/1.1 202 Accepted
Cache-Control: no-store

{"scan": {"id": "s_01M4VBNH6RK9CTMWCBK0PK0FSV", "kind": "scan", "state": "queued", "phase": "scan",
          "mode": "synthesize", "kid": "k_424284cf3ec721ac",
          "spec": {"url": "http://demo-site:8410/apply", "url_origin": "http://demo-site:8410",
                   "scope": {"max_pages": 25, "max_depth": 3, "deny": ["/logout", "/admin"], "…": "…"},
                   "auth": {"storage_state_ref": "dps/ui/demo-onboarding", "storage_state": null},
                   "consistency": {"scope": "key", "key_wrapped": false, "caller_key": false,
                                   "caller_seed": false, "…": "…"}, "…": "…"},
          "progress": {"outcome": "", "pages": null, "fields": null, "…": "…"}, "…": "…"},
 "key_material": {"alg": "dps.v1", "kid": "k_424284cf3ec721ac", "dek": "<base64url, shown once>",
                  "kcv": "<key check value>"},
 "synthesis_seed": {"alg": "dps.synth.v1", "seed": "<shown once>", "created_at": "2026-10-13T19:26:12Z"},
 "replay": false}
```

The **key and the seed are in this answer only** — DPS seals them (with the raw start URL and any
inline storage state) into the job row under `DPS_JOB_KEK` for the worker and purges them when the
scan ends. Keep them: the key reveals an obfuscate capture map, the seed reproduces the personas.
The stored spec is value-free: the start URL is **redacted** (path segments a detector recognises
become `<ENTITY>` and value-like ones `{id}`; query values become `<ENTITY>` or `<v>`, keys that are
values themselves `<k>`; userinfo and the fragment are dropped), the
resolved `max_pages`/`max_depth` are recorded, and `consistency` keeps flags only.

## Accept-time and job error codes

Refused at `POST /scans` (nothing is queued; the refusal is audited without the request):

| Code | Status | When |
|---|---|---|
| `url_invalid` | 422 | not an `http(s)` URL, carries userinfo, or does not parse (also a `url` the model refuses: shorter than 8 or longer than 2048 characters, not a string) |
| `host_not_registered` | 422 | the URL's host is not registered for the tenant |
| `scope_invalid` | 422 | `max_pages` outside 1 … `DPS_SCAN_MAX_PAGES`, `max_depth` outside 0–5, a malformed glob, any other refused `scope` field |
| `start_url_denied` | 422 | the start path (or its hash route) matches the deny list, or is outside a non-empty allow list |
| `inline_auth_forbidden` | 422 | inline `auth.storage_state` where inline secrets are not allowed (staging/prod) |
| `auth_invalid` | 422 | both a ref and an inline state, a ref containing `..` or outside the ref pattern, any other refused `auth` field |
| `har_not_supported` | 422 | `capture.har: true` |
| `fill_invalid` | 422 | personas outside 1–50, `display_persona` outside 1 … `personas`, an unsupported locale, any other refused `fill` field |
| `policy_invalid` | 422 | the policy does not resolve |
| `key_material_invalid` | 422 | a supplied `consistency.key_material` or `synthesis_seed` is not valid |
| `wrap_key_invalid` | 422 | `wrap_key` is not `rsa-oaep-256` with a readable RSA public key |
| `idempotency_key_invalid` / `idempotency_key_reused` | 422 | a malformed `Idempotency-Key`; the same key with another body (or a non-scan job) |
| `queue_full` | 429 | the tenant already has `DPS_JOB_TENANT_MAX_QUEUED` (1000) active jobs |
| `kek_unconfigured` | 503 | no `DPS_JOB_KEK`: a scan cannot be sealed |
| `db_unavailable` | 503 | no metadata database |

Every refusal in this table is `{"detail": "the scan was refused: <code>", "code": "<code>"}` and is
audited (`scan.create`, `rejected`). Most `url`/`scope`/`auth`/`fill` rules are checked by the
`ScanSpec` model before `validate_scan` runs; `POST /scans` maps those refusals by the refused
field's location (`url` → `url_invalid`, `scope.*` → `scope_invalid`, `auth.*` → `auth_invalid`,
`fill.*` → `fill_invalid`; with several refused sections, the first in that order), so the caller
gets the same code either way. A refusal with no code stays FastAPI's 422 with `{type, loc, msg}`
only (no input echoed): an unknown key at any level (decision D4, even inside `scope`/`auth`/`fill`),
a refused `mode`, `parallel_view`, `capture`, `consistency`, `policy` or `purpose` field, a body that
is not a JSON object, or a body that mixes one of those with a coded refusal. The plan's `db_unconfigured` is
`db_unavailable` as built (the M2 name), and its `consistency_invalid` is split into
`key_material_invalid` and `wrap_key_invalid`.

Refused by the browser-worker when it opens the scan (the job ends `failed` with this
`error_code`, the key is purged, no page is visited):

| `error_code` | When |
|---|---|
| `secret_unavailable` | the sealed secret cannot be opened, or the storage-state ref does not resolve **for this tenant** (another tenant's ref fails closed; the crawl never runs without the asked-for session) |
| `key_unavailable` | the sealed key material is missing |
| `spec_invalid` | the stored spec no longer validates |
| `policy_invalid` | the policy no longer resolves |
| `host_not_registered` | the host was unregistered after the scan was accepted (checked again at run time and on every request) |

## Job lifecycle of a scan

```
POST /scans ─► queued (phase scan) ─► running ─► succeeded           (phase finalize: evidence, exports, key purged)
                  │                       │   └► failed              (setup error: error_code above, key purged)
                  │                       └─ cancel ─► queued (phase finalize) ─► running ─► canceled
                  └─ cancel (never started) ─► canceled
```

- The browser-worker (`python -m dps.browser_worker`, its own image) claims only `scan` jobs from
  the M2 queue, `DPS_BROWSER_CONCURRENCY` at a time, with M2's leases, heartbeats and reaper. A
  reaped scan is retried from a clean slate (its rows and artefacts are reset first).
- Budgets: `max_pages`, `max_depth`, `DPS_SCAN_MAX_PER_TEMPLATE` visits per URL template
  (`/help/faq/{id}`), `DPS_SCAN_PAGE_TIMEOUT_SECONDS` per page and `DPS_SCAN_JOB_TIMEOUT_SECONDS`
  per scan. A scan that hits the page or time budget still **succeeds**; the evidence records
  `truncated: "pages" | "time"`.
- Cancel of a running scan queues a finalize (`phase: finalize`, outcome `canceled`): the crawl
  stops at its next page, and the canceled scan still gets its evidence pack and exports, and its
  key purged. `409 not_cancelable` once it is finalizing or terminal.
- `progress` carries `outcome`, `pages`, `fields`, `blocked_requests` and `evidence_id`; the live
  crawl counters are in `GET /scans/{id}` → `summary`.

## Reading a scan

`GET /scans/{id}` → `summary` (all counts):

```json
{"pages_by_status": {"done": 21, "skipped": 4}, "pages_visited": 21, "pages_discovered": 21,
 "pages_queued": 0, "pages_classified": 21, "pages_filled": 12, "forms": 14, "fields": 63,
 "personal_fields": 51, "critical_fields": 9,
 "risk_flags": [{"code": "A1", "flag": "autocomplete_enabled_sensitive", "severity": "high",
                 "page_no": 3, "field_nos": [3, 11], "entity_types": ["SSN", "DRIVER_LICENSE"],
                 "query_keys": [], "where": null, "path": "http://demo-site:8410/apply/us"}],
 "blocked_requests": {"GET": 1, "POST": 71}, "non_get_allowed": 0, "navigations": 21,
 "captures": 3, "validation": {"synthesize": {"checked": 61, "accepted": 60, "rejected": 1},
                                "obfuscate": {"checked": 61, "accepted": 57, "rejected": 4}},
 "personas": 5, "display_persona": 1, "mode": "synthesize", "locale": "en_IN",
 "export_artefacts": {"json": "art_…", "csv": "art_…"}, "evidence_id": "ev_…", "elapsed_s": 72.3}
```

`non_get_allowed` is **measured** from the request log (requests with a method other than GET/HEAD
that the guard let through) and must be 0. `path` in a risk flag is the page's redacted URL.

**Pages** (`ScanPage`): `page_no`, `url_redacted`, `title_redacted`, `depth`, `parent_page_no`,
`status` (`queued` `visiting` `done` `redirected` `skipped` `failed`), `status_reason` (for a skip
the scope verdict — `deny`, `not_allowed`, `other_origin`, `unregistered_host`, `scheme` — or
`template` (the per-template budget), `budget`, `auth_redirect`, …),
`discovered_via` (`start` `link` `spa_click` `redirect`), `template` (value-free URL template),
`http_status`, `final_url_redacted`, form/field/personal/critical counts, `forms` (method, redacted
action, counts — a form's `method: "get"` with personal fields raises `sensitive_form_uses_get`),
`artefacts` (below), `rendered_findings` (entity type → count of personal data the page renders),
`risk_flags`, `validation` (per fill mode: fields checked / accepted / rejected by **the page's own
client-side validation**), `capture_status` (`captured` `withheld` `not_needed`), `timings`.

**Fields** (`ScanField`, from `GET /scans/{id}/pages/{page_no}`):

```json
{"page_no": 9, "form_index": 1, "field_no": 3, "selector": "input#aadhaar[name=\"aadhaar\"]",
 "tag": "input", "input_type": "text", "name_attr": "aadhaar", "id_attr": "aadhaar",
 "label": "Aadhaar number", "label_source": "label_for", "autocomplete": null,
 "pattern": "\\d{4} \\d{4} \\d{4}", "maxlength": null, "required": true, "options_count": null,
 "entity_type": "AADHAAR", "sensitivity": "CRITICAL", "confidence": 0.99,
 "detectors": ["label", "name", "pattern"], "evidence": ["label:aadhaar", "name:aadhaar", "pattern:AADHAAR"],
 "placeholder_shape": "XXXX XXXX XXXX", "prefilled": false,
 "persona_attr": "aadhaar", "fill_format": "shape:#### #### ####", "fill_status": "filled",
 "fill_synthesize": "1750 6742 7931", "fill_obfuscate": "5535 0850 7931", "obfuscate_method": "fpe_digits",
 "validation_messages": [], "frame_index": 0, "in_shadow": false,
 "risk_codes": ["autocomplete_enabled_sensitive"]}
```

Classification order: the `autocomplete` token map → the structural dictionary over label, name,
id and placeholder (EN/ES) → input type and pattern → value detectors on a pre-filled value. A
select's options are counted (`options_count`), never stored. `fill_synthesize` is the display
persona's value; `fill_obfuscate` is a **token of that persona value** (never of page data).
`fill_status`: `filled`, `neutral` (not personal: a neutral choice kept), `not_filled`,
`skipped_file`, `skipped_hidden`, `skipped_readonly`, `failed`. Password and OTP inputs are typed
with generated credentials (`persona_attr` `credential` / `otp`), which are never stored or
exported.

**Requests** (`RequestCount`): the guard's decisions counted per page, method, resource type,
decision (`allowed`/`blocked`), reason and host — e.g. `{"method": "GET", "resource_type": "image",
"decision": "blocked", "reason": "host", "host": "tracker.invalid", "count": 1}`. Blocked third-party
host names are recorded (contract open question 17).

**Events** (`ScanEvent`, the navigation log): `seq`, `ts`, `kind` (`navigate` `request` `fill`
`capture` `skip` `spa` `popup` `dialog` `error`), `method`, `result` (an HTTP status, `captured`,
`skipped`, `blocked`, …), `page_no`, `target` (redacted URL or origin), `note_code`, `counts`. At
most `DPS_SCAN_MAX_EVENTS` rows per scan (`events_dropped` in the evidence beyond that). A `spa`
event with result `reached` (note: the scope verdict, e.g. `deny`) records a click whose own handler
ran a route the scope refuses in the same document (an `href="#"` link whose `onclick` sets a hash
route or calls `pushState`): no request existed for the guard to refuse, so the route ran; it is
neither queued nor recorded as a skipped page, that tab is not processed further, and a replayed
route that lands there is `failed` with `scope_reached`.

**Inventory** (`InventoryRow`): `page_no`, `path` (redacted URL), `form_index`, `field_no`, `label`,
`tag`, `input_type`, `name_attr`, `autocomplete`, `pattern`, `maxlength`, `required`,
`entity_type`, `sensitivity`, `confidence`, `detectors`, `risk_codes`, `fill_status`,
`persona_attr`, `obfuscate_method`. No value of any kind.

### Risk flags

| `flag` | Severity | Raised when |
|---|---|---|
| `autocomplete_enabled_sensitive` | high | a visible text-entry field classified HIGH or CRITICAL (not a credential) leaves browser autofill on (`autocomplete` absent or `on`) |
| `password_without_autocomplete_token` | medium | a password field whose `autocomplete` does not end in `current-password` / `new-password` |
| `sensitive_form_uses_get` | high | a form with fields of MEDIUM sensitivity or above has `method="get"` (its values would land in URLs and logs); `where: "form"` |
| `pii_in_query_string` | high | the page's URL (`where: "url"`) or a link to another origin (`where: "link"`) carries personal data in a query value; `query_keys` lists the keys, the value is never stored |
| `pii_rendered_unmasked` | high (a CRITICAL value) or medium | the page renders HIGH/CRITICAL personal data in clear (`where: "table"` or `"text"`; `entity_types` lists what); a masked rendering is not a finding |

They are numbered `A1`, `A2`, … in (page, kind) order in the summary, the inventory and the evidence.

## Artefacts of a scan

Each page's `artefacts` maps a variant to an artefact id (`GET /artefacts/{id}`):

| Variant | Content |
|---|---|
| `original` | the page as loaded, **personal data it renders masked** with boxes drawn before encoding (an unmasked original only with `DPS_SCAN_STORE_UNMASKED_ORIGINALS=true`, local only) |
| `annotated` | `original` with the numbered field overlays (numbers = `field_no`) |
| `filled_synthesize` / `filled_obfuscate` | the parallel view: the page filled with the display persona / with tokens of it, numbered overlays, never submitted |
| `masked_capture` | the obfuscated capture of a page that renders personal data: the DOM text is replaced (tokens for `obfuscate`, persona surrogates for `synthesize`), every remaining copy is boxed, PNG |
| `capture_json` | `obfuscate` mode only: the reversible map of that capture (`dps.capture.v1`, below) |

A capture **fails closed**: when a value cannot be located and masked (text split across nodes, a
frame that could not be read, a page that changed under the capture, the leakage sweep finding an
original), no image is stored for it (`capture_status: "withheld"`, event `capture/withheld` with
the reason). The exports (artefact kinds `export_json`, `export_csv`) and the printable evidence
pack (`evidence_html`) are stored at finalize.

## Export — `dps.scan-export.v1`

`GET /scans/{id}/export?format=json`:

```json
{"format": "dps.scan-export.v1", "scan_id": "s_…", "generated_at": "2026-10-13T19:27:24Z",
 "mode": "synthesize", "locale": "en_IN", "personas": 5, "display_persona": 1,
 "label": "Synthetic personas generated by DPS. Obfuscated values are tokens of these personas, never of page data.",
 "key": {"alg": "dps.v1", "kid": "k_424284cf3ec721ac"}, "seed_fingerprint": "145f3d2c022c4f58",
 "persona_set": [{"persona": 1, "attributes": {"name": "Sathvik Bajaj", "aadhaar": "175067427931",
                  "pan": "FGAPB7231A", "phone": "+91 81239 73320", "email": "sathvik.bajaj8197@example.org", "…": "…"}}],
 "fields": [{"page_no": 9, "path": "http://demo-site:8410/apply/identity", "form_index": 1, "field_no": 3,
             "label": "Aadhaar number", "selector": "input#aadhaar[name=\"aadhaar\"]", "entity_type": "AADHAAR",
             "persona_attr": "aadhaar", "fill_format": "shape:#### #### ####", "obfuscate_method": "fpe_digits",
             "fill_status": "filled",
             "values": [{"persona": 1, "synthesize": "1750 6742 7931", "obfuscate": "5535 0850 7931"}, "…"]}]}
```

The field → entity → value mapping for **every persona**. `?format=csv` has the columns `page_no,
path, form_index, field_no, label, entity_type, persona_attr, persona, synthesize_value,
obfuscate_value, obfuscate_method` (one row per field and persona). Credential fields carry
`"note": "credential fields are filled but never exported"` and no values; `credential` and `otp`
attributes never appear in `persona_set`. The obfuscate column is reproducible: the same key
(`consistency.key_material`) gives the same tokens, the same seed the same personas.

## Capture map — `dps.capture.v1`, and how to reveal it

An `obfuscate` scan stores, beside each `masked_capture`, a `capture_json` artefact:

```json
{"format": "dps.capture.v1", "page_no": 4, "mode": "obfuscate", "kid": "k_…",
 "data": {"t": [null, "<token>", null, "…"], "v": ["<token>", "…"]},
 "reveal_hints": [{"…": "…"}], "methods_used": {"fpe_digits": 3, "fpe_text": 2}}
```

`data.t` holds the tokenised text of each rendered text node that carried personal data (`null` for
untouched nodes), `data.v` the tokenised values of pre-filled inputs. To read the original page
text back, the key holder calls Layer 1's reveal with the key returned by `POST /scans` (unwrapped
first if it was RSA-wrapped):

```http
POST /api/v1/reveal                      (scope reveal)
{"data": <capture_json.data>, "key_material": <the scan's key_material>, "reveal_hints": <capture_json.reveal_hints>}
```

→ `{"result": {"t": [...original texts...], "v": [...]}}`. The map is a token-only document; DPS
holds no key after the scan, so without the caller's key it reveals nothing. A `synthesize` scan has
no map (surrogates are not reversible).

## Evidence — the `scan` block

The finalize writes a `dps.evidence.v1` pack (`job_kind: "scan"`, `source.kind: "ui"` with the
redacted start URL, origin, scope and auth kind; `target.kind: "artefacts"` with the export ids;
`consistency` with the key id, the seed's fingerprint and `key_custody.purged_at`; `reveal_map` per
tokenised field `field:<page>/<form>/<field>`) with a `scan` block:

```json
{"pages": 21, "forms": 14, "fields": 63, "by_entity": {"AADHAAR": 2, "EMAIL": 6, "…": 0},
 "risk_flags": [{"code": "A1", "flag": "autocomplete_enabled_sensitive", "…": "…"}],
 "personas": 5, "display_persona": 1,
 "submissions": 0, "non_get_allowed": 0, "scope_paths_reached": 0,
 "blocked_requests": {"GET": 1, "POST": 71}, "navigations": 21,
 "validation": {"synthesize": {"checked": 61, "accepted": 60, "rejected": 1}, "obfuscate": {"…": 0}},
 "captures": {"captured": 3, "withheld": 0}, "spa_routes": 1, "truncated": null,
 "submit_attempts_neutralised": 4, "websockets_closed": 0, "dialogs_dismissed": 0,
 "popups_closed": 0, "events_dropped": 0, "fills_skipped": 0, "scope_routes_reached": 0}
```

`submissions` is 0 by construction (no code path submits); `non_get_allowed` is its measured
counterpart; `scope_paths_reached` counts HTTPS redirect hops to a denied path that neither the
route nor the proxy could refuse (they are GETs, never processed as pages, and counted rather than
hidden); `scope_routes_reached` counts the `spa`/`reached` events above (refused routes a page's
own click handler ran without a request). The pack's `verification` block for a scan counts the stored token fields
(`values_checked`) and the filled pages (`batches`); it is not a sweep over page data (that sweep
runs per capture, before an image is stored). The printable HTML (`/evidence/{id}/html`) renders
the scan block.

## D4 — DPS never submits anything

There is no submit option in the request model, the API, the worker or the console, and the request
guard enforces it below the page for every request of the scan's isolated browser context:

1. **Methods.** Only `GET` and `HEAD` leave the browser. Every other method — a form `POST`, an
   XHR/fetch autosave, a beacon, `PUT`/`PATCH`/`DELETE`, a CORS preflight — is aborted, whatever
   page issued it. A `GET` asking to be treated as another method (an override header or a
   `_method` query key) counts as that method. There is no switch.
2. **Hosts, on every hop.** Only the scan's allowed hosts are reachable: Chromium sends every
   connection of the context through the in-process **scope proxy**, so redirect hops and worker
   traffic are held to the same hosts; QUIC and non-proxied WebRTC are disabled by launch flags.
3. **Scope paths.** The deny list holds for every request, the allow list for every navigation, on
   every hop the route or the proxy sees.
4. **Filled pages.** From the first fill until the page is closed, every navigation (a GET form
   submission is a navigation) and every script request is aborted; only passive resources load. A
   request on any page whose URL carries a fill value is aborted.
5. **Locked documents.** `sendBeacon`, `fetch(keepalive)`, `WebSocket`, `Worker` stop working on
   every open page once a page is filled; `SharedWorker`, service workers, `RTCPeerConnection`,
   `fetchLater`, `WebSocketStream` and `WebTransport` are unavailable on every page.
6. **Closing.** A page is closed only after its route aborts everything and its unload handlers
   have run against that route, so unload-time traffic cannot leave.
7. **Hardening** (defence in depth): `form.submit()`/`requestSubmit()` are counted no-ops and submit
   events are cancelled; the no-submission proof holds with it switched off.
8. **Fail closed.** A request whose decision cannot be computed is aborted.

The proof is a counting server: the demo site's `GET /__received` counts every non-GET request,
every GET-form submission and every hit on `/logout` and `/admin` it ever receives.
`tests/scanner/test_no_submit.py` (M4 exit criterion) shows the counters unchanged through full
scans with and without hardening and through adversarial submission attempts (beacons, keepalive,
SharedWorker, WebSocket, redirects to foreign hosts, unload traffic), while the guard's log shows
the blocked POSTs; `DPS_SMOKE_SCANNER=1 python tools/smoke_test.py` repeats the check against a
compose stack (`non_get`, `form_get`, `logout_hits`, `admin_hits` +0, `non_get_allowed` 0).

## Personas preview

```http
POST /api/v1/scans/personas/preview      (scope scan; no database needed)
{"locale": "en_IN", "count": 2}
```

```json
{"synthesis_seed": {"alg": "dps.synth.v1", "seed": "<fresh>", "created_at": "…"},
 "personas": [{"persona": 1, "values": {"Name": "Qabil Chaudhari", "Date of birth": "1955-09-11",
                                         "Aadhaar": "145651201576", "PAN": "USEPC9524Z", "Mobile": "+91 81238 07901"}}],
 "notes": ["Aadhaar numbers are Verhoeff-valid and generated, not issued", "PAN values are format-valid",
           "one persona per record: name, gender, e-mail and birth date agree"]}
```

The same generator the worker uses: passing this seed back as `consistency.synthesis_seed` makes
persona 1 of the scan exactly persona 1 of the preview.

## Readiness and metrics

- App `/readyz` → component `browser`: ok with `"N online"` when at least one browser-worker
  heartbeat is recent (refreshed every 15 s; `unknown` right after start). Not required for
  readiness: the other routes keep working without a browser-worker.
- Browser-worker ops server on `DPS_BROWSER_METRICS_PORT` (9401, in-network only): `/health`,
  `/readyz` (Chromium up and the consumer claiming; `browser: chromium <version>`), `/metrics`.
- Metrics (labels never carry a URL or a value): `dps_scan_pages_total{outcome}`,
  `dps_scan_requests_total{decision,method,reason}`, `dps_scan_fields_total{entity_type}`,
  `dps_scan_fills_total{mode,outcome}`, plus M2's `dps_jobs_claimed_total{kind="scan"}`.
