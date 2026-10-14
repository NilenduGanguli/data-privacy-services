#!/usr/bin/env bash
# Where does DPS development stand? Read-only: prints branches, worktrees, per-lane task ledger,
# uncommitted work, latest review/verification verdicts, Docker stacks and running agents.
# Usage: tools/dev/resume_status.sh            (see docs/superpowers/RESUME.md §3)
set -u
ROOT=${DPS_ROOT:-/Users/neelu/document_intelligence}
MAIN=$ROOT/data-privacy-services
REPORTS=${DPS_REPORTS:-$HOME/.cache/dps-build/reports}
hr() { printf '\n==== %s\n' "$*"; }

hr "worktrees"; git -C "$MAIN" worktree list
hr "tags (oldest first)"; git -C "$MAIN" tag --sort=creatordate | tr '\n' ' '; echo
hr "main: last commits / uncommitted / merge in progress?"
git -C "$MAIN" log --oneline -10
git -C "$MAIN" status --short | head -20
[ -f "$MAIN/.git/MERGE_HEAD" ] && echo "!! a merge is in progress on main"
echo "origin/main: $(git -C "$MAIN" rev-parse --short origin/main 2>/dev/null) ($(git -C "$MAIN" rev-list --count origin/main..main 2>/dev/null) local commits not pushed)"

for b in m3 m4 m4ui m5 m5ui fsec fdeploy; do
  git -C "$MAIN" rev-parse -q --verify "refs/heads/$b" >/dev/null || continue
  merged=no; git -C "$MAIN" merge-base --is-ancestor "$b" main && merged=yes
  hr "branch $b  (merged into main: $merged; $(git -C "$MAIN" rev-list --count main.."$b") commits not on main)"
  git -C "$MAIN" log --oneline -6 "$b"
  wt="$ROOT/dps-wt-$b"
  if [ -d "$wt" ]; then
    case "$b" in m3) n=M3-DEVIATIONS.md;; m4) n=M4-DEVIATIONS.md;; m5|fsec|fdeploy) n=M5-DEVIATIONS.md;;
      m4ui) n=M4-DEVIATIONS-console.md;; m5ui) n=M5-DEVIATIONS-console.md;; *) n=;; esac
    log=""; [ -n "$n" ] && [ -f "$wt/docs/superpowers/build/$n" ] && log="$wt/docs/superpowers/build/$n"
    [ -n "$log" ] && { echo "  task ledger ($(basename "$log")):"; grep -E '^## ' "$log" | sed 's/^## /    /' | cut -c1-110 | tail -14; }
    u=$(git -C "$wt" status --short | wc -l | tr -d ' ')
    echo "  uncommitted files in $wt: $u"; [ "$u" != 0 ] && git -C "$wt" status --short | head -15 | sed 's/^/    /'
  fi
done

hr "latest review / verification verdicts ($REPORTS)"
ls -t "$REPORTS"/*.md 2>/dev/null | head -24 | while read -r f; do
  printf '  %-34s %s\n' "$(basename "$f")" "$(grep -m1 -oiE 'verdict[^a-z]*(pass|fail)' "$f" 2>/dev/null)"
done

hr "docker compose projects"
docker compose ls -a 2>/dev/null || echo "  docker not reachable (start Docker Desktop: open -a Docker)"

hr "agent processes"
echo "  claude processes: $(pgrep -fl 'claude' 2>/dev/null | grep -c 'output-format') (stream-json sessions; workflow subagents run inside them)"
echo "  most recently modified files in lane worktrees (activity check):"
find "$ROOT"/dps-wt-* -path '*/node_modules' -prune -o -path '*/.venv' -prune -o -type f -newermt '-10 minutes' -print 2>/dev/null | head -8 | sed 's/^/    /'
