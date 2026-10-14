from pathlib import Path

import fitz
import pandas as pd

from bank_statement_dashboard import (
    EXPECTED_COLUMNS,
    append_history,
    build_dashboard,
    dashboard_category_options,
    deduplicate_statement_summaries,
    discover_statement_files,
    _infer_kind,
    file_sha256,
    infer_category,
    load_known_categories,
    parse_statement_pdf_with_summary,
    trust_existing_combined_pdf,
    update_transaction_category,
)


ROOT = Path(__file__).parent










def test_pdf_discovery_includes_combined_and_monthly_statements(tmp_path):
    (tmp_path / "combined.pdf").touch()
    (tmp_path / "hsbc_2026-08.pdf").touch()
    (tmp_path / "notes.txt").touch()

    assert [path.name for path in discover_statement_files(tmp_path)] == [
        "combined.pdf",
        "hsbc_2026-08.pdf",
    ]


def test_password_protected_statement_is_read_without_an_unlocked_copy(tmp_path):
    password = "test-statement-password"
    encrypted_pdf = tmp_path / "Email Statement.pdf"
    password_file = tmp_path / "statement_password.txt"
    password_file.write_text(password, encoding="utf-8")

    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Encrypted statement test")
    document.save(
        encrypted_pdf,
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="test-owner-password",
        user_pw=password,
    )
    document.close()

    config = load_known_categories(ROOT / "merchant_categories.json")
    records, summaries = parse_statement_pdf_with_summary(encrypted_pdf, config)

    assert records == []
    assert summaries == []
    assert [path.name for path in tmp_path.glob("*.pdf")] == ["Email Statement.pdf"]
    locked_document = fitz.open(encrypted_pdf)
    try:
        assert locked_document.needs_pass
    finally:
        locked_document.close()


def test_zero_activity_hsbc_statement_does_not_create_fake_transactions(tmp_path):
    statement = tmp_path / "zero-activity.pdf"
    document = fitz.open()
    page = document.new_page(width=612, height=792)

    def text(x, y, value):
        page.insert_text((x, y), value, fontsize=8)

    text(300, 30, "STATEMENT PERIOD FROM 15 Mar 2026 TO 14 Apr 2026")
    text(50, 80, "Date")
    text(100, 80, "Transaction Details")
    text(306, 80, "Debits/Withdrawals")
    text(412, 80, "Credits/Deposits")
    text(520, 80, "Balance")
    text(46, 100, "15 Mar")
    text(98, 100, "BALANCE BROUGHT FORWARD")
    text(536, 100, "0.00")
    text(46, 116, "14 Apr")
    text(98, 116, "CLOSING BALANCE")
    text(536, 116, "0.00")
    text(46, 132, "HSBC BONUS")
    text(98, 132, "SAVINGS BSB No. 000000 Account")
    text(316, 132, "123456789")
    text(98, 148, "Transaction Total")
    text(365, 148, "0.00")
    text(458, 148, "0.00")
    text(98, 164, "Transaction Number")
    text(375, 164, "0")
    text(468, 164, "0")
    document.save(statement)
    document.close()

    config = load_known_categories(ROOT / "merchant_categories.json")
    records, summaries = parse_statement_pdf_with_summary(statement, config)

    assert records == []
    assert summaries == [
        {
            "statement_start": "2026-03-15",
            "statement_end": "2026-04-14",
            "opening_balance": 0.0,
            "closing_balance": 0.0,
            "printed_debits": 0.0,
            "printed_credits": 0.0,
            "computed_debits": 0.0,
            "computed_credits": 0.0,
            "debit_difference": 0.0,
            "credit_difference": 0.0,
            "balance_difference": 0.0,
            "reconciled": True,
            "bank": "HSBC",
            "transaction_count": 0,
        }
    ]


def test_duplicate_statement_summaries_are_collapsed():
    summary = {
        "statement_start": "2026-02-01",
        "statement_end": "2026-02-28",
        "opening_balance": 100.0,
        "closing_balance": 80.0,
        "printed_debits": 20.0,
        "printed_credits": 0.0,
        "computed_debits": 20.0,
        "computed_credits": 0.0,
        "transaction_count": 1,
        "reconciled": True,
    }

    assert deduplicate_statement_summaries([summary, dict(summary)]) == [summary]


def test_history_append_preserves_existing_rows(tmp_path):
    history_path = tmp_path / "bank_transactions.csv"
    existing = pd.DataFrame(
        [{**{column: "" for column in EXPECTED_COLUMNS}, "transaction_id": "validated-old"}]
    )
    existing.to_csv(history_path, index=False)
    original_bytes = history_path.read_bytes()
    new = pd.DataFrame(
        [{**{column: "" for column in EXPECTED_COLUMNS}, "transaction_id": "validated-new"}]
    )

    append_history(history_path, new)

    assert history_path.read_bytes().startswith(original_bytes)
    assert list(pd.read_csv(history_path)["transaction_id"]) == [
        "validated-old",
        "validated-new",
    ]


def test_existing_combined_pdf_is_registered_without_reparsing(tmp_path):
    combined = tmp_path / "combined.pdf"
    combined.write_bytes(b"already validated PDF bytes")
    history = pd.DataFrame(
        [
            {
                "transaction_id": "validated-old",
                "statement_start": "2025-11-01",
                "statement_end": "2025-11-30",
            }
        ]
    )
    reconciliation = [
        {
            "statement_start": "2025-11-01",
            "statement_end": "2025-11-30",
            "reconciled": True,
        }
    ]
    import_log = {"version": 1, "files": {}}

    assert trust_existing_combined_pdf(tmp_path, history, reconciliation, import_log)
    entry = import_log["files"][file_sha256(combined)]
    assert entry["status"] == "trusted_existing_history"
    assert entry["transaction_count"] == 1




def test_dashboard_is_offline_responsive_and_escapes_embedded_data(tmp_path):
    row = {column: "" for column in EXPECTED_COLUMNS}
    row.update(
        {
            "transaction_id": "test",
            "date": "2026-01-01",
            "statement_start": "2026-01-01",
            "statement_end": "2026-01-31",
            "merchant": "</script><script>alert('x')</script>",
            "raw_description": "test",
            "debit": 1.0,
            "credit": 0.0,
            "signed_amount": -1.0,
            "amount": 1.0,
            "kind": "expense",
            "category": "Uncategorised",
            "subcategory": "Needs review",
            "confidence": 0.0,
            "review_status": "review",
        }
    )
    output = tmp_path / "dashboard.html"
    build_dashboard(pd.DataFrame([row]), output, [])
    dashboard = output.read_text(encoding="utf-8")
    assert "<script src=" not in dashboard
    assert "@media (max-width:760px)" in dashboard
    assert "Transaction drill-down" in dashboard
    assert r"<\/script><script>alert" in dashboard
    assert "</script><script>alert" not in dashboard


def test_dashboard_category_edit_updates_ledger_config_and_html(tmp_path):
    history_path = tmp_path / "bank_transactions.csv"
    categories_path = tmp_path / "merchant_categories.json"
    reconciliation_path = tmp_path / "statement_reconciliation.json"
    output_path = tmp_path / "dashboard.html"
    row = {column: "" for column in EXPECTED_COLUMNS}
    row.update(
        {
            "transaction_id": "editable-transaction",
            "date": "2026-01-01",
            "statement_start": "2026-01-01",
            "statement_end": "2026-01-31",
            "merchant": "Example Cafe",
            "debit": 12.5,
            "credit": 0.0,
            "signed_amount": -12.5,
            "amount": 12.5,
            "kind": "expense",
            "category": "Uncategorised",
            "subcategory": "Needs review",
            "category_source": "unmatched",
            "rule_id": "unmatched",
            "confidence": 0.0,
            "review_status": "review",
            "quality_flag": "needs_category_review",
        }
    )
    pd.DataFrame([row]).to_csv(history_path, index=False)
    categories_path.write_text(
        '{"version": 2, "manual_overrides": {}, "aliases": []}', encoding="utf-8"
    )
    reconciliation_path.write_text("[]", encoding="utf-8")

    result = update_transaction_category(
        history_path,
        categories_path,
        reconciliation_path,
        output_path,
        "editable-transaction",
        "Dining & Takeaway",
        apply_to_future=True,
    )

    updated = pd.read_csv(history_path).iloc[0]
    config = load_known_categories(categories_path)
    assert result["category"] == "Dining & Takeaway"
    assert updated["category"] == "Dining & Takeaway"
    assert updated["subcategory"] == "Manual selection"
    assert updated["category_source"] == "dashboard_manual"
    assert updated["review_status"] == "reviewed"
    assert updated["quality_flag"] != "needs_category_review"
    assert config["manual_overrides"]["example cafe"]["category"] == "Dining & Takeaway"
    assert "transaction-category" in output_path.read_text(encoding="utf-8")


def test_merchant_category_edit_updates_all_matching_history_only(tmp_path):
    history_path = tmp_path / "bank_transactions.csv"
    categories_path = tmp_path / "merchant_categories.json"
    reconciliation_path = tmp_path / "statement_reconciliation.json"
    output_path = tmp_path / "dashboard.html"
    rows = []
    for transaction_id, merchant, kind in [
        ("first", "Example Cafe", "expense"),
        ("second", "EXAMPLE-CAFE", "expense"),
        ("third", "Example Cafe", "refund"),
        ("other", "Other Cafe", "expense"),
    ]:
        row = {column: "" for column in EXPECTED_COLUMNS}
        row.update(
            transaction_id=transaction_id,
            merchant=merchant,
            kind=kind,
            date="2026-01-01",
            category="Uncategorised",
            subcategory="Needs review",
            category_source="unmatched",
            rule_id="unmatched",
            confidence=0.0,
            review_status="review",
            quality_flag="needs_category_review;check_amount",
        )
        rows.append(row)
    pd.DataFrame(rows).to_csv(history_path, index=False)
    categories_path.write_text('{"version": 2, "manual_overrides": {}, "aliases": []}', encoding="utf-8")
    reconciliation_path.write_text("[]", encoding="utf-8")

    assert "Gifts" in dashboard_category_options(pd.DataFrame(rows))
    result = update_transaction_category(
        history_path,
        categories_path,
        reconciliation_path,
        output_path,
        "first",
        "Gifts",
        apply_to_merchant=True,
    )

    updated = pd.read_csv(history_path).set_index("transaction_id")
    assert result["updated_count"] == 3
    assert result["apply_to_merchant"] is True
    assert set(result["updated_transaction_ids"]) == {"first", "second", "third"}
    assert set(updated.loc[["first", "second", "third"], "category"]) == {"Gifts"}
    assert set(updated.loc[["first", "second", "third"], "review_status"]) == {"reviewed"}
    assert set(updated.loc[["first", "second", "third"], "quality_flag"]) == {"check_amount"}
    assert updated.loc["other", "category"] == "Uncategorised"
    assert updated.loc["other", "quality_flag"] == "needs_category_review;check_amount"
    assert load_known_categories(categories_path)["manual_overrides"] == {}


def test_local_rule_patterns_extend_built_in_category_rules():
    config = {
        "version": 2,
        "manual_overrides": {},
        "aliases": [],
        "rule_patterns": {
            "dining_named_eatery": [r"\blantern lane\b"],
            "housing_rent": [r"\bacme property\b"],
        },
    }
    eatery = infer_category("LANTERN LANE", 18.0, "EFTPOS VISA AUD LANTERN LANE", config)
    assert eatery["category"] == "Dining & Takeaway"
    assert eatery["rule_id"] == "dining_named_eatery"
    assert infer_category("LANTERN LANE", 18.0, "LANTERN LANE", {})["category"] == "Uncategorised"

    rent = "TEN123456 ACME PROPERTY 12345678 RENT REF"
    assert _infer_kind(rent.replace(" RENT REF", ""), 500.0, 0.0, config) == "expense"
    assert _infer_kind(rent.replace(" RENT REF", ""), 500.0, 0.0) == "outgoing_transfer"
