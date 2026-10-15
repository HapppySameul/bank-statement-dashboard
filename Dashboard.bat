@echo off
rem Bank statement dashboard for Windows: import new statements from data\ and
rem rebuild data\index.html. Statements and the ledger in data\ are git-ignored.
setlocal
cd /d "%~dp0"
if not exist data mkdir data
set "DASHBOARD_PYTHON=python"
if exist ".venv\Scripts\python.exe" set "DASHBOARD_PYTHON=.venv\Scripts\python.exe"
"%DASHBOARD_PYTHON%" src\bank_statement_dashboard.py --statement-dir "data" --history-file "data\bank_transactions.csv" --categories-file "data\merchant_categories.json" --reconciliation-file "data\statement_reconciliation.json" --import-log-file "data\statement_import_log.json" --output-html "data\index.html"
pause
