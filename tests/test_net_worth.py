"""Net worth: month-end values and the split of each change into savings and returns."""

import json

import pandas as pd
import pytest

from bank_statement_dashboard import EXPECTED_COLUMNS, build_dashboard
from net_worth import bank_balances, build_net_worth, load_holdings, load_prices, month_ends, yahoo_symbol


def flat(price, start="2025-12-01", end="2026-04-30"):
    """Daily closes at one price for every day in a range."""
    return {day.date().isoformat(): price for day in pd.date_range(start, end)}


def position(symbol, steps, currency="AUD"):
    return {"name": symbol, "account": "Broker", "currency": currency, "symbol": symbol, "steps": steps}


def test_yahoo_symbols_for_each_exchange():
    assert yahoo_symbol("AAPL.US") == "AAPL"
    assert yahoo_symbol("VAS.AU") == "VAS.AX"
    assert yahoo_symbol("600000.SHG") == "600000.SS"
    assert yahoo_symbol("000001.SHE") == "000001.SZ"
    with pytest.raises(ValueError):
        yahoo_symbol("ABC.XX")


def test_month_ends_finish_on_today():
    assert month_ends("2025-11-01", "2026-01-15") == ["2025-11-30", "2025-12-31", "2026-01-15"]


def test_price_rise_is_a_return_and_fx_converts_to_aud():
    closes = flat(10.0, end="2026-01-31")
    closes.update(flat(12.0, start="2026-01-01", end="2026-02-28"))
    prices = {"ABC": {"closes": closes, "splits": {}}, "AUDUSD=X": {"closes": flat(0.5, end="2026-02-28"), "splits": {}}}
    holdings = {"positions": [position("ABC", [("2025-12-01", 100.0)], currency="USD")], "cash": []}

    result = build_net_worth(holdings, prices, {}, "2026-02-28")

    # 100 units x US$2 rise / 0.5 USD per AUD = A$400, all in January.
    january = result["months"][1]
    assert january["returns"] == pytest.approx(400.0)
    assert january["savings"] == pytest.approx(0.0)
    assert result["years"]["2026"]["returns"] == pytest.approx(400.0)


def test_buying_shares_with_cash_is_neither_savings_nor_returns():
    prices = {"ABC": {"closes": flat(10.0), "splits": {}}}
    holdings = {
        "positions": [position("ABC", [("2025-12-01", 100.0), ("2026-02-10", 200.0)])],
        "cash": [{"name": "Cash", "account": "Cash", "currency": "AUD", "steps": [("2025-12-01", 5000.0), ("2026-02-10", 4000.0)]}],
    }

    result = build_net_worth(holdings, prices, {}, "2026-03-31")

    february = next(row for row in result["months"] if row["date"] == "2026-02-28")
    assert february["investments"] == pytest.approx(2000.0)
    assert february["cash"] == pytest.approx(4000.0)
    assert february["returns"] == pytest.approx(0.0)
    assert february["savings"] == pytest.approx(0.0)


def test_split_recorded_late_is_not_a_purchase():
    # Yahoo closes are split-adjusted, so the 8:1 split is invisible in prices;
    # the post-split quantity is only recorded four days after the split.
    prices = {"ABC": {"closes": flat(10.0), "splits": {"2026-04-21": 8.0}}}
    holdings = {"positions": [position("ABC", [("2025-12-01", 10.0), ("2026-04-25", 80.0)])], "cash": []}

    result = build_net_worth(holdings, prices, {}, "2026-04-30")

    assert [row["investments"] for row in result["months"]] == [800.0] * len(result["months"])
    assert all(row.get("returns", 0) == 0 and row.get("savings", 0) == 0 for row in result["months"])


def test_bank_cash_counts_as_savings():
    prices = {"ABC": {"closes": flat(10.0), "splits": {}}}
    holdings = {"positions": [position("ABC", [("2025-12-01", 1.0)])], "cash": []}
    statements = [{"account": "HSBC", "statement_start": "2025-12-01", "statement_end": "2026-01-31", "opening_balance": 100.0, "closing_balance": 600.0}]
    row = {column: "" for column in EXPECTED_COLUMNS}
    row.update(statement_start="2025-12-01", statement_end="2026-01-31", date="2026-01-15", signed_amount=500.0)

    result = build_net_worth(holdings, prices, bank_balances(pd.DataFrame([row]), statements), "2026-01-31")

    assert result["months"][-1]["cashAccounts"] == {"HSBC": 600.0}
    assert result["months"][-1]["savings"] == pytest.approx(500.0)


def test_failed_fetch_falls_back_to_cached_prices(tmp_path):
    cache = tmp_path / "price_history.json"
    load_prices(["ABC"], cache, fetch=lambda symbol, start: {"closes": {"2026-01-02": 5.0}, "splits": {}})

    def offline(symbol, start):
        raise OSError("network down")

    prices, problems = load_prices(["ABC", "NEW"], cache, fetch=offline)

    assert prices["ABC"]["closes"] == {"2026-01-02": 5.0}
    assert "ABC" in problems[0] and "cached" in problems[0]
    assert "NEW" not in prices and "no prices" in problems[1]


def test_load_holdings_reads_the_investment_project(tmp_path):
    (tmp_path / "portfolio").mkdir()
    (tmp_path / "portfolio" / "current_portfolio.json").write_text(json.dumps({"portfolios": [
        {"Name": "US", "Currency": "USD", "Assets": [
            {"FullTicker": "ABC.US", "Name": "ABC", "Currency": "USD", "Valuations": [
                {"Date": "2026-04-22", "Quantity": 80}, {"Date": "2025-11-01", "Quantity": 10}]},
        ]},
        {"Name": "Cash", "Currency": "AUD", "Assets": [
            {"FullTicker": "", "Name": "Cash", "Currency": "AUD", "Valuations": [{"Date": "2026-02-16", "Quantity": 16000}]},
        ]},
    ]}), encoding="utf-8")

    holdings = load_holdings(tmp_path)

    assert holdings["positions"][0]["symbol"] == "ABC"
    assert holdings["positions"][0]["steps"] == [("2025-11-01", 10.0), ("2026-04-22", 80.0)]
    assert holdings["cash"][0]["steps"] == [("2026-02-16", 16000.0)]


def test_dashboard_embeds_net_worth(tmp_path):
    row = {column: "" for column in EXPECTED_COLUMNS}
    row.update(transaction_id="t1", date="2026-01-01", debit=1.0, kind="expense")
    net_worth = {"available": True, "months": [], "years": {}, "holdings": [], "problems": ["</script> check"]}

    build_dashboard(pd.DataFrame([row]), tmp_path / "index.html", [], net_worth)

    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'id="net-worth"' in page and "__NET_WORTH_JSON__" not in page
    assert "</script> check" not in page


def test_saver_statements_replace_the_hand_kept_cash_record():
    prices = {"ABC": {"closes": flat(10.0, end="2026-03-31"), "splits": {}}}
    holdings = {
        "positions": [position("ABC", [("2025-12-01", 1.0)])],
        "cash": [{"name": "Cash", "account": "Cash", "currency": "AUD", "steps": [("2026-01-10", 999.0), ("2026-03-01", 700.0)]}],
    }
    bank = {"Saver": [("2025-12-01", 1000.0), ("2026-02-15", 1200.0)]}

    result = build_net_worth(holdings, prices, bank, "2026-03-31", saver_account="Saver")
    cash = {row["date"]: row["cashAccounts"] for row in result["months"]}

    # Statements win while they cover the date; a later record carries it forward.
    assert cash["2026-01-31"] == {"Saver": 1000.0}
    assert cash["2026-02-28"] == {"Saver": 1200.0}
    assert cash["2026-03-31"] == {"Saver": 700.0}
