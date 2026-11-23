#!/usr/bin/env bash
# Local alternative to the GitHub Actions schedule. Use ONE of the two, not both,
# so there is a single monitoring database. Example crontab (09:00 local daily):
#   0 9 * * * /path/to/SpikerzAssessment/scripts/daily_check.sh >> /path/to/SpikerzAssessment/reports/cron.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then set -a; source .env; set +a; fi
python3 -m spikerz_qa check   # records today only, then rebuilds every report
