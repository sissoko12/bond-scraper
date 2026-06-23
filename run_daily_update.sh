#!/bin/bash
# Wrapper for the daily BondSupermart price refresh, invoked from cron.
# cron runs with a minimal environment, so we set everything explicitly.

set -euo pipefail

REPO="/Users/netanelnevo/Desktop/finance project]/bondsupermart-scraper"
PYTHON="/opt/anaconda3/bin/python3"

cd "$REPO"
export PYTHONPATH="$REPO"

# Append a timestamped run to the log (kept out of git via .gitignore *.log).
echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') : daily_price_update start =====" \
    >> "$REPO/daily_update.log"
"$PYTHON" daily_price_update.py >> "$REPO/daily_update.log" 2>&1
echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') : daily_price_update done =====" \
    >> "$REPO/daily_update.log"
