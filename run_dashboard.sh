#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
DASHBOARD_PYTHON=python3
if [ -x ".venv/bin/python" ]; then
  DASHBOARD_PYTHON=.venv/bin/python
fi
"$DASHBOARD_PYTHON" bank_statement_dashboard.py --statement-dir "." --history-file "bank_transactions.csv" --categories-file "merchant_categories.json" --reconciliation-file "statement_reconciliation.json" --import-log-file "statement_import_log.json" --output-html "index.html"
