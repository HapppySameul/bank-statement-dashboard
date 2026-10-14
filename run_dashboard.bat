@echo off
setlocal
cd /d "%~dp0"
set "DASHBOARD_PYTHON=python"
if exist ".venv\Scripts\python.exe" set "DASHBOARD_PYTHON=.venv\Scripts\python.exe"
"%DASHBOARD_PYTHON%" bank_statement_dashboard.py --statement-dir "." --history-file "bank_transactions.csv" --categories-file "merchant_categories.json" --reconciliation-file "statement_reconciliation.json" --import-log-file "statement_import_log.json" --output-html "index.html"
pause
