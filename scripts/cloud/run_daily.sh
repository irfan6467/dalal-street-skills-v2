#!/usr/bin/env bash
# Daily after-close job for Claude's cloud. Run from anywhere; commits reports/ and journal/ and pushes.
# Public NSE end-of-day data only: no Shoonya, no orders, no secrets.
set -euo pipefail
cd "$(dirname "$0")/../.."
python3 scripts/cloud/daily_eod.py "$@"
git add reports journal
if git diff --cached --quiet; then
  echo "nothing new to commit"
  exit 0
fi
day=$(ls reports | sort | tail -1 | sed 's/\.md$//')
git -c user.name=Claude -c user.email=noreply@anthropic.com commit -q -m "Daily NSE report and shadow journal for ${day}"
git push -q origin HEAD
echo "pushed reports/${day}.md"
