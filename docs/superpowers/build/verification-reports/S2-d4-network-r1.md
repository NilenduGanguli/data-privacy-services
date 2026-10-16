# S2 — branch `m4` after main was merged into it — D4-network lens, round 1

**Verdict: PASS — no blocking finding.** On the merged tree, the scanner sends nothing but GET/HEAD.
Every proof agrees, live and in counting-server tests: no submission and no non-GET reached a target,
no request reached a foreign host, the deny/allow rail held, and no other spelling of an address or
DNS alias got past the host allow-list. The merge did not touch the D4 enforcement code.

| | |
|---|---|
| Date | 2026-10-14, 21:33–22:05 IST |
| Tree | `dps-wt-m4`, branch `m4` @ `a0e8d26`. `git status` was clean at the start and is clean at the end. No repository file was modified. |
| Stack | the gates verifier's running lane stack (`dps-wt-m4`, profiles docs/sources/oracle/scanner/tokenizer). It was not restarted or rebuilt, and nothing was taken down. |
| Probes / outputs | `~/.cache/dps-build/reports/S2-d4-network-r1/` (scripts `p1`, `p4`–`p6`, `common.py`; JSON results; pytest logs and junit) |
| Method | live API scans on the deployed browser-worker, the project's own counting-server proof suites, accept-time host checks, one SSRF allow-list probe with the scanner's real guard, proxy and Chromium, and value-free counters (DB and metrics). Everything is counts, codes, or redacted paths. No value, key or seed was printed. |

## 1. What the merge changed in the D4 path

`git diff bdbdc0a..a0e8d26` (lane verification r3 PASS → the merged head):
- **Unchanged, byte for byte:** `guard.py`, `proxy.py`, `browser.py`, `urls.py`, `dom.py`, `runtime.py`. These hold the method rule, `ISOLATE_JS`/`LOCK_JS`, the scope proxy, the launch flags, the deny/allow rail and the click-target checks.
- `crawler.py`: one change. `_classify` now calls `classify_fields` (main's mixed-PII rules). No navigation, click or guard code changed.
- `browser_worker.py`: one change, `assert_production_posture(settings, role=BROWSER_WORKER)`. That role keeps the scanner rules (headless browser, no unmasked originals).
- Compose: the browser-worker now reads the tenant-scoped `dps-secrets` volume (SEC-2). There is no network change: no `network_mode`, no `extra_hosts`, and `DPS_SCAN_REGISTERED_HOSTS` is unchanged. The host fallback (`localhost,127.0.0.1,demo-site`) applies only when `env in ("local","dev")`, both at accept (`routers/scans.py:137`) and in the worker (`scanner/__init__.py:138`).

## 2. Live scans on the deployed browser-worker: synthesize and obfuscate (P1)

The shared demo site's counters are moved by other verifiers' deliberate positive controls (see N5). So I ran a **private instance** of the same demo app: the same image and files, a second `node server.mjs` in the demo-site container on port 18410, with its own `/__received`. I then scanned it through `POST /api/v1/scans`:
- start `http://demo-site:18410/`, deny `/logout`, `/admin`
- 50 pages, depth 4, 3 personas, `en_IN`, demo session ref
- the instance was stopped afterwards; its final counters were all 0

| | synthesize `s_01M4XK3NR6QHNW8PQE1NQMYDW7` | obfuscate `s_01M4XK3NRC5F2FXQA1NJ7TP27A` |
|---|---|---|
| state / pages / filled pages | succeeded / 21 / 12 | succeeded / 21 / 12 |
| private `/__received` delta (non_get, form_get, logout, admin, by_path) | **0, 0, 0, 0, {}** | (same run window, same instance) |
| request rows (`dps_scan_request`) | allowed GET only: document 54, script 67, stylesheet 54 | identical |
| refused | POST beacon `api_removed` **26**; POST fetch `method` **34**; GET image `host` 1 (`tracker.invalid`) | identical |
| `non_get_allowed` (summary, rows, evidence) / `scope_paths_reached` | **0 / 0** | **0 / 0** |
| evidence `submissions` / `submit_attempts_neutralised` | 0 / 4 | 0 / 4 |
| navigation log | skipped `deny` ×2 (`/logout`, `/admin`), `other_origin` ×1, `template` ×1; spa queued 1 | identical |

The shared demo site's counters did not move during this window either (`shared_moved` all 0).

## 3. Live allow/deny rail (P5)

Scan `s_01M4XKJ7YVNHVPNQ4WW9Y95RT7` (`p5_allow_rail.json`):
- start `/apply` on the private instance, allow `["/apply"]`, deny `["/logout","/admin","/apply/card"]`
- result: succeeded
- pages navigated: `/apply`, `/apply/{address,documents,employment,identity,review,us}`. None was outside the allow list, and `/apply/card` was not visited.
- skipped: `deny` (`/apply/card`, `/logout`) and `not_allowed` (`/`, `/app`, `/help`, `/login`, `/profile`)
- private counters: delta 0; non-GET allowed: 0

## 4. Accept-time host checks (P4)

`POST /scans` received 24 start URLs whose host is not literally registered. Tenant `default` has no registered hosts, so the dev fallback applies. The cases:
- numeric forms of 127.0.0.1: decimal, hex, octal, `127.1`
- `[::1]`, `[::ffff:127.0.0.1]`, `0.0.0.0`, `0`
- trailing-dot hosts
- the demo-site container's IP and its container-name alias
- `app`, `dps-db`, `minio`, `169.254.169.254`
- `localtest.me`, `127.0.0.1.nip.io`, `demo-site%2e`
- userinfo and backslash authority tricks
- `ftp://`, `file://`

Result:
- **17 × 422 `host_not_registered`, 7 × 422 `url_invalid`**
- scan jobs: 19 → 19, so nothing was queued
- 0 answers echoed the URL

## 5. SSRF allow-list with the scanner's real guard, proxy and Chromium (P6)

Setup:
- `new_scan_context` + `ScanGuard`, Chromium launched with `LAUNCH_ARGS`
- the only allowed host is `localhost`
- a raw TCP listener on 127.0.0.1 was referenced only through 9 other spellings: `127.0.0.1`, decimal, hex, octal, `127.1`, `[::ffff:127.0.0.1]`, `localtest.me`, `127.0.0.1.nip.io`, `localhost.`
- each spelling was tried as a first hop (image) and as a 302 redirect hop from the allowed host, over http and https

Result (`p6_ssrf_hosts.json`):
- the listener accepted **0 connections**
- every attempt was refused and counted:
  - route `host` ×10 (Chromium canonicalises the numeric forms to one URL)
  - proxy `unrouted` GET `host` ×9 and CONNECT `host` ×9
- the scope proxy opened connections **only to `localhost`**. Refused names were never resolved by the proxy.
- Positive control: the listener's allowed spelling connected once. This also shows the host rule is per host, not per port (N1).

## 6. Counting-server proof suites on the merged tree (project tests, isolated demo instance)

The demo site was a fixture-started private instance on `127.0.0.1:18420`, not the shared one:
`./lane.sh env DPS_TEST_DEMO_SITE=http://127.0.0.1:18420 DPS_TEST_REQUIRE_SERVICES=1 pytest tests/scanner/{test_guard,test_popup_realms,test_scope_rail,test_no_submit}.py`

Result: **100 passed, 0 skipped, 0 failed** (248 s): test_guard 64, test_popup_realms 7, test_scope_rail 24, test_no_submit 5. Log: `d4-tests-run1.log`; junit: `d4-tests-run1.xml`.

Lens coverage (each test has its positive control):
- **beacons and keepalive fetch:**
  - `test_nothing_but_get_leaves_an_unfilled_page`
  - `test_a_filled_page_can_beacon_keepalive_socket_or_start_workers_nowhere`
  - `test_closing_a_page_through_the_guard_lets_no_unload_request_out`
  - `test_https_nothing_but_get_leaves_where_the_proxy_is_blind`
- **SharedWorker and service workers:**
  - `test_no_shared_or_service_worker_runs_in_a_scan_context`
  - `test_a_service_worker_is_refused_even_by_the_proxy`
  - `test_the_launch_flags_keep_every_connection_on_the_proxy`
- **WebSocket:**
  - `test_a_workers_websocket_never_reaches_the_server`
  - the filled-page socket test above
  - `test_https_fetch_later_and_websocket_stream_are_gone_in_every_realm`
- **popups during arm:**
  - `test_https_popups_opened_while_arm_runs_relay_nothing`
  - `test_https_a_popup_opened_before_the_fill_relays_nothing`
  - `test_a_filled_page_and_its_popups_send_nothing`
  - the 3 full `ScanRunner` scans of the hostile page in `test_popup_realms.py`
- **redirect hops to foreign hosts:**
  - `test_redirect_hops_never_leave_the_scope` (foreign listener 0 connections, http and https)
  - `test_the_scope_proxy_enforces_the_guard_on_every_hop`
- **deny/allow rail, including handler-driven hash and `pushState` routes:**
  - `test_the_deny_list_holds_for_clicks_redirects_and_frames`
  - `test_the_allow_list_holds_for_navigations_not_for_resources`
  - `test_spa_discovery_never_clicks_a_hash_route_the_deny_list_names`
  - `test_the_replay_click_…`
  - `test_a_click_whose_handler_reached_a_denied_route_is_recorded_as_reached`
  - `test_a_replay_click_that_lands_on_a_denied_route_stops_that_tab`
  - `test_an_https_redirect_hop_to_a_denied_path_is_counted_as_reached`
- **nothing submitted:** `test_no_submit.py` (full scans with hardening off and on, adversarial attempts, static check)

**Race reruns:** the 13 popup, socket, worker and redirect proofs (including the M4-verify-r1 F1 race) were run 4 more times. Result: **4 × 13 passed** (`d4-race-reruns.log`).

## 7. Deployed Chromium and stack-wide counters

- The browser-worker's Chromium (container PID 79) runs with:
  - `--disable-shared-workers`
  - `--disable-quic`
  - `--force-webrtc-ip-handling-policy=disable_non_proxied_udp`
  - `--dns-prefetch-disable`
  - `--disable-background-networking`
  - `--headless`
- `dps_scan_request` over **every scan in the lane DB** at the end (18 scans with rows, including other verifiers' and the smoke's):
  - **allowed non-GET 0**
  - `scope_paths_reached` 0
  - blocked 848
- Browser-worker `/metrics` since start: allowed series only `GET ok` (1881). Blocked: POST `method` 370, POST `api_removed` 282, GET `host` 13. There is no allowed non-GET series.

## 8. Blocking findings

None.

## 9. Non-blocking notes

- **N1 — the host rule is per host, not per port.**
  - `CrawlScope.network_host_allowed` compares host names only, so sub-resources may reach any port of a registered host. P6's control shows this; navigations stay same-origin by default.
  - This is the contract's design (registered hosts are hosts). Worth one line in the docs / F1 review.
- **N2 — the local/dev fallback includes `localhost` and `127.0.0.1`.**
  - In local/dev only, a scan-scope key can point a scan at the browser-worker's own loopback (its ops server on 9401).
  - Staging and prod have no fallback, so this is a dev-only exposure. Consider removing the two loopback names from the compose fallback once the tests stop needing them.
- **N3 — registered names are trusted to resolve where the admin intends.**
  - The scope proxy checks host names, not resolved IPs. There is no IP pinning or range check.
  - That is acceptable for curated internal targets. F1 (security review) should decide whether the scanner path needs M5 T17 S5's netguard / TLS-only egress (`egress_tls_required` is not applied to scan targets yet; S2-premerge carried this to F1).
- **N4 — outside D4.** The browser-worker's Chromium runs with `--no-sandbox` (the Playwright default in containers). It is partly mitigated by non-root uid 10001, `cap_drop: ALL` and `no-new-privileges`. Flag for F1, since the scanner loads untrusted pages.
- **N5 — the shared demo-site counters are not a clean D4 baseline while several verifiers run.**
  - During this round they moved +4, all `POST /api/draft` (16 → 20; only that path, an even count). That matches a deliberate unguarded draft-flush positive control run by another verifier.
  - It did not come from a scan: DB-wide allowed non-GET is 0 and there is no allowed non-GET metric series.
  - Lens verifiers should use a private instance (as here) or a fresh baseline with `by_path` attribution.
- **N6 — the documented HTTPS residuals have not changed.** The residual list in `guard.py` (traffic the route and the proxy cannot see over HTTPS) is in a file the merge did not touch. I did not re-probe it this round beyond the project's suites above.
