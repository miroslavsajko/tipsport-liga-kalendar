#!/usr/bin/env bash
#
# Scrape the calendars and push them to GitHub. Must run from a
# residential connection — see the README.
#
# Usage: scripts/run_local.sh [--no-push]

set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# A partial scrape still publishes: fresh calendars for the teams that
# worked beat holding all twelve back because one failed. The scraper's
# exit code is carried to the end of this script.
set +e
"${PYTHON:-python3}" scripts/update_calendar.py
RC=$?
set -e

[[ "${1:-}" == "--no-push" ]] && exit "$RC"

if [[ -z "$(git status --porcelain -- docs)" ]]; then
    echo "no calendar changes, nothing to push"
    exit "$RC"
fi

git pull --rebase --autostash origin main
git add -- docs
git commit -m "Update team calendars ($(date '+%Y-%m-%d'))"
git push origin HEAD:main
exit "$RC"
