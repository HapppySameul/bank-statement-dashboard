"""Net worth: bank cash plus investment holdings, valued in AUD at month ends.

Holdings and their dated quantities come from the separate Investment project
(portfolio/current_portfolio.json). Prices and FX are Yahoo daily closes,
fetched when the dashboard is built; only ticker symbols leave this machine.
The last good prices are cached in data/ so an offline build still works.

A year's change in net worth is split into investment returns (price and FX
moves on the units held) and savings (everything else: salary kept, less
spending). Buying shares moves money from cash to investments, so it changes
neither figure.
"""

import calendar
import json
import urllib.parse
import urllib.request
from bisect import bisect_right
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/"
# Yahoo quotes AUDUSD=X as USD per AUD, so a USD price divided by it is AUD.
FX_SYMBOLS = {"USD": "AUDUSD=X", "CNY": "AUDCNY=X"}
# Exchange suffixes in the Investment project's tickers mapped to Yahoo's.
YAHOO_SUFFIX = {"US": "", "AU": ".AX", "SHG": ".SS", "SHE": ".SZ"}
# Investment-project accounts that hold cash rather than shares.
CASH_ACCOUNTS = {"Cash"}
# Which bank account the Investment project's AUD cash record tracks is set in
# the git-ignored data/net_worth_settings.json as {"saver_account": "<bank>"}.
# That account's statements win; the record only carries the balance forward
# after its last imported statement.
PRICE_HISTORY_START = "2024-12-01"

Series = Dict[str, float]  # {YYYY-MM-DD: close}


def yahoo_symbol(full_ticker: str) -> str:
    """Convert a ticker such as 'AAPL.US', 'VAS.AU' or '600000.SHG' to Yahoo's form."""
    code, _, exchange = full_ticker.rpartition(".")
    if not code or exchange not in YAHOO_SUFFIX:
        raise ValueError(f"Unknown exchange in ticker {full_ticker!r}")
    return code + YAHOO_SUFFIX[exchange]


def load_holdings(investment_dir: Path) -> Dict[str, object]:
    """Read dated quantities from the Investment project's canonical portfolio.

    Returns {"positions": [...], "cash": [...]} where every entry carries
    "steps": [(date, quantity), ...] sorted by date. A quantity applies from
    its date until the next one; before the first date it is zero.
    """
    path = Path(investment_dir) / "portfolio" / "current_portfolio.json"
    canonical = json.loads(path.read_text(encoding="utf-8"))
    positions, cash = [], []
    for account in canonical.get("portfolios", []):
        if account.get("IsDeleted") or account.get("ExcludeFromTotal"):
            continue
        for asset in account.get("Assets", []):
            if asset.get("IsDeleted"):
                continue
            steps = sorted(
                (str(item["Date"])[:10], float(item.get("Quantity") or 0))
                for item in asset.get("Valuations", [])
                if item.get("Date")
            )
            if not steps:
                continue
            entry = {
                "name": asset.get("Name") or asset.get("FullTicker") or "",
                "account": account.get("Name", ""),
                "currency": asset.get("Currency") or account.get("Currency") or "AUD",
                "steps": steps,
            }
            if account.get("Name") in CASH_ACCOUNTS or not asset.get("FullTicker"):
                if entry["currency"] == "AUD":
                    cash.append(entry)
                continue
            entry["symbol"] = yahoo_symbol(asset["FullTicker"])
            positions.append(entry)
    return {"positions": positions, "cash": cash, "source": str(path)}


def fetch_yahoo(symbol: str, start: str) -> Dict[str, object]:
    """Fetch daily closes and split events for one symbol from Yahoo.

    Closes are split-adjusted by Yahoo; splits are returned so dated
    quantities can be put on the same basis.
    """
    period1 = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp())
    query = urllib.parse.urlencode(
        {"period1": period1, "period2": int(datetime.now(timezone.utc).timestamp()), "interval": "1d", "events": "split"}
    )
    request = urllib.request.Request(
        f"{YAHOO_CHART}{urllib.parse.quote(symbol, safe='')}?{query}",
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))["chart"]["result"][0]
    offset = result["meta"].get("gmtoffset", 0)
    to_day = lambda stamp: datetime.fromtimestamp(stamp + offset, timezone.utc).date().isoformat()
    closes = {}
    for stamp, close in zip(result.get("timestamp", []), result["indicators"]["quote"][0]["close"]):
        if close is not None and close > 0:
            closes[to_day(stamp)] = float(close)
    splits = {
        to_day(int(event["date"])): float(event["numerator"]) / float(event["denominator"])
        for event in (result.get("events") or {}).get("splits", {}).values()
        if event.get("numerator") and event.get("denominator")
    }
    return {"closes": closes, "splits": splits, "currency": result["meta"].get("currency")}


def load_prices(
    symbols: List[str],
    cache_path: Optional[Path],
    fetch: Callable[[str, str], Dict[str, object]] = fetch_yahoo,
    start: str = PRICE_HISTORY_START,
) -> Tuple[Dict[str, Dict[str, object]], List[str]]:
    """Fetch every symbol, falling back to the cached copy when a fetch fails.

    Returns (prices, problems). A symbol with neither fresh nor cached prices
    is left out and named in problems.
    """
    cache: Dict[str, Dict[str, object]] = {}
    if cache_path and Path(cache_path).exists():
        try:
            cache = json.loads(Path(cache_path).read_text(encoding="utf-8")).get("symbols", {})
        except (OSError, ValueError):
            cache = {}
    prices, problems = {}, []
    for symbol in symbols:
        try:
            data = fetch(symbol, start)
            if not data.get("closes"):
                raise ValueError("no closes")
            prices[symbol] = dict(data, fetched=True)
        except Exception as error:  # network, HTTP or format problems all fall back the same way
            if symbol in cache:
                prices[symbol] = dict(cache[symbol], fetched=False)
                problems.append(f"{symbol}: live prices unavailable ({error}); used prices cached on {cache[symbol].get('fetched_at', 'an earlier run')}")
            else:
                problems.append(f"{symbol}: no prices available ({error})")
    if cache_path:
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        for symbol, data in prices.items():
            if data.get("fetched"):
                cache[symbol] = {key: value for key, value in data.items() if key != "fetched"}
                cache[symbol]["fetched_at"] = stamp
        Path(cache_path).write_text(json.dumps({"symbols": cache}, indent=1, sort_keys=True), encoding="utf-8")
    return prices, problems


def _value_on_or_before(series: Series, day: str) -> Optional[Tuple[str, float]]:
    days = sorted(series)
    index = bisect_right(days, day)
    return (days[index - 1], series[days[index - 1]]) if index else None


def _active_step(steps: List[Tuple[str, float]], day: str) -> Optional[Tuple[str, float]]:
    active = None
    for step in steps:
        if step[0] > day:
            break
        active = step
    return active


def _step_value(steps: List[Tuple[str, float]], day: str) -> float:
    active = _active_step(steps, day)
    return active[1] if active else 0.0


def _split_factor(splits: Dict[str, float], day: str) -> float:
    """Units held on `day` times this factor gives today's split-adjusted units."""
    factor = 1.0
    for split_day, ratio in splits.items():
        if split_day > day:
            factor *= ratio
    return factor


def month_ends(first: str, last: str) -> List[str]:
    """Every calendar month end from first's month to last's, ending with `last` itself."""
    year, month = int(first[:4]), int(first[5:7])
    result = []
    while True:
        end = date(year, month, calendar.monthrange(year, month)[1]).isoformat()
        if end >= last:
            result.append(last)
            return result
        result.append(end)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def bank_balances(transactions: pd.DataFrame, statements: List[Dict[str, object]]) -> Dict[str, List[Tuple[str, float]]]:
    """Daily end-of-day balance points per bank account, rebuilt from each statement."""
    by_period: Dict[Tuple[str, str], Dict[str, float]] = {}
    if not transactions.empty:
        frame = transactions.assign(signed_amount=pd.to_numeric(transactions["signed_amount"], errors="coerce").fillna(0.0))
        for (start, end, day), amount in frame.groupby(["statement_start", "statement_end", "date"])["signed_amount"].sum().items():
            by_period.setdefault((str(start), str(end)), {})[str(day)] = float(amount)
    points: Dict[str, List[Tuple[str, float]]] = {}
    for statement in sorted(statements, key=lambda item: str(item.get("statement_start"))):
        if statement.get("opening_balance") in (None, ""):
            continue
        start, end = str(statement["statement_start"]), str(statement["statement_end"])
        balance = float(statement["opening_balance"])
        series = points.setdefault(str(statement["account"]), [])
        series.append((start, round(balance, 2)))
        for day, amount in sorted(by_period.get((start, end), {}).items()):
            balance += amount
            series.append((day, round(balance, 2)))
        if statement.get("closing_balance") not in (None, ""):
            series.append((end, round(float(statement["closing_balance"]), 2)))
    return {account: sorted(series) for account, series in points.items()}


def _balance_at(series: List[Tuple[str, float]], day: str) -> Optional[float]:
    value = None
    for point_day, balance in series:
        if point_day > day:
            break
        value = balance
    return value


def build_net_worth(
    holdings: Optional[Dict[str, object]],
    prices: Dict[str, Dict[str, object]],
    bank: Dict[str, List[Tuple[str, float]]],
    today: str,
    problems: Optional[List[str]] = None,
    saver_account: Optional[str] = None,
) -> Dict[str, object]:
    """Month-end net worth with each month's change split into savings and returns."""
    problems = list(problems or [])
    holdings = holdings or {"positions": [], "cash": []}
    positions = [p for p in holdings["positions"] if p["symbol"] in prices]
    cash_records = holdings["cash"]
    fx = {currency: prices.get(symbol, {}).get("closes", {}) for currency, symbol in FX_SYMBOLS.items()}

    def price_aud(position: Dict[str, object], day: str) -> Optional[Tuple[str, float]]:
        quote = _value_on_or_before(prices[position["symbol"]]["closes"], day)
        if quote is None:
            return None
        if position["currency"] == "AUD":
            return quote
        rate = _value_on_or_before(fx.get(position["currency"], {}), day)
        return (quote[0], quote[1] / rate[1]) if rate else None

    def units(position: Dict[str, object], day: str) -> float:
        # A quantity is in units as recorded on its own date; Yahoo closes are
        # split-adjusted to today, so scale by the splits after that date. Using
        # the record's date (not `day`) keeps a split from looking like a purchase
        # when the post-split quantity is recorded a few days late.
        active = _active_step(position["steps"], day)
        if not active:
            return 0.0
        return active[1] * _split_factor(prices[position["symbol"]].get("splits", {}), active[0])

    # Start where the holdings records start: the first record is a snapshot of
    # shares already owned, and counting it as one month's savings would be wrong.
    starts = [item["steps"][0][0] for item in positions] or [series[0][0] for series in bank.values() if series]
    if not starts:
        return {"available": False, "problems": problems or ["No bank balances or investment holdings were found."]}
    dates = month_ends(min(starts), today)

    def investments_at(day: str) -> float:
        total = 0.0
        for position in positions:
            held = units(position, day)
            if held:
                quote = price_aud(position, day)
                if quote:
                    total += held * quote[1]
        return total

    saver_end = bank[saver_account][-1][0] if saver_account and bank.get(saver_account) else None

    def cash_at(day: str) -> Dict[str, float]:
        balances = {}
        for account, series in bank.items():
            value = _balance_at(series, day)
            if value is not None:
                balances[account] = value
        for record in cash_records:
            active = _active_step(record["steps"], day)
            if not active:
                continue
            if saver_end is None:
                balances["Saver (Investment project)" if record["name"].lower() == "cash" else record["name"]] = active[1]
            elif active[0] > saver_end:
                # A balance recorded after the last saver statement replaces it.
                balances[saver_account] = active[1]
        return balances

    def purchases_between(start: str, end: str) -> float:
        """AUD spent on units bought (negative when sold) after start up to end, at the close on the trade date."""
        total = 0.0
        for position in positions:
            for step_day, _ in position["steps"]:
                if start < step_day <= end:
                    before = units(position, (date.fromisoformat(step_day) - timedelta(days=1)).isoformat())
                    change = units(position, step_day) - before
                    quote = price_aud(position, step_day)
                    if change and quote:
                        total += change * quote[1]
        return total

    months = []
    previous = None
    for day in dates:
        cash = cash_at(day)
        invested = investments_at(day)
        row = {
            "date": day,
            "cash": round(sum(cash.values()), 2),
            "investments": round(invested, 2),
            "netWorth": round(sum(cash.values()) + invested, 2),
            "cashAccounts": {name: round(value, 2) for name, value in cash.items()},
        }
        if previous:
            returns = invested - previous["investments"] - purchases_between(previous["date"], day)
            row["returns"] = round(returns, 2)
            row["savings"] = round(row["netWorth"] - previous["netWorth"] - returns, 2)
        months.append(row)
        previous = row

    years = {}
    for row in months:
        year = row["date"][:4]
        prior = [item for item in months if item["date"] < f"{year}-01-01"]
        start = prior[-1] if prior else months[0]
        in_year = [item for item in months if item["date"][:4] == year and item["date"] > start["date"]]
        years[year] = {
            "startDate": start["date"],
            "start": start["netWorth"],
            "endDate": row["date"],
            "end": row["netWorth"],
            "savings": round(sum(item.get("savings", 0) for item in in_year), 2),
            "returns": round(sum(item.get("returns", 0) for item in in_year), 2),
            "partialStart": not prior,
        }

    holding_rows = []
    for position in positions:
        held = units(position, today)
        if not held:
            continue
        quote = price_aud(position, today)
        native = _value_on_or_before(prices[position["symbol"]]["closes"], today)
        if not quote or not native:
            continue
        holding_rows.append({
            "name": position["name"],
            "symbol": position["symbol"],
            "account": position["account"],
            "currency": position["currency"],
            "units": round(held, 4),
            "price": round(native[1], 4),
            "priceDate": native[0],
            "valueAUD": round(held * quote[1], 2),
        })
        if (date.fromisoformat(today) - date.fromisoformat(native[0])).days > 7:
            problems.append(f"{position['symbol']}: latest price is from {native[0]}")
    holding_rows.sort(key=lambda row: -row["valueAUD"])

    latest_fx = {currency: _value_on_or_before(series, today) for currency, series in fx.items() if series}
    return {
        "available": True,
        "asOf": today,
        "months": months,
        "years": years,
        "holdings": holding_rows,
        "fx": {currency: {"date": value[0], "audRate": round(value[1], 6)} for currency, value in latest_fx.items() if value},
        "holdingsAsOf": max((step[0] for item in holdings["positions"] + cash_records for step in item["steps"]), default=None),
        "problems": problems,
    }


def net_worth_for_dashboard(
    investment_dir: Optional[Path],
    transactions: pd.DataFrame,
    statements: List[Dict[str, object]],
    cache_path: Optional[Path],
    today: Optional[str] = None,
    fetch: Callable[[str, str], Dict[str, object]] = fetch_yahoo,
    saver_account: Optional[str] = None,
) -> Dict[str, object]:
    """Everything the Net worth section needs; never raises for missing data."""
    today = today or date.today().isoformat()
    problems: List[str] = []
    holdings = None
    if investment_dir:
        try:
            holdings = load_holdings(Path(investment_dir))
        except (OSError, ValueError, KeyError) as error:
            problems.append(f"Investment holdings not loaded from {investment_dir}: {error}")
    symbols = sorted({p["symbol"] for p in (holdings or {}).get("positions", [])})
    # CNY is always fetched: the page's AUD/CNY switch converts at this rate.
    currencies = {p["currency"] for p in (holdings or {}).get("positions", [])} | {"CNY"}
    symbols += [FX_SYMBOLS[c] for c in sorted(currencies) if c in FX_SYMBOLS]
    prices, price_problems = load_prices(symbols, cache_path, fetch)
    return build_net_worth(holdings, prices, bank_balances(transactions, statements), today, problems + price_problems, saver_account)
