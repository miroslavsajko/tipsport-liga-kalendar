#!/usr/bin/env bash
#
# Scrape the calendars and push them to GitHub, from a machine on a
# residential connection. Cloudflare challenges datacenter IPs, so hosted
# runners cannot do this — see README.
#
# Run it by hand, or on a schedule of your own (cron / systemd timer /
# launchd / Task Scheduler). Safe to run when nothing has changed: it
# commits only when a calendar actually differs.
#
# Usage: scripts/run_local.sh [--no-push]

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

PUSH=1
[[ "${1:-}" == "--no-push" ]] && PUSH=0

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

# Prefer a local venv if one exists, so cron does not need a global install.
if [[ -x "$REPO_DIR/.venv/bin/python" ]]; then
    PYTHON="$REPO_DIR/.venv/bin/python"
else
    PYTHON="${PYTHON:-python3}"
fi

BRANCH="${GIT_BRANCH:-main}"

log "scraping with $PYTHON"
# A partial scrape still publishes: fresh calendars for the teams that
# worked beat holding all twelve back because one failed. The scraper's
# exit code is carried to the end of this script.
set +e
"$PYTHON" scripts/update_calendar.py
SCRAPE_RC=$?
set -e
if [[ "$SCRAPE_RC" -ne 0 ]]; then
    log "scrape reported issues (exit $SCRAPE_RC) — publishing what succeeded"
else
    log "scrape finished"
fi

if [[ "$PUSH" -eq 0 ]]; then
    log "--no-push given, leaving changes in the working tree"
    exit "$SCRAPE_RC"
fi

if [[ -z "$(git status --porcelain -- docs)" ]]; then
    log "no calendar changes, nothing to push"
    exit "$SCRAPE_RC"
fi

# Rebase onto the remote first: this machine is not the only writer.
log "pulling $BRANCH"
git pull --rebase --autostash origin "$BRANCH"

git add -- docs
git commit -m "Update team calendars ($(date '+%Y-%m-%d'))"
log "pushing to origin/$BRANCH"
git push origin "HEAD:$BRANCH"
log "done"
exit "$SCRAPE_RC"
