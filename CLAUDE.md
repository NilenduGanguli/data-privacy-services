# Data Privacy Services — agent guide

Development is mid-programme (target `v1.0.0`). **Before doing anything, read `docs/superpowers/RESUME.md`** and run
`tools/dev/resume_status.sh` — they say where the work stands, what is left, how to test it and how to continue.

Rules that always apply here:
- Commits are authored by `NilenduGanguli <nilendu.ganguli@gmail.com>` with **no `Co-Authored-By` or other trailer**;
  conventional commits; commit only your own paths with an explicit pathspec.
- Never push, publish or tag a release without the owner's explicit request.
- In lane worktrees (`../dps-wt-*`) run every Python tool through `./lane.sh`; run `docker compose` from the worktree.
- Never print or log customer values, key material or secrets; never weaken a validator or verification to pass a test.
