# The ten-minute demo: presenter script

> **Status: draft, finish stage S6a (2026-10-14).** Written against the console and API as built on `main`
> plus the M4 scanner branch. Finish stage S6b runs the real rehearsal (`tools/demo_seed.py`,
> `tools/e2e_demo.py --console`), confirms every label and timing below against the running stack, and
> rebuilds the deck from that run. This script never quotes a figure that was not measured: where a beat
> says "read it off the screen", the number comes from the live stack or from the deck slide built from
> the rehearsal.

**Audience:** bank management: the CIO office, data protection, platform and data owners.
**Promise of the ten minutes:** one Privacy Engine, three entry points, two outcomes, and every claim
backed by a measurement on screen.
**What runs:** the local compose stack (`--profile full`) with synthetic seed data only. No real customer
data appears anywhere in the demo, on screen or in the deck.
**What is local in the room:** OCR is the local mock of the Azure Read v3.2 contract (Tesseract inside), the
LLM gateway is the stub, the bank tokenization service is the vaultless mock. In the bank the same settings
point at the org gateways (Stellar, Azure through the org gateway) and the bank's tokenizer.
**Deck:** `docs/demo/dps-showcase.pptx`, built by `python tools/build_deck.py` from the rehearsal's own
report and screenshots and the bench results, so every number on a slide was measured. Its speaker notes
are the **Slide N** blocks of this script.

**Slides and beats:** slides 1 to 4 open (optional, before the clock starts), slides 5 and 6 go with the
Playground, 7 to 9 with Data sources, 10 with the UI scanner, 11 with Reports and Admin, 12 closes. Policies
has no slide; it is shown live only.

## Timeline at a glance

| Clock | Beat | Console page (sidebar) | Slides | Proof on screen |
|---|---|---|---|---|
| T−30 min | Before the room | — | — | dress rehearsal ends PASS, under 10:00 |
| before 0:00 | Opening (optional, at most 90 s) | — | 1–4 | — |
| 0:00 | Overview (30 s) | Overview | — | tile "Raw PII sent to LLM" reads 0 |
| 0:30 | Playground (2:30) | Playground | 5–6 | findings with evidence; reveal identical; redacted PDF |
| 3:00 | Policies (1:00) | Policies | — | v1 to v2 diff with its change ticket |
| 4:00 | Data sources (3:00) | Data sources & jobs | 7–9 | partitions; evidence pack; three-way join; synthetic pack |
| 7:00 | UI scanner (2:00) | UI scanner | 10 | "No submissions · POST interception on"; POSTs received 0 |
| 9:00 | Reports and Admin (1:00) | Reports & audit, Admin | 11 | evidence PDF; audit trail; posture checks with the ones enforced at boot marked; rotated key refused |
| 10:00 | Close, then Q&A | — | 12 | — |

## Before the room (T−30 min)

Run everything from the repository root, inside the project's virtual environment.

```bash
docker compose --profile full up --build -d
# poll readiness; never `docker compose --wait` (the stack has one-shot services)
until curl -fsS http://127.0.0.1:8400/readyz >/dev/null; do sleep 5; done
python tools/demo_seed.py                  # idempotent: tenant, keys, connections, policies, job group
python tools/e2e_demo.py --console         # dress rehearsal: every step PASS, total under 10:00
python tools/build_deck.py --strict        # the deck from this rehearsal; fails if any input is missing
```

- Oracle Free needs about three minutes on a first start. If it is slow, rehearse with
  `python tools/e2e_demo.py --console --skip-oracle` and present the PostgreSQL and MongoDB pair in the join.
  Such a rehearsal cannot rebuild the deck with `--strict` (slide 8 needs all three pairs): keep the deck
  built from the last full rehearsal.
- Open the console at `http://127.0.0.1:8400`. In the top bar paste the `demo-console` key from
  `var/demo/demo-keys.json` (copy it to the clipboard without printing it, for example with `pbcopy`; the
  key never appears on the projector), set Tenant to `retail-banking-apac`, leave "Remember key on this
  device" unticked, and press **Light theme** (projectors wash out the dark theme).
- Close every other tab and notification. Keep `docs/demo/dps-showcase.pptx` open in Keynote on a second
  screen: slides 5 to 11 are the fallback for any beat that misbehaves live.
- Glance at `reports/e2e-demo-report.md`: the job, scan and evidence ids to open live are listed there.

## Opening (before the clock starts, optional, at most 90 seconds)

Present slides 1 to 4, then switch to the console.

#### Slide 1 · Data Privacy Services

> "Data Privacy Services: one Privacy Engine, three entry points, two outcomes, and privacy we measure
> rather than claim."
> "Everything you will see runs on a local stack with synthetic seed data. The numbers on these slides were
> measured, by the rehearsal named in the footer and, for throughput, by the bench runs, not taken from a
> brochure."

#### Slide 2 · Customer data keeps reaching places it should not

> "Test, UAT, analytics, vendors and now LLM pipelines all want realistic data, and too often they get
> production data."
> "DPS gives them a protected copy through the door the data already uses: an API call, a database job or a
> web front end."
> "Two outcomes: obfuscated data the owner can reverse with their own key, or synthetic data with no path
> back to a real person."

#### Slide 3 · Three promises, built to be proved

> "Three promises, each with a mechanism and a measurement."
> "No LLM ever sees a raw value: the engine works locally, the gateway receives typed placeholders only, and
> a counter per tenant proves zero."
> "The caller holds the key: DPS returns it and keeps nothing, so nobody can reverse a token without it,
> including us."
> "Evidence, not claims: every job leaves an evidence pack you can hand to an auditor."

#### Slide 4 · One engine, four roles, one metadata database

> "One container image runs four roles: the app serves the API and this console, the worker runs database
> jobs, the browser worker runs scans, and migrate owns the schema."
> "The metadata database holds jobs, hashed API keys, evidence and the audit log, never a customer value."
> "The only line to an LLM is the dashed one: placeholders only. The whole thing deploys to OpenShift with
> kustomize."

## 0:00 · Overview (30 seconds)

**Show:** sidebar **Overview** (page "Protection posture"): the KPI tiles, the **Posture score** card, the
layer cards. Point at the tile **Raw PII sent to LLM**.

**Say:**
> "One engine, three entry points, two outcomes. This tile is the promise: raw PII sent to an LLM, zero.
> Measured, not claimed."

**Proof:** the tile reads 0 for this tenant (the rehearsal measured the same through `/reports/summary`).

## 0:30 · Playground (2 minutes 30 seconds)

**Show, step by step:**
1. Sidebar **Playground** ("Try the engine"), input **JSON**: the sample KYC record of a fictional customer
   with checksum-valid identifiers is already there. Policy: `gdpr-pseudonymise` (or `retail-kyc` when the
   selector lists it).
2. **Detect only**: the findings pane lights up; hover an entity chip to show the detector and the score.
3. **Obfuscate**: the output keeps formats (the Aadhaar number keeps its last four digits, the e-mail keeps
   its domain). Point at the card **Key material · returned once, never stored**.
4. **Reveal with key**: the original comes back; read the callout "Original restored … DPS kept no copy."
5. **Synthesize**: a coherent fake person, marked "synthetic · no path back".
6. Input **Document**: **Load demo KYC scan**, OCR **always**, mode **Redact**: findings per page, then
   **Download redacted PDF** and open it.

**Say:**
> "Every finding says which detector found it, with what score, and which rule decided its treatment."
> "The bank keeps this key. DPS stored nothing; reveal works only with the key the caller hands back."
> "True redaction: the text is gone, not hidden behind a black box."

**Proof:** reveal identical to the input; the redacted PDF has no text to select.

#### Slide 5 · The Playground: findings, tokens and the key

> "A KYC record goes in; every finding names its detector, its score and the rule that decided its
> treatment."
> "Obfuscation keeps formats, so downstream systems keep working: an identifier still looks like that
> identifier, an e-mail keeps its domain."
> "The key card belongs to the bank: DPS stored nothing, and reveal with that key gave back exactly the
> input."
> "The counter of raw values sent to an LLM read zero for this call."

#### Slide 6 · Documents: true redaction, not a box over text

> "A scanned KYC page: OCR reads it, the engine finds the sensitive values, and the pages are rebuilt as
> images with those values burned out."
> "Nothing survives in a text layer, so nothing hidden can be copied out; an OCR re-read of every page checks
> that no original is still readable."
> "The text output still comes back with the key, so the owner can reveal it; the redacted PDF is
> irreversible by design."

## 3:00 · Policies (1 minute, live only)

**Show:** sidebar **Policies**, open `retail-kyc`: the per-entity treatment matrix; the custom recognizer
`employee-id` with **Live test on the engine** (type a staff id into the test text); **Version history**:
the diff from v1 to v2 with its change ticket.

**Say:**
> "Policies are the control surface: presets for PCI, GDPR, DPDP and analytics, our own recognizers, and
> immutable versions."
> "Every response and every evidence pack names the policy hash it ran with."

**Proof:** v2 carries the change ticket it was published under.

## 4:00 · Data sources (3 minutes)

**Show, step by step:**
1. Sidebar **Data sources & jobs**, **New job**: connect to the saved Oracle connection `crm-oracle`, pick the
   customers table, **Run discovery**.
2. The **Column heat-map**: review every column, accept one override, **Accept snapshot**.
3. Target **Copy to a table** in the UAT schema, job group `crm-uat-refresh`, run. On the job page watch
   **Partitions** commit, then open the **Evidence pack** (label "pseudonymised — still personal data").
   If the worker is slow, open the rehearsal's finished job from the report instead of waiting.
4. On the job page, **Cross-system joins** then **Open consistency check**: the same group key across the
   Oracle, MongoDB and PostgreSQL copies.
5. Open the synthetic companion's evidence pack, section **Synthetic fidelity and privacy**.

**Say:**
> "Discover, review, run, evidence: nobody writes a masking script by hand."
> "Same key, same tokens, three systems: the joins still work, and there is no lookup table to steal."
> "Synthetic data, measured, not claimed anonymous: fidelity, exact-row matches zero, nearest-neighbour
> distance above its target."

**Proof:** every partition committed and the leakage sweep passed; the consistency check matches every pair;
the synthetic pack shows exact-row matches 0.

#### Slide 7 · Databases: masked copies at scale, with evidence

> "Layer 2 works on whole schemas: discover the columns, review the heat-map, run in partitions with
> checkpoints, then a verification sweep and a sealed evidence pack."
> "These are this rehearsal's jobs on Oracle, MongoDB and PostgreSQL, and the throughput we measured with
> the bench tool on one machine; production scales out with more workers."

#### Slide 8 · One key, three systems, the same tokens

> "A job group gives Oracle, MongoDB and PostgreSQL one key, so the same customer id becomes the same token
> in every system."
> "The consistency check samples customer ids and joins the masked copies: every pair matched, with no lookup
> table anywhere."

#### Slide 9 · Synthetic data: measured, not claimed anonymous

> "For synthetic copies the copula engine is fitted, previewed and measured before a single row is written."
> "Fidelity says the shape survived; exact-row matches must be zero, and a real row is rejected in flight."
> "The nearest-neighbour distance ratio and the membership-inference test say how far the synthetic rows sit
> from real people; utility says a model trained on synthetic data still works on real data."
> "That is why the label reads: synthetic, measured, not claimed anonymous."

## 7:00 · UI scanner (2 minutes)

**Show, step by step:**
1. Sidebar **UI scanner** (new scan): Start URL `http://demo-site:8410/apply` (the onboarding form of the
   demo site, reached from inside the stack), fill mode **Synthesize persona fill**, three personas,
   **Start crawl**.
2. The live crawl map, then the scan page: **Pages with forms** and the annotated form with its field chips.
3. **Parallel view**, **Filled with: Synthetic persona**, then flip to **Obfuscated tokens**.
4. **Export inventory** (CSV).
5. Point at the chip **No submissions · POST interception on**.

**Say:**
> "The scanner shows what each screen collects, and renders every screen filled with safe data beside the
> original."
> "There is no submit button in DPS, and the browser blocks every POST."

**Proof:** the demo site's own counter of POST requests received stays at 0 (slide 10 shows the rehearsal's).

#### Slide 10 · UI scanner: every screen filled, nothing submitted

> "The scanner walked the onboarding journey, classified every field and filled each screen with a synthetic
> persona and, flipped, with obfuscated tokens."
> "The demo site counts every POST it receives: zero. DPS has no submit path, and the browser blocks every
> POST from a filled page."

## 9:00 · Reports and Admin (1 minute)

**Show:** sidebar **Reports & audit** ("Evidence and audit"): **Evidence packs**, open one, **Print** to
show the evidence PDF; the **Audit log** filtered by the job id. Then **Admin**: **Posture checks**, the keys
table with **Rotate**.

**Say:**
> "Every job and every scan leaves an evidence pack and a PDF an auditor can keep; the audit log says who ran
> what, and when, without a single value in it."
> "These posture checks are evaluated as if this were production. On this local stack they are only
> reported, so the production-only ones fail here by design, for example the bootstrap admin key the
> seed uses and the stub LLM.
> Staging and production refuse to boot when a check marked enforced at boot fails."

**Proof:** the posture list marks the checks enforced at boot and says this stack only reports them (the
local stack fails the production-only checks by design; read the count off the screen or slide 11); a
rotated key is refused.

#### Slide 11 · Evidence for every job, posture at every boot

> "Evidence packs name the policy hash, the methods, the verification and the measurements, and print as a
> PDF."
> "In staging and production the service will not start when a posture check enforced at boot fails;
> this local stack evaluates the same checks as if it were production and only reports them, so the
> production-only ones fail here by design."
> "The rehearsal also proved that a rotated key stops working and that retention runs."
> "And the counter that matters most: raw PII sent to an LLM, zero, for this tenant."

## 10:00 · Close

#### Slide 12 · Decisions taken, and what comes next

> "The owner took the four decisions that shape the platform: where it lives, who holds the key, how jobs
> write, and that the scanner never submits."
> "The rest stand at the design defaults, and one is still open: the scale targets, which we set together
> next."
> "Next steps: scale targets, the FF1 licence review, the bank's tokenization service, and single sign-on for
> the console."

## Q&A crib

| Question | Answer |
|---|---|
| Format-preserving encryption and licensing? | AES-SIV deterministic tokens by default; FF1 (`fastfpe`) only behind a licence review and the `DPS_FPE_ENABLED` switch (D8); the bank's own tokenizer plugs in through `external_tokenize`. |
| Who holds the keys? | The caller (D2). The data key is returned in the response, optionally wrapped with the caller's public key; DPS stores no key; job keys are sealed for the job's life and purged with it. |
| Is pseudonymised data anonymous? | No. Under GDPR and India's DPDP Act pseudonymised data is still personal data; the evidence label says so. Synthetic packs are labelled "measured, not claimed anonymous". |
| What exactly reaches an LLM? | Only typed placeholders or field metadata. A verifier checks every outbound call and blocks a raw value; the per-tenant counter must read zero, and an alert fires otherwise. |
| Data residency? | DPS makes no cross-region calls; the LLM gateway, OCR gateway and databases are in-region endpoints configured per deployment. |
| How does it run in the bank? | OpenShift through kustomize: one image, four roles (app, worker, browser-worker, migrate), an external metadata PostgreSQL, secrets from mounted files. |

## If something fails

| Symptom | Recovery |
|---|---|
| A job stays `waiting` | `docker compose restart worker`; meanwhile open the rehearsal's finished job (ids in `reports/e2e-demo-report.md`). |
| Oracle is not ready | Rehearse and present with `--skip-oracle`: the PostgreSQL and MongoDB pair in the join; slide 8 still shows the last full rehearsal's three-way join (keep that deck; a `--skip-oracle` run cannot rebuild it with `--strict`). |
| The scan times out | Re-run with one persona; or show slide 10, the rehearsal's scan. |
| The evidence PDF renders slowly | Use the HTML evidence page and **Print** from the browser. |
| The console shows 401 or an empty page | Re-paste the `demo-console` key and check the Tenant field reads `retail-banking-apac`. |
| The stack is down | Switch to the deck: slides 5 to 11 carry the rehearsal's measured results. |

## Reset between rehearsals

- `python tools/demo_seed.py --reset-jobs` sets the tenant's retention to one day, runs retention
  (`POST /admin/retention/run`) so the demo jobs go, and sets retention back to thirty days.
- For a clean slate: `docker compose --profile full down -v`, then the "Before the room" commands again.
