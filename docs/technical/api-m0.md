# Layer 1 API (M0)

Base path `/api/v1`. The live schema is at `/api/v1/openapi.json` (Swagger UI at `/docs`).

**Headers.** `X-API-KEY` (required unless the server runs with `DPS_AUTH_ENABLED=false`);
`X-DPS-Tenant` (optional; required when the key is granted several tenants; must match
`[A-Za-z0-9][A-Za-z0-9_.-]{0,63}`); `X-Request-ID` (optional; echoed as `trace_id` when it matches
`[A-Za-z0-9][A-Za-z0-9._-]{0,63}`, otherwise a fresh 16-hex id is used).

**Errors** are `{"detail": "..."}` and never quote the request:

| Status | When |
|---|---|
| 400 | bad policy (`invalid policy: …`), bad key material, wrap key, reveal hints or method parameters; a key that does not match its key check value (`kcv`); malformed `X-DPS-Tenant` |
| 401 | missing or invalid API key (`WWW-Authenticate` set) |
| 403 | the key lacks the route's scope, is not granted the tenant, or needs `X-DPS-Tenant` |
| 404 | unknown `/api/...` route |
| 413 | body over `DPS_MAX_PAYLOAD_MB` (by `Content-Length` or while streaming), text over `DPS_MAX_TEXT_CHARS`, document over `DPS_MAX_ITEMS` leaf values |
| 422 | invalid body (`detail` is a list of `{type, loc, msg}`), not exactly one of `data`/`text`, JSON nested deeper than 100, or the leakage sweep failed under a `strict` policy (`output failed the leakage sweep; nothing was returned`) |
| 429 | rate limit (`Retry-After` set) |
| 500 | `internal error` (only the exception class is logged) |

## Endpoints

| Method | Path | Scope | Purpose |
|---|---|---|---|
| POST | `/detect` | detect | findings + record sensitivity, no transformation |
| POST | `/obfuscate` | obfuscate | transform per policy; returns `key_material` and `reveal_hints` |
| POST | `/synthesize` | synthesize | surrogates; `synthesis_seed` returned unless the consistency scope is `request` |
| POST | `/reveal` | reveal | undo an obfuscation with the caller's key material (+ hints) |
| GET | `/policies/presets` | detect | `[{name, description}]` for the six presets |
| POST | `/policies/validate` | detect | resolve a preset or inline policy, return the effective policy (400 naming the broken rule) |
| POST | `/policies/plan` | detect | the plan for a sample without transforming (`mode`: `obfuscate` \| `synthesize`) |

Probes outside the base path, no key: `GET /health`, `GET /readyz`, `GET /metrics` (see `HANDOFF.md` §5).

Presets: `gdpr-pseudonymise` (the default), `dpdp-pseudonymise`, `pci-dss`, `analytics-safe`,
`synthetic-realistic`, `synthetic-strict`.

## Request body (detect / obfuscate / synthesize / policies/plan)

```json
{
  "data": { "customer": "any JSON document" },
  "text": "or a text: exactly one of data / text",
  "policy": "gdpr-pseudonymise",
  "options": {
    "locale": "en_IN",
    "token_style": "natural",
    "wrap_key": { "type": "rsa-oaep-256", "public_key_pem": "-----BEGIN PUBLIC KEY-----..." },
    "consistency": { "scope": "key", "key_material": { "...": "from an earlier response" },
                     "synthesis_seed": { "...": "from an earlier response" } },
    "llm_rewrite": false
  },
  "mode": "obfuscate"
}
```

- `policy` is a preset name, a full inline policy (`{"name": ..., "treat": {...}, ...}`), or a
  preset with overrides: `{"preset": "gdpr-pseudonymise", "treat": {"EMAIL": {"method": "redact"}}}`.
  An override replaces a whole `treat` / `synthesize.overrides` entry (it never inherits the
  preset's params); the effective name becomes `gdpr-pseudonymise+overrides`. Omitted: the
  server's `DPS_DEFAULT_POLICY`.
- `options` overrides the policy for this request: `locale`; `token_style` (`natural` or
  `envelope`); `wrap_key` (RSA ≥ 2048, OAEP-SHA256: the response then carries `wrapped_dek`
  instead of `dek`); `consistency.scope` (`request` | `key` | `entity`) with caller-held
  `key_material` (obfuscate: the same key gives the same tokens) or `synthesis_seed` (synthesize:
  the same seed gives the same surrogates); `llm_rewrite` (text synthesis only, and only when the
  server's `DPS_LLM_TEXT_REWRITE` is on).
- `mode` is read by `/policies/plan` only.

Methods a policy can name: reversible `det_token`, `fpe_digits`, `fpe_text`, `email`, `phone`,
`date_shift`, `external_tokenize` (no adapter in M0: a 400); irreversible `redact`, `mask_partial`,
`hmac_pseudonym`, `generalize`, `bucket`, `noise`, `null`, `dictionary`, `surrogate`, `keep`.

## Responses

**`/detect`** → `findings[]`, `record_sensitivity` (`LOW` < `MEDIUM` < `HIGH` < `CRITICAL`), `report`,
`trace_id`. A finding:

```json
{ "locator": { "path": "$.email", "start": null, "end": null }, "entity_type": "EMAIL", "score": 1.0,
  "detectors": ["pattern:email", "structural"], "sensitivity": "MEDIUM",
  "evidence": ["pattern:email", "key:email"], "value_hash": "71d4f55f72fa128d" }
```

`start`/`end` are set for a span inside a string. `value_hash` is a salt-free 16-hex hash for
"same value as" links; it does not hide low-entropy values, so treat it like the finding itself.

**`/policies/plan`** → `findings[]`, `plan[]`, `record_sensitivity`, `trace_id`. A plan row:
`{path, span, entity_type, score, method, params, reason, reversible}`; `span` is `[start, end]` or
`null`; `reason` is `policy:<TYPE>`, `policy:default_sensitive`, `policy:synthesize_default`,
`policy:keep_non_sensitive`, with `|fallback:fpe_disabled` appended when FF1 is off.

**`/obfuscate`, `/synthesize`** → `result` (same shape as the input), `findings[]`, `plan[]`,
`key_material` (obfuscate only), `reveal_hints[]` (obfuscate only), `synthesis_seed` (synthesize
only), `report`, `trace_id`.

- `key_material`: `{alg: "dps.v1", kid, dek | wrapped_dek + wrap: {type, fingerprint}, kcv,
  consistency_scope, created_at}`. Returned once; DPS keeps no copy. `kcv` is the key check
  value: 96 bits of HKDF output of the DEK under the kid (`kcv_` + base64url). It is not secret
  and reveals nothing about the DEK; it lets the holder (and reveal) confirm a DEK is the right
  one. It is kept when the DEK is wrapped, so an unwrapped DEK can be checked.
- `reveal_hints[]`: `{path, start, end, entity_type, method, params, reverse_params,
  original_type, anchor, kid, kcv, field_scoped}` — locations and methods, never values.
- `synthesis_seed`: `{alg: "dps.synth.v1", seed, created_at}` — confidential (it links outputs).

**`report`** (every engine response): `mode`, `policy`, `policy_hash`, `engine_version`,
`findings_total`, `findings_by_type`, `findings_by_detector`, `record_sensitivity`,
`methods_used` (what actually ran, per value), `fallbacks` (one per value that did not get its
policy method: `fallback:fpe_disabled`, `fallback:fpe_unavailable`,
`fallback:fpe_domain_too_small`, `llm:<reason>`), `reversible_values`,
`verification` (`{passed, status, checked, hits: [{path, entity_type}], coverage}`, `null` for
detect and reveal), `llm` (`{calls, raw_pii_sent: 0}`, plus `used` and `fallback` for synthesize),
`duration_ms`. Under a `warn` policy a failed sweep returns the output with
`verification.passed: false`.

The leakage sweep looks for the originals of the values the detectors **found**; a value no
detector found is never checked. `verification.coverage` says what the sweep could see:
`{basis: "detected-values", detectors: ["structural", "patterns", "name_cues", "ner"?, "custom"?],
ner: "on" | "off" | "unavailable", free_text_fields, complete, gaps: [...]}`. Free text is a text
payload, or a JSON string of two or more words that was not replaced whole. With NER off (the
compose image) or failed to load, names in free text are found only next to a cue — a title
(`Mr`, `Smt`, `Sra.`…), a lead-in (`name is`, `Dear`, `s/o`, `account holder`…), contact details
right after the name, or `<name>'s Aadhaar` — so free text is a gap. `status` is derived:
`failed` (an original was found; `passed: false`), `partial` (none found, `gaps` not empty) or
`passed` (none found, no gaps). A `strict` policy fails the request on `failed` only.

## Reveal

```json
{ "data": { "...": "the obfuscated document" }, "key_material": { "...": "as returned, with dek" },
  "reveal_hints": [ "...as returned" ] }
```

→ `{result, revealed, report, trace_id}`; `revealed` is the number of hints applied.

- `key_material` must carry the plaintext `dek`: unwrap a `wrapped_dek` client-side first.
  When it also carries `kcv`, the `kcv` must match the `dek` and `kid`, or the request is refused
  (400 `invalid reveal request: key check value does not match …`). The same check applies to
  caller-held key material in `options.consistency` on `/obfuscate`.
- Every hint must carry `kid` and `kcv`. Hints made under another `kid`, or whose `kcv` the
  presented key does not match, are refused with a value-free 400 **before anything is
  decrypted**: FF1 and the shape-keeping methods have no integrity check, so a mistyped DEK would
  otherwise decrypt to plausible but false identifiers.
- In `envelope` style, `det_token` replacements inside text spans are written
  `[[PII:<TYPE>:<token>]]` and reveal **without** hints. Format-keeping replacements (`fpe_*`,
  `email`, `phone`, `date_shift`) and whole-field tokens need the hints.
- Irreversible methods (`redact`, `surrogate`, …) have no hint and stay as they are.

## curl

```bash
export DPS_API_KEY=dps_local_dev_key_change_me      # the compose stack's local demo key
curl -s localhost:8400/api/v1/obfuscate -H "X-API-KEY: $DPS_API_KEY" -H 'Content-Type: application/json' \
  -d '{"data":{"ssn":"123-45-6789","email":"ann@example.com"},"policy":"gdpr-pseudonymise"}' > obf.json
jq '{result, methods: .report.methods_used, fallbacks: .report.fallbacks, verification: .report.verification.status}' obf.json
jq '{data: .result, key_material, reveal_hints}' obf.json \
  | curl -s localhost:8400/api/v1/reveal -H "X-API-KEY: $DPS_API_KEY" -H 'Content-Type: application/json' -d @- \
  | jq '{result, revealed}'
```

The SSN comes back as a `det_token` with `fallback:fpe_domain_too_small`: `gdpr-pseudonymise`
keeps its last 4 digits, which leaves FF1 a domain of 10⁵ (under the 10⁶ minimum).
