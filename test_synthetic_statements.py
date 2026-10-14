"""Parser and import tests that use generated sample statements, not private data."""

import json
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
import pytest

import bank_statement_dashboard
from bank_statement_dashboard import (
    DASHBOARD_SECURITY_HEADERS,
    detect_statement_bank,
    infer_category,
    parse_statement_pdf_with_summary,
)
from statement_fixtures import (
    write_hsbc_statement,
    write_sample_hsbc_statement,
    write_sample_ing_statement,
    write_unrelated_pdf,
)


ROOT = Path(__file__).parent


@pytest.fixture
def hsbc(tmp_path):
    path = write_sample_hsbc_statement(tmp_path / "hsbc_sample.pdf")
    return parse_statement_pdf_with_summary(path, {})


@pytest.fixture
def ing(tmp_path):
    path = write_sample_ing_statement(tmp_path / "ing_sample.pdf")
    return parse_statement_pdf_with_summary(path, {})


def test_hsbc_periods_reconcile_to_printed_totals(hsbc):
    records, summaries = hsbc
    assert len(summaries) == 2
    assert len(records) == sum(summary["transaction_count"] for summary in summaries) == 10
    for summary in summaries:
        assert summary["reconciled"]
        assert summary["debit_difference"] == 0
        assert summary["credit_difference"] == 0
        assert summary["balance_difference"] == 0


def test_hsbc_year_rollover_is_resolved_per_statement_period(hsbc):
    records, _ = hsbc
    dates = [record["date"] for record in records]
    assert min(dates) == "2025-12-20"
    assert max(dates) == "2026-02-03"
    assert "2026-01-05" in dates
    assert not any(value.startswith("2025-01") or value.startswith("2026-12") for value in dates)


def test_hsbc_reference_numbers_are_not_parsed_as_amounts(hsbc):
    records, _ = hsbc
    assert not any(record["amount"] in {1111.0, 2222.0} for record in records)
    costco = next(record for record in records if record["date"] == "2025-12-20")
    assert costco["debit"] == 120.95
    assert costco["raw_description"].endswith("Ref 1111")


def test_hsbc_debits_credits_and_transfers_are_preserved(hsbc):
    records, _ = hsbc
    kinds = Counter(record["kind"] for record in records)
    assert kinds["expense"] == 6
    assert kinds["cashback"] == 1
    assert kinds["incoming_transfer"] == 2
    assert kinds["outgoing_transfer"] == 1
    assert any(record["debit"] == 2000.0 and record["kind"] == "outgoing_transfer" for record in records)
    assert any(record["credit"] == 2000.0 and record["kind"] == "incoming_transfer" for record in records)
    rent = next(record for record in records if record["merchant"] == "Rent payment")
    assert rent["kind"] == "expense" and rent["category"] == "Housing & Rent"


def test_hsbc_identical_purchases_keep_distinct_ids_and_parsing_is_idempotent(tmp_path, hsbc):
    records, _ = hsbc
    mcdonalds = [record for record in records if record["merchant"] == "MCDONALDS SAMPLEVILLE"]
    assert len(mcdonalds) == 2
    assert len({record["transaction_id"] for record in records}) == len(records)

    again = write_sample_hsbc_statement(tmp_path / "hsbc_sample.pdf")
    second_records, _ = parse_statement_pdf_with_summary(again, {})
    assert [record["transaction_id"] for record in records] == [
        record["transaction_id"] for record in second_records
    ]


def test_records_have_auditable_category_metadata(hsbc, ing):
    records = hsbc[0] + ing[0]
    assert all(0 <= float(record["confidence"]) <= 1 for record in records)
    assert all(record["category_source"] and record["rule_id"] for record in records)
    assert all(record["review_status"] in {"auto", "review", "reviewed"} for record in records)
    unmatched = [record for record in records if record["category"] == "Uncategorised"]
    assert {record["merchant"] for record in unmatched} == {"COSTCO SAMPLEVILLE", "MYSTERY SHOP"}
    assert all(record["review_status"] == "review" for record in unmatched)


def test_ing_statement_reconciles_from_balances(ing):
    records, summaries = ing
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary["reconciled"]
    assert summary["opening_balance"] == 250.0
    assert summary["closing_balance"] == 3204.8
    assert summary["computed_credits"] == 3500.0
    assert summary["computed_debits"] == 545.2
    assert [record["kind"] for record in records] == [
        "incoming_transfer",
        "expense",
        "outgoing_transfer",
        "income",
    ]


def test_category_aliases_and_word_boundaries():
    config = {
        "version": 2,
        "manual_overrides": {},
        "aliases": [
            {"match": "ww metro", "category": "Groceries", "subcategory": "Supermarket"},
        ],
    }
    mcdonalds = infer_category("MCDONALDS SAMPLEVILLE", 1.49, "EFTPOS VISA AUD MCDONALDS SAMPLEVILLE", config)
    woolworths_metro = infer_category("WW METRO", 20.0, "EFTPOS VISA AUD WW METRO CNR MAIN ST", config)
    # "gas" must not match inside an unrelated word such as "vegas".
    vegas = infer_category("VEGAS BURGERS", 18.0, "EFTPOS VISA AUD VEGAS BURGERS", config)
    assert mcdonalds["category"] == "Dining & Takeaway"
    assert woolworths_metro["category"] == "Groceries"
    assert woolworths_metro["category_source"] == "merchant_alias"
    assert vegas["category"] != "Utilities"


def test_bank_detection(tmp_path):
    assert detect_statement_bank(write_sample_hsbc_statement(tmp_path / "a.pdf")) == "hsbc"
    assert detect_statement_bank(write_sample_ing_statement(tmp_path / "b.pdf")) == "ing"
    assert detect_statement_bank(write_unrelated_pdf(tmp_path / "c.pdf")) is None


def run_import(monkeypatch, folder: Path) -> None:
    monkeypatch.chdir(folder)
    monkeypatch.setattr(sys, "argv", ["bank_statement_dashboard.py", "--statement-dir", "."])
    bank_statement_dashboard.main()


def test_monthly_import_appends_new_statements_and_skips_unrelated_pdfs(tmp_path, monkeypatch, capsys):
    write_sample_hsbc_statement(tmp_path / "hsbc_2026-02.pdf")
    write_sample_ing_statement(tmp_path / "ing_2026-03.pdf")
    write_unrelated_pdf(tmp_path / "coffee_grinder_receipt.pdf")

    run_import(monkeypatch, tmp_path)
    output = capsys.readouterr().out
    assert "Imported 2 new PDF(s)" in output
    assert "Skipped coffee_grinder_receipt.pdf: not a recognised HSBC or ING statement" in output

    history = pd.read_csv(tmp_path / "bank_transactions.csv")
    assert len(history) == 14
    import_log = json.loads((tmp_path / "statement_import_log.json").read_text(encoding="utf-8"))
    assert sorted(entry["source_file"] for entry in import_log["files"].values()) == [
        "hsbc_2026-02.pdf",
        "ing_2026-03.pdf",
    ]
    reconciliation = json.loads((tmp_path / "statement_reconciliation.json").read_text(encoding="utf-8"))
    assert len(reconciliation) == 3 and all(item["reconciled"] for item in reconciliation)
    dashboard = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert '<meta name="robots" content="noindex, nofollow, noarchive">' in dashboard
    assert '<meta name="referrer" content="no-referrer">' in dashboard

    csv_before = (tmp_path / "bank_transactions.csv").read_bytes()
    run_import(monkeypatch, tmp_path)
    output = capsys.readouterr().out
    assert "No new statements found; transaction CSV left unchanged" in output
    assert "Skipped 2 previously imported PDF(s)" in output
    assert (tmp_path / "bank_transactions.csv").read_bytes() == csv_before


def test_unreconciled_statement_stops_the_import_without_touching_the_ledger(tmp_path, monkeypatch):
    statement = write_sample_ing_statement(tmp_path / "ing_2026-03.pdf")
    run_import(monkeypatch, tmp_path)
    csv_before = (tmp_path / "bank_transactions.csv").read_bytes()

    write_hsbc_statement(
        tmp_path / "hsbc_2026-04.pdf",
        [("01 Apr 2026 TO 30 Apr 2026", 10.0, [("02 Apr", "EFTPOS VISA AUD SHOP 12345678", "1.00", None)])],
        misprinted_debits=5.0,
    )

    with pytest.raises(ValueError, match="did not reconcile"):
        run_import(monkeypatch, tmp_path)
    assert (tmp_path / "bank_transactions.csv").read_bytes() == csv_before
    assert statement.exists()


def test_cloudflare_headers_file_matches_dashboard_security_headers():
    lines = [
        line.strip()
        for line in (ROOT / "_headers").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert lines[0] == "/*"
    headers = dict(line.split(": ", 1) for line in lines[1:])
    assert headers == DASHBOARD_SECURITY_HEADERS


def test_cloudflare_only_publishes_the_dashboard():
    config = json.loads(
        "\n".join(
            line
            for line in (ROOT / "wrangler.jsonc").read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("//")
        )
    )
    assert config["assets"] == {"directory": "./site"}
    deploy = (ROOT / "deploy_dashboard.sh").read_text(encoding="utf-8")
    assert "rm -rf site" in deploy
    assert "cp index.html _headers site/" in deploy
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "index.html" in ignored
    assert "site/" in ignored


def test_dashboard_labels_each_statement_and_transaction_with_its_account(tmp_path, hsbc, ing):
    hsbc_records, hsbc_summaries = hsbc
    ing_records, ing_summaries = ing
    assert {item["bank"] for item in hsbc_summaries} == {"HSBC"}
    assert {item["bank"] for item in ing_summaries} == {"ING"}

    # Reconciliation files written before summaries recorded their bank.
    legacy = [{key: value for key, value in item.items() if key != "bank"} for item in hsbc_summaries + ing_summaries]
    assert [bank_statement_dashboard.statement_account(item) for item in legacy] == ["HSBC"] * len(hsbc_summaries) + ["ING"]

    output = tmp_path / "index.html"
    bank_statement_dashboard.build_dashboard(pd.DataFrame(hsbc_records + ing_records), output, legacy)
    dashboard = output.read_text(encoding="utf-8")
    for heading in ("Income statement", "Statement register", "Recurring payments", "Account balance", "Transaction drill-down"):
        assert heading in dashboard
    assert "HSBC and ING" in dashboard
    # Every template placeholder was filled in.
    assert not re.search(r"__[A-Z_]+__", dashboard.split("<script>", 2)[0])
    embedded = dashboard.split("const transactions = ", 1)[1].split(";\n", 1)[0]
    accounts = Counter(item["account"] for item in json.loads(embedded))
    assert accounts == {"HSBC": len(hsbc_records), "ING": len(ing_records)}
