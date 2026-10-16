# S1 — main after the M4 merge — D4-network lens, round 1

**Verdict: PASS — no blocking finding in the D4 lens.** On `main` @ `8ea220a`, the deployed scanner sends
nothing but GET/HEAD:
- No submission and no non-GET request reached a target.
- No request reached a foreign host.
- The deny and allow rail held on every navigation the scanner caused, including a handler-driven
  `pushState` route.
- No other spelling of a host and no DNS alias got past the host allow-list.

Every proof agrees: live scans on the deployed browser-worker, counting servers, the project's proof
suites, the deployed image, Chromium's NetLog, and stack-wide counters. The merge did not touch the D4
enforcement code.

**An operational incident I caused must be fixed by the owner or the orchestrator (§9).** My first
race-rerun script was broken. It ran the default pytest suite against main's stack for about
11 minutes, and this left main's **Oracle and MongoDB demo sources at test size**. I could not
reload them because the permission classifier refused it. It is not a product defect, and it does
not affect any proof here.

| | |
|---|---|
| Date | 2026-10-15, 21:06–21:50 IST |
| Tree | `/Users/neelu/document_intelligence/data-privacy-services`, `main` @ **`8ea220a`**. Clean at the start and at the end. HEAD is unchanged, with no stash. During the run my broken script rewrote 54 tracked PNGs; I restored them (§9). |
| Stack | The gates verifier's running stack (profiles docs, sources, oracle, scanner, tokenizer). I did not restart, rebuild or `down` it. Only my own extra node process was started in `demo-site`, on port 18441, and it was stopped at the end. |
| Probes / outputs | `~/.cache/dps-build/reports/S1-M4-d4-network-r1/`: scripts `p0`–`p8`, `common.py`, `race_reruns.sh`, JSON results, pytest logs and junit, the NetLog. Everything is counts, codes or redacted paths. No value, key or seed was printed. |
| Python | main's `.venv` (Python 3.12.13, playwright 1.55.0, host Chromium 1187), endpoints from `~/.cache/dps-build/s1-m4-gates-r1/env.sh` (contract §12.2) |

## 1. What changed in the D4 path since the last D4 verification

The last D4 verification was S2 d4-network r1, PASS, on `m4` @ `a0e8d26`. The range to `8ea220a` is
`a3cd667`, `cbbac38`, the merge `5959a75`, `932075a`, `40d8b34` and `8ea220a`.

**Unchanged, byte for byte** (`git diff a0e8d26 HEAD`, and no commit in the range touches them):
- the enforcement code: `guard.py`, `proxy.py`, `browser.py`, `urls.py`, `runtime.py`, `store.py`,
  `inventory.py`, `dps/browser_worker.py` and `dps/netguard.py`;
- `docker-compose.yml`, `Dockerfile`, `Dockerfile.browser` and `demo/`;
- the D4 proof suites: `test_guard`, `test_popup_realms`, `test_scope_rail`, `test_no_submit`,
  `test_demo_site`, `tests/scanner/conftest.py` and `tools/smoke_test.py`.

**Changed:**
- **Host fallback.** `routers/scans.py` and `scanner/__init__.py` now read
  `Settings.scan_fallback_hosts`. The rule is the same: it applies only when `env` is `local` or
  `dev`, and is `[]` otherwise (`config.py:554-557`). It is the same rule at accept time and in the
  worker. `posture.py` only reports the list.
- **`crawler.py`.** One capture-needed rule for a select that draws its placeholder, and selects stay
  masked in the filled screens. No navigation, click or guard code changed.
- **`dom.py`, `capture.py`, `classify.py` and `models.py`** (the S2 fixes, B1a–c among them).
  - What the code reads is new: `selectedOptions`, the computed `appearance` and the text of
    `button` or `selectedcontent`. The census now descends into a customizable select's button.
  - Masks are placed with CDP `Page.getLayoutMetrics` (`viewport_box`, read-only), and
    `scroll-snap-type: none` was added to the no-motion style.
  - A grep of all three files finds no `dispatchEvent`, `click`, `focus`, `showPicker`,
    `requestSubmit` or `submit`, no `location` assignment, `window.open`, `fetch`, `sendBeacon` or
    `XMLHttpRequest`, and no `scrollIntoView` or popover call.
  - The obfuscated capture still arms the guard before it rewrites the DOM.

**Containers run this code** (p0):
- sha256 of the 16 D4-path files in `browser-worker` and the 17 files in `app` (`dps/layers/scanner/*`,
  `browser_worker.py`, `config.py`, `routers/scans.py`) are identical to the tree.
- The deployed Chromium runs with `--disable-shared-workers`, `--disable-quic`,
  `--force-webrtc-ip-handling-policy=disable_non_proxied_udp`, `--dns-prefetch-disable`,
  `--disable-background-networking` and `--headless`.
- `browser-worker` publishes no port, uses `cap_drop: ALL`, `no-new-privileges` and a read-only
  root filesystem.

## 2. Live scans of the demo site, synthesize and obfuscate (P1)

Four API scans ran on the deployed browser-worker with the same settings: 50 pages, depth 4,
3 personas, `en_IN`, the demo session ref, and deny `/logout` and `/admin`.
- **Private instance:** the same demo app in the `demo-site` container on port 18441. Its own
  `/__received` counter is untouched by anyone else.
- **Shared instance:** `demo-site:8410`, with a fresh baseline and attribution by `by_path`.

| | private synth `s_01M503V2Z5EZ…` | private obf `s_01M503V2ZBEG…` | shared synth `s_01M503V2ZH1Y…` | shared obf `s_01M503V2ZQNT…` |
|---|---|---|---|---|
| state / pages / filled / captures | succeeded / 21 / 12 / 3 | same | same | same |
| `/__received` delta (non_get, form_get, logout, admin) | **0/0/0/0**, by_path `{}` | (same window) | **0/0/0/0**, by_path unchanged (16/4/2/2 before and after) | (same window) |
| `dps_scan_request` | allowed: GET document 54, script 67, stylesheet 54 | same | same | same |
| refused | POST beacon `api_removed` 26; POST fetch `method` 34; GET image `host` 1 (`tracker.invalid`) | same | same | same |
| allowed non-GET / allowed with a scope reason | **0 / 0** | 0 / 0 | 0 / 0 | 0 / 0 |
| evidence: `submissions` / `submit_attempts_neutralised` / `non_get_allowed` / `scope_paths_reached` / `scope_routes_reached` | **0** / 4 / **0** / **0** / 0 | same | same | same |
| navigation log | skipped `deny` ×2, `other_origin` ×1, `template` ×1; SPA click queued and navigated 1 | same | same | same |

The demo site's own scripts exercise the D4 channels: an autosave `fetch` POST, `pagehide` and
`visibilitychange` beacons, a `keepalive` POST to `/api/draft`, a telemetry beacon, `requestSubmit` on
change, and a tracker pixel on a foreign host. Every one was refused and counted; none reached a
counter. The four scans took 181 s together.

## 3. Deny and allow rail, live

**P5, allow list** (`s_01M5041TG5Y472X5EZCQ7QFJRM`): start `/apply` on the private instance, allow
`["/apply"]`, deny `["/logout","/admin","/apply/card"]`.
- The scan succeeded.
- Navigated: `/apply` and `/apply/{address,documents,employment,identity,review,us}`. Nothing
  outside the allow list was navigated, and `/apply/card` was never navigated.
- Skipped `deny`: `/apply/card` and `/logout`. Skipped `not_allowed`: `/`, `/app`, `/help`, `/login`
  and `/profile`.
- Counters +0; allowed non-GET 0.

**P8, deny rail on handler-driven SPA routes** (`s_01M5045V4Z9Y1VKM450581B9YQ`): start `/app`, deny
`["/logout","/admin","/app/preferences","/app/nominee"]`. The demo's `spa.js` has two `pushState`
routes:
- `<a href="/app/nominee" data-spa>`: its target is checked before any click. It was **never
  clicked**: the link was skipped `deny`, and no page was processed under it.
- `<span role="link" data-route="/app/preferences">` (no href): its handler's `pushState` route ran
  without a request. It was recorded as **`spa`/`reached`, note `deny`**, and the evidence shows
  `scope_routes_reached` 1. The tab was not processed further: there is no page row and no
  `navigate` event for it.
- Under the denied paths: 0 navigated pages and 0 allowed rows with a scope reason. Counters +0;
  submissions 0.

## 4. Host checks at accept time (P3)

`POST /scans` received 24 start URLs whose host is not literally registered. Tenant `default` has no
registered hosts, so the dev fallback `localhost,127.0.0.1,demo-site` applies. The cases:
- decimal, hex and octal forms of 127.0.0.1, and `127.1`;
- `[::1]`, `[::ffff:127.0.0.1]`, `0.0.0.0` and `0`;
- trailing-dot names;
- the demo-site container's IP (`172.25.0.5`) and its name `data-privacy-services-demo-site-1`;
- `app`, `dps-db`, `minio` and `169.254.169.254`;
- `localtest.me`, `127.0.0.1.nip.io` and `demo-site%2e`;
- userinfo and backslash authority tricks;
- `ftp://` and `file://`.

Result:
- **17 × 422 `host_not_registered` and 7 × 422 `url_invalid`.**
- 0 answers echoed the URL.
- Scan jobs went from 14 to 14: nothing was queued.

## 5. SSRF through numeric hosts or DNS, at runtime (P6, P6c, P6n)

**The probe.** It uses the scanner's own `new_scan_context`, `ScanGuard` and scope proxy, with
Chromium launched with `LAUNCH_ARGS`. The only allowed host is `localhost`. A raw TCP listener on
127.0.0.1 is referenced only through 9 other spellings: `127.0.0.1`, decimal, hex, octal, `127.1`,
`[::ffff:127.0.0.1]`, `localtest.me`, `127.0.0.1.nip.io` and `localhost.`. Each spelling is tried as
a first hop and as a 302 redirect hop, over http and https.

**Results:**
- **P6 (host, main's code):**
  - The listener accepted **0 connections**.
  - The scope proxy connected only to `localhost`.
  - Refusals: route `host` ×10, proxy `unrouted` GET `host` ×9, CONNECT `host` ×9.
  - Positive control: the allowed spelling connected once. The host rule is per host, not per
    port (N1).
- **P6c (inside the deployed image `data-privacy-services-browser:local`:** `--network none`,
  uid 10001, Linux headless shell 1187). The results are identical: listener 0, control 1, proxy
  connected only to `localhost`, the same refusal counts.
- **P6n (the same probe with Chromium's NetLog):**
  - Every TCP connection Chromium opened (39 attempts) went to the scope proxy (`127.0.0.1:<proxy
    port>`), and nowhere else.
  - The only resolutions that could reach the system resolver were of the proxy's own IP literal:
    HTTP-proxy and SSL connect jobs.
  - The Network Quality Estimator made local-only lookups (cache and hosts file) of each request's
    host. These failed with `ERR_NAME_NOT_RESOLVED` for `localtest.me` and `127.0.0.1.nip.io`,
    although the Mac's system DNS resolves both (`dscacheutil`). So **no DNS query for a refused name
    left Chromium**, and the proxy never resolves a refused name (P6).

Summary: `p6n_netlog_summary.json`.

## 6. The project's counting-server proof suites on main

The suites ran against a private host demo instance (`DPS_TEST_DEMO_SITE=http://127.0.0.1:18451`,
started by the fixture), with `DPS_TEST_REQUIRE_SERVICES=1`:
`tests/scanner/{test_guard,test_popup_realms,test_scope_rail,test_no_submit}.py`

**Result: 100 passed, 0 skipped, 0 failed** (306 s): test_guard 64, test_popup_realms 7,
test_scope_rail 24, test_no_submit 5 (`d4-tests-run1.{log,xml}`, `d4-tests-run1-cases.json`).

Each channel has its unguarded positive control:
- **beacons and keepalive:** `…can_beacon_keepalive_socket_or_start_workers_nowhere`,
  `…closing_a_page_through_the_guard_lets_no_unload_request_out[False|True]`;
- **SharedWorker and service workers:** `test_no_shared_or_service_worker_runs_in_a_scan_context[False|True]`,
  `test_a_service_worker_is_refused_even_by_the_proxy`;
- **WebSocket:** `test_a_workers_websocket_never_reaches_the_server`, `…make_no_script_requests_at_all[websocket]`,
  `test_https_fetch_later_and_websocket_stream_are_gone_in_every_realm[unfilled|filled]`;
- **popups during arm:** `test_https_popups_opened_while_arm_runs_relay_nothing[False-0|True-0|True-1|True-2]`,
  `test_https_a_popup_opened_before_the_fill_relays_nothing[unguarded|guarded]`,
  `test_a_filled_page_and_its_popups_send_nothing`, the 3 hostile `ScanRunner` scans in
  `test_popup_realms`, `test_an_inert_popup_document_loads_nothing_even_after_document_open[unguarded|guarded]`,
  `test_positive_control_unguarded_popup_realms_deliver_every_hostile_request`;
- **redirect hops to foreign hosts:** `test_redirect_hops_never_leave_the_scope`,
  `test_the_proxy_refuses_and_counts_scope_paths_on_redirect_hops`,
  `test_an_https_redirect_hop_to_a_denied_path_is_counted_as_reached`;
- **deny and allow rail, including hash and `pushState` routes:** test_scope_rail's 24 tests
  (`…never_clicks_a_hash_route_the_deny_list_names`, `…handler_reached_a_denied_route_is_recorded_as_reached`,
  `…deny_list_holds_for_clicks_redirects_and_frames`, …);
- **no submission:** `test_no_submit` (full scans with hardening off and on, adversarial attempts,
  static check).

**Race reruns:** the 27 race-prone proofs (popup realms, sockets, workers, redirect hops, unload) were
run 3 more times. Result: **3 × 27 passed** (77–79 s each; `d4-race-reruns.log`, `d4-race-{1,2,3}.xml`).
The gates verifier also passed them twice: in the whole suite and in the browser gate.

## 7. Stack-wide counters and logs (P7)

**`dps_scan_request` over every scan in main's DB** at the end of my window: 23 scans with rows,
mine and other verifiers' scans of every tenant. Jobs: 23 succeeded, 7 failed, 1 canceled. The
failures are all `secret_unavailable`, another verifier's SEC-5 negative tests.
- **allowed non-GET 0**
- **allowed rows with a scope reason (`scope_paths_reached`) 0**
- blocked 996

**browser-worker `/metrics` since start:**
- The only allowed series is `GET ok` (2436).
- Blocked: POST `method` 488, POST `api_removed` 370, GET `host` 16.
- There is no allowed non-GET series.

**browser-worker logs since 15:44Z** (468 lines): all at info level (audit 231, scan_page 216,
scan_finalized 15, scan_setup_failed 6, from the same SEC-5 tests). There is no warning, error,
`guard_error`, `guard_lock` failure or page left open.

## 8. The S2 r3 findings, in this lens

- **Captures-privacy B1a–c (`40d8b34`).** The fix reads what a closed select draws. Its new reads
  dispatch no events, make no clicks, open no picker and send no request (§1). Live scans of demo
  pages with selects (P1, P8) kept non-GET at 0.
- **Masking and the console B1 wording (`932075a`).** These are outside this lens. The gates verifier
  verified both live, and its report says PASS.

## 9. Incident: my broken rerun script ran the general suite against main (not a product defect)

**What happened**
- **21:25.** My first `race_reruns.sh` used `mapfile`, which macOS bash 3.2 lacks. It therefore passed
  pytest **no test ids**, and pytest ran the default `testpaths` (the whole suite) from the main
  checkout against main's stack.
  - The demo site was redirected to a private host instance on 18451, so the shared `/__received`
    counters never moved (16/4/2/2 before and after).
- **21:36.** I stopped it with SIGTERM to the script and pytest, and killed the orphaned fixture demo
  server.
  - The log kept only the last progress line, because of the script's `| tail -1`.
  - From what it touched, the run went at least through `tests/api`, `tests/connectors` (including
    the Oracle and MongoDB tests) and `tests/console`.

**Effects and what I did**
1. **54 tracked `docs/superpowers/build/m3-shots/*.png` were rewritten** (21:31:44–21:32:18, A-N2).
   - The S1-M4 console, captures-privacy and regression lenses reported this as "another process":
     it was this run.
   - I copied them to `m3-shots-from-accidental-run/` and **restored them to HEAD** by explicit
     pathspec (`git checkout -- docs/superpowers/build/m3-shots`). `git status` is clean and HEAD is
     `8ea220a`.
2. **Session database `dps_test_99575` was left in dps-db.** I **dropped** it with the fixture's own
   statement (`DROP DATABASE IF EXISTS … WITH (FORCE)`). Only the two older leftovers remain
   (gates N8).
3. **The M3 fixtures reloaded main's demo sources at test size.**
   - Now: Oracle `CRM.CUSTOMERS` 200, `LOANS.APPLICANTS` 50; Mongo `crm.profiles` 200.
   - PG `bank.customers` is still 20,000.
   - The regression lens saw this (its N5 b) and redid its affected check.
   - **NOT restored.** The permission classifier refused my run of the documented reload as a
     shared-resource change. **Owner or orchestrator, please run:**
     ```bash
     cd /Users/neelu/document_intelligence/data-privacy-services
     source ~/.cache/dps-build/s1-m4-gates-r1/env.sh
     .venv/bin/python tools/seed_sources.py --target all --rows 20000 --seed 7
     ```
     This is gates r1's "seed 2" command. Model 2 reloads without `--reset`.
4. **Unknown.** I could not tell whether the killed run's later tests created jobs or targets on the
   live stack before 21:36. After the reseed refusal, the classifier also refused a read of main's
   job table. Source-pg holds no scratch schema from the run: `t_7e1f3db0` dates from 10:52 IST.

**Impact on this lens:** none. Every D4 proof here reads the demo site or the probes' own servers,
never the bank sources. The fixed script reads its ids without `mapfile` and refuses to run unless it
has all 27. It then ran only the 27 ids, 3 times.

**Lesson for the orchestration:** a script that builds pytest arguments must refuse an empty list,
and macOS ships bash 3.2.

## 10. Non-blocking notes

- **N1 (carried): the host rule is per host, not per port.** Sub-resources may reach any port of a
  registered host (P6's control shows it). This is by design.
- **N2 (carried): the local/dev fallback includes `localhost` and `127.0.0.1`.** In dev, a demo page
  can reach the browser-worker's own loopback, for example its ops port. On main the rule is
  unchanged and only for `local`/`dev` (`scan_fallback_hosts` is `[]` otherwise).
- **N3 (carried): registered names are trusted.** There is no IP pinning, and the admin decides what
  a name resolves to. P6n adds evidence: Chromium makes no DNS query of its own, and only the proxy
  resolves, only allowed names.
- **N4 (carried, outside D4).** The browser-worker's Chromium runs with `--no-sandbox`, mitigated by
  uid 10001, `cap_drop: ALL` and `no-new-privileges`. This is for F1.
- **N5 (carried, accepted residual, not re-probed).** Encoded persona values in passive URLs
  (base64), and the `Cookie` and `Referer` headers (S2 captures-privacy r3 N1). They are an owner
  decision on D4; the values are synthetic and only sent to in-scope hosts with GET.
- **N6 (documentation).** HANDOFF §17 ("Verified 2026-10-15 on main") names
  `~/.cache/dps-build/reports/S1-merge-m4.md` as its report, but that file still says IN PROGRESS.
  The full gate record is `S1-M4-gates-r1.md`.

## 11. Resume check, 22:03-22:06 IST (this attempt)

An earlier attempt of this job wrote §0-§10 above and ended before returning its verdict. This
attempt re-checked the state instead of redoing the probes:
- HEAD is still `8ea220a`. The tree is clean, with no stash. No listener is left on 18441 or 18451.
- main's stack is still up: 11 services healthy, the same containers ("Up 2 hours"), not restarted.
- **P7 re-read** (`p7b_counters_resume.py`, writes `p7b_counters_resume.json`; read-only SELECTs and
  `/metrics`): identical to §7. 23 scans with rows, allowed non-GET **0**, allowed scope reason **0**,
  blocked 996. The metrics show only `allowed|GET|ok`. No scan has run since §7.
- The shared demo `/__received` is still 16/4/2/2, as in §2.
- **The §9 incident is still open.** Mongo `crm.profiles` still holds 200 documents, so main's demo
  sources have not been reseeded. The reseed command in §9.3 is still needed (owner or
  orchestrator). It is not a D4 defect.

Verdict unchanged: **PASS, no blocking finding in the D4-network lens.**
