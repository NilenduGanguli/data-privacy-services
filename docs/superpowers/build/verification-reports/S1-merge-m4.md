# S1 — merge `m4` into main (in progress)

**Status: IN PROGRESS** (2026-10-15 09:50 IST). This file is rewritten at the end.

Done so far:
- `5959a75` `merge: M4 into main` (one textual conflict, contract §8.1, both sides kept).
- `932075a` `fix(integrate): a stopped run is worded by its batch …` (S2 console r3 B1 + the duplicate
  `EvidenceOut` import both sides added).
- Scanner fix for S2 captures-privacy r3 B1a/B1b/B1c written test-first (uncommitted at this point:
  `dps/layers/scanner/{classify,capture,crawler,dom,models,personas_fill}.py`,
  `tests/scanner/test_capture_select_drawn.py`, docs).
- Work dir: `~/.cache/dps-build/s1-merge-m4/` (logs, gate chain `chain.sh`, env `env.sh`, probes).

Next: commit the scanner fix, rebuild images, `up -d` with five profiles, gate chain, live console probe,
log scan, HANDOFF/RESUME, autopush, final report.
