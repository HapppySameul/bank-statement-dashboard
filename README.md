# Bank Statement Dashboard

This project parses bank statement PDFs from the supported HSBC and ING layouts, reconciles every statement period using the totals available in each statement, categorises transactions with auditable rules, and generates an offline interactive dashboard.

## Folder layout

```
Dashboard.command      macOS/Linux launcher: update, publish, or edit categories
Dashboard.bat          Windows launcher: import statements and rebuild the dashboard
data/                  your statements, ledger and dashboard (git-ignored, created on first run)
src/                   the parser, the local editing server and the dashboard page
deploy/                Cloudflare Worker config and security headers
tests/                 tests that run on generated sample statements
```

## Your data stays on your computer

Everything personal lives in `data/`: statement PDFs, the transaction ledger, category rules, the statement password and the generated `index.html`. The whole folder is git-ignored, so the repository only ever holds code, and the Tests workflow fails if any of those files is committed.

To view the dashboard online, publish it to Cloudflare straight from your computer (see [Publishing to Cloudflare](#publishing-to-cloudflare)); nothing is built from GitHub.

## One-time setup

The launchers need Python plus the packages in `requirements.txt`. On macOS or Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

On Windows:

```bat
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

The launchers use `.venv` when it exists and fall back to the system Python otherwise.

## Run

On macOS, double-click `Dashboard.command` in Finder and pick an option:

1. **Update** imports new statements from `data/`, publishes the dashboard to Cloudflare, and opens it in your browser with editable categories.
2. **Publish** publishes the current dashboard on its own, for example after editing categories.
3. **Edit** opens the current dashboard with editable categories without importing anything.

Keep the Terminal window open while editing categories; changes are saved automatically to `data/bank_transactions.csv` and `data/merchant_categories.json`. Opening `data/index.html` directly in Finder uses the browser's `file://` mode, which cannot write changes back.

Every run first syncs the code with GitHub, so this Mac always has the latest version. If you changed any code locally, the launcher lists the changes and offers to commit and push them; it then pulls anything new from GitHub (for example changes merged from a pull request) and pushes local commits. `data/` is git-ignored and never synced, and the launcher refuses to commit PDFs, CSVs or `index.html` even if they appear elsewhere. If GitHub cannot be reached, it says so and carries on with the local copy.

From a terminal (macOS or Linux), run the same steps by name:

```sh
./Dashboard.command update    # import, publish, then edit
./Dashboard.command build     # import and rebuild data/index.html only
./Dashboard.command publish
./Dashboard.command edit
./Dashboard.command sync      # only sync the code with GitHub
```

On Windows, double-click `Dashboard.bat`. It pulls the latest code from GitHub first (it never commits or pushes). It imports new statements and rebuilds `data\index.html`; open that file in a browser to view it.

Each import updates:

- `data/bank_transactions.csv` - validated ledger; new transactions are appended, and dashboard category edits update the relevant row;
- `data/statement_reconciliation.json` - reconciliation results for validated statements;
- `data/statement_import_log.json` - fingerprints of PDFs already handled; and
- `data/index.html` - the offline dashboard generated from the ledger.

The source PDF is never modified.

## Adding the next month's statement

1. Download the complete statement PDF from HSBC or ING.
2. Put it in `data/`; there is no need to unlock or re-save it.
3. Run **Update** (`Dashboard.command`) or double-click `Dashboard.bat`.
4. Check the final messages in the window. A successful one-month import reports `Imported 1 new PDF(s)`, the appended transaction count, and `Validated and reconciled 1 new statement(s)`.
5. After a successful import, archive the PDF somewhere safe or leave it in `data/`. If left there, future runs recognise and skip it.

The launcher treats `bank_transactions.csv` as the validated transaction ledger. Existing rows are not parsed, recategorised, reconciled, sorted, or rewritten. Only a previously unseen PDF is parsed and reconciled; after it passes, its transactions are appended to the end of the CSV and `index.html` is refreshed from the ledger.

`statement_import_log.json` records a SHA-256 fingerprint for every imported PDF. Renaming an imported PDF does not make it new, and running the launcher again does not re-audit it. Every top-level `*.pdf` in `data/` is considered as a possible input, but only unrecognised file fingerprints are parsed. File names do not control the statement dates; names such as `hsbc_YYYY-MM.pdf` are recommended only because they sort clearly.

If you start from a multi-month `combined.pdf` together with an existing ledger, keep it in `data/`: on the first run it is registered as trusted history using the existing CSV and reconciliation data, and it is not parsed again. Do not merge future statements into a replacement `combined.pdf`; incremental imports make that unnecessary.

The CSV, reconciliation file, and import log carry validated history, so back up those three files together. If the CSV or import log is deleted, the incremental workflow cannot reliably distinguish previously validated data from new input.

### What happens when an import fails

The new PDF is unlocked in memory, then checked before anything is appended. If its configured password is missing or incorrect, or it is incompatible, unreconciled, or conflicts with a statement period already in the ledger, the run stops and the existing CSV remains unchanged. Correct or replace that PDF and run the launcher again.

Editing `merchant_categories.json` directly affects future imports only. Previously validated CSV rows are intentionally left unchanged unless you edit a category through the dashboard.

If the window reports `No module named ...`, repeat the one-time setup. If it reports a password error, confirm `data/statement_password.txt` exists and contains the current password. If a statement you expected to import was reported as skipped, or a PDF is rejected as incompatible, confirm that it is a complete HSBC or ING statement that matches the requirements below. If a statement does not reconcile, nothing is appended; confirm that the PDF is complete and uses the expected layout before trying again.

## PDF requirements

The parser supports the machine-generated HSBC Everyday Global statement layout and ING interim statements. A suitable input must:

- be a complete PDF statement, including every transaction page and the statement totals;
- contain selectable/searchable text, not photographs or scanned page images;
- contain a supported transaction table with date, description, debit/withdrawal, credit/deposit, and balance columns in the expected positions;
- include the printed opening and closing balances; HSBC statements must also include printed debit and credit totals, while ING statements are reconciled from opening balance + deposits - withdrawals = closing balance because ING's interim layout does not print transaction totals; and
- be either unencrypted or unlockable with the password stored in `data/statement_password.txt`.

Password-protected PDFs are opened directly in memory; the source PDF remains encrypted and is never modified. Keep the password only in `data/statement_password.txt`, never in a file name, the code, or this README.

There is no required statement file name. Each previously unseen PDF in `data/` is checked to see whether it is an HSBC or ING statement. A PDF that is neither (a receipt or an invoice, for example) is skipped with a `Skipped ...: not a recognised HSBC or ING statement` message, and the rest of the import carries on. Skipped PDFs are not recorded in the import log, so they are checked again on the next run; move them elsewhere to silence the message. A PDF that looks like an HSBC or ING statement but cannot be read or reconciled still stops the import, so a layout change is never skipped silently.

## Editing categories

When the dashboard is opened with **Edit** (or after **Update**), each transaction's Category cell is an editable dropdown, including `Gifts`. A selection is saved immediately to `bank_transactions.csv`, the row is marked as a reviewed manual category, and its subcategory is set to `Manual selection`. Select “Apply category to all existing transactions from this merchant” before changing a category to update every ledger row with the same merchant name (ignoring case and punctuation). Future-import merchant overrides are separate: select “Also apply expense choices to future imports from the same merchant” to save that rule to `merchant_categories.json`. The dashboard HTML is regenerated after each save.

The Transaction drill-down headers sort all matching rows before pagination. Click a header again to reverse its order. The Timeframe dropdown offers This month (from the first day through today) and the previous 1, 2, 3, 6, or 12 complete calendar months; changing either date field switches back to a custom range.

## Categorisation methodology

1. Debit and credit columns determine transaction direction.
2. Manual overrides in `merchant_categories.json` have highest priority.
3. Reviewed merchant aliases canonicalise known merchants and assign categories.
4. Bounded regular-expression rules classify broader merchant patterns.
5. Unmatched transactions remain `Uncategorised` with `review_status=review`.

Where a merchant's activity strongly suggests a category but its legal name is opaque, the alias can use a lower confidence and `review_status=review`. This gives the dashboard a useful provisional breakdown without presenting the classification as confirmed.

Automatic results are not written back into the category configuration. Dashboard edits do write a `manual_overrides` entry, keyed by the normalised merchant name. To review a merchant manually, you can also add an entry under `manual_overrides`:

```json
{
  "some merchant": {
    "canonical_merchant": "Some Merchant",
    "category": "Dining & Takeaway",
    "subcategory": "Restaurant"
  }
}
```

The built-in rules in `CATEGORY_RULES` (in `src/bank_statement_dashboard.py`) only name common chains and generic words. To teach a rule your own local merchants without adding them to the code, list extra patterns under `rule_patterns` in `data/merchant_categories.json`, keyed by the rule id. Patterns for `housing_rent` also stop rent payments being counted as transfers.

```json
{
  "version": 2,
  "rule_patterns": {
    "dining_named_eatery": ["\\blantern lane\\b"],
    "housing_rent": ["\\bacme property\\b"]
  }
}
```

Each CSV row records the categorisation source, rule identifier, confidence, review status, source PDF page, and a stable transaction ID. Identical legitimate purchases are preserved as separate transactions.

## Reconciliation standard

An HSBC statement is marked reconciled only when all three checks pass to the cent:

- extracted debits equal the statement's printed debit total;
- extracted credits equal the statement's printed credit total;
- opening balance + credits - debits equals the printed closing balance.

ING interim statements do not print debit or credit totals, so an ING statement is marked reconciled when opening balance + deposits - withdrawals equals the printed closing balance to the cent.

The dashboard exposes reconciliation and category-review status alongside the financial breakdowns.

## Privacy

`data/` is git-ignored, and so are PDFs, `bank_transactions.csv`, `statement_reconciliation.json`, `statement_import_log.json`, `merchant_categories.json`, `statement_password.txt` and `index.html` anywhere else in the folder. The Tests workflow fails if any of them is ever added to the repository.

`index.html` embeds every transaction so that it works offline, so it is published to Cloudflare from your computer rather than through git.

### Publishing to Cloudflare

**Publish** (and **Update**) copies `data/index.html` and `deploy/_headers` into an otherwise empty `deploy/site/` folder and uploads only that folder with [Wrangler](https://developers.cloudflare.com/workers/wrangler/), using the Worker defined in `deploy/wrangler.jsonc`.

One-time setup:

1. Install [Node.js](https://nodejs.org). The first publish opens a browser to sign in to Cloudflare.
2. Set `name` in `deploy/wrangler.jsonc` to your Worker's name.
3. If the Worker was connected to a GitHub repository for automatic builds, disconnect it (Worker > Settings > Build), otherwise a push would replace the dashboard with a build that has no data.
4. Protect the site with [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/policies/access/) before publishing real data. Put the policy on the whole hostname (both the `workers.dev` address and any custom domain), and keep its allow list to named email addresses or GitHub accounts rather than a whole email domain.

`deploy/_headers` tells browsers and caches never to store, index, frame or refer the page, and limits the page to talking to its own origin. Keep it identical to `DASHBOARD_SECURITY_HEADERS` in `src/bank_statement_dashboard.py`; a test checks this.

### Local editing server

`src/dashboard_server.py` listens on `127.0.0.1` only and serves nothing but the dashboard. It rejects requests that arrive under any other host name (which blocks DNS-rebinding attacks from websites open in the same browser), and it only accepts category edits sent as JSON from the dashboard's own page, so another website cannot change the ledger while the server is running.

## Tests

Run `python -m pytest -q` from the repository root. The tests in `tests/` use generated sample HSBC and ING statements (see `tests/statement_fixtures.py`) and run anywhere, including on GitHub Actions for every push. No test reads real statements.

## Dashboard sections

The dashboard is generated from `src/dashboard_template.html`; edit that file to change its layout.

- **Overview** - a card layout with the closing balance and a monthly money in vs money out chart, money in, money out and net savings compared with the prior period of the same length (or the same number of calendar months), a money out vs money in bar, short insights, a cost analysis by category, a savings-rate gauge, a recurring payments tracker, a card per bank account with quick actions (export, statement, review queue, filters, print) and the latest transactions.
- **Income statement** - a month-by-month table of income lines, spending by category, net savings and savings rate, with totals and monthly averages. Export CSV downloads it.
- **Spending** - category breakdown against the prior period, monthly spending by category, top merchants, and recurring payments (merchants charged in three or more months, with fixed-amount charges listed first).
- **Accounts & reconciliation** - one card per bank, the daily balance of each account and the combined total, a register of every statement period with its opening and closing balances, warnings for statements that do not reconcile or for missing statement periods, net transfers and data-quality counts.
- **Transactions** - the editable drill-down, with an Account column and Export CSV for the filtered rows.

Money in counts income and cashback; money out counts spending net of refunds; transfers are left out of both. Changing a transfer's category in the Transactions table (for example a salary paid by transfer to `Income`, or rent paid by transfer to `Housing & Rent`) makes it count under that category. Print report produces a printable summary without the transaction table. The page follows the system light or dark setting.

Charts don't zoom or pan. Instead, tapping (or clicking) a chart, a summary figure, an income statement cell, a category or a recurring payment opens a details panel. It shows the totals, a spending-by-category breakdown and the transactions behind that figure, and from there **Filter dashboard** or **Open in Transactions** applies the same filter. On phones the panel slides up from the bottom (the back gesture closes it), the sidebar becomes a bottom navigation bar, active filters show as chips you tap to remove, and transactions are listed as cards with a sort menu. The timeframe and quick search sit in the top bar; the other filters open from **Filters**.
