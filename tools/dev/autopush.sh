#!/usr/bin/env bash
# Push every local DPS branch and every tag to origin, after checking the new commits:
#   - author must be nilendu.ganguli@gmail.com and no commit may carry a Co-Authored-By/Signed-off-by trailer;
#   - gitleaks (Docker image zricethezav/gitleaks) must find no secret in the new commits (.gitleaksignore honoured);
#   - only fast-forward pushes (a branch that diverged from origin is skipped, never force-pushed);
#   - a tag is pushed only when its commit is already on a pushed branch.
# Run by the LaunchAgent ~/Library/LaunchAgents/com.nilendu.dps-autopush.plist every 10 minutes (see
# docs/superpowers/RESUME.md §1). Log: ~/.cache/dps-build/autopush.log. Safe to run by hand at any time.
set -u
REPO=${DPS_REPO:-/Users/neelu/document_intelligence/data-privacy-services}
STATE=$HOME/.cache/dps-build
LOG=$STATE/autopush.log
LOCK=$STATE/autopush.lock
OWNER_EMAIL=nilendu.ganguli@gmail.com
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
mkdir -p "$STATE"
exec >>"$LOG" 2>&1
ts() { date '+%Y-%m-%d %H:%M:%S'; }
mkdir "$LOCK" 2>/dev/null || { echo "$(ts) skip: another run holds $LOCK"; exit 0; }
trap 'rmdir "$LOCK"' EXIT
cd "$REPO" || { echo "$(ts) error: no repo at $REPO"; exit 1; }
git fetch -q --prune origin || { echo "$(ts) skip: fetch failed (offline?)"; exit 0; }
if ! docker info >/dev/null 2>&1; then echo "$(ts) skip: Docker is down, cannot run the secret scan"; exit 0; fi

pushed=0; held=0
for b in $(git for-each-ref --format='%(refname:short)' refs/heads/); do
  local_sha=$(git rev-parse "$b")
  if git rev-parse -q --verify "refs/remotes/origin/$b" >/dev/null; then
    remote_sha=$(git rev-parse "refs/remotes/origin/$b")
    [ "$local_sha" = "$remote_sha" ] && continue
    if ! git merge-base --is-ancestor "$remote_sha" "$local_sha"; then
      echo "$(ts) HOLD $b: diverged from origin/$b (never force-pushed)"; held=$((held+1)); continue
    fi
    range="$remote_sha..$local_sha"
  else
    range="origin/main..$local_sha"
  fi
  if git log --format='%ae' "$range" | grep -qv "^$OWNER_EMAIL\$"; then
    echo "$(ts) HOLD $b: a commit in $range is not authored by $OWNER_EMAIL"; held=$((held+1)); continue
  fi
  if git log --format='%B' "$range" | grep -qiE '^(co-authored-by|signed-off-by):'; then
    echo "$(ts) HOLD $b: a commit in $range carries a trailer"; held=$((held+1)); continue
  fi
  if ! docker run --rm -v "$REPO":/repo:ro zricethezav/gitleaks:latest git /repo \
        --log-opts="$range" --redact --no-banner --exit-code 3 >/dev/null 2>&1; then
    rc=$?
    echo "$(ts) HOLD $b: gitleaks exit $rc on $range (3 = possible secret; run it by hand with --verbose)"; held=$((held+1)); continue
  fi
  if git push -q origin "refs/heads/$b:refs/heads/$b"; then
    echo "$(ts) pushed $b $(git rev-parse --short "$local_sha")"; pushed=$((pushed+1))
  else
    echo "$(ts) HOLD $b: push failed"; held=$((held+1))
  fi
done

git fetch -q origin
for t in $(git tag); do
  git ls-remote --exit-code --tags origin "refs/tags/$t" >/dev/null 2>&1 && continue
  c=$(git rev-list -n1 "$t")
  if [ -n "$(git branch -r --contains "$c" 2>/dev/null)" ]; then
    git push -q origin "refs/tags/$t" && echo "$(ts) pushed tag $t"
  else
    echo "$(ts) HOLD tag $t: its commit is not on a pushed branch yet"
  fi
done
[ $pushed -gt 0 ] || [ $held -gt 0 ] && echo "$(ts) done: pushed=$pushed held=$held"
exit 0
