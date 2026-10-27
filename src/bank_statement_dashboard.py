import argparse
import hashlib
import html
import json
import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import fitz
import pandas as pd
from plotly.offline import get_plotlyjs

from net_worth import net_worth_for_dashboard


MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

EXPECTED_COLUMNS = [
    "transaction_id",
    "source_file",
    "source_page",
    "statement_page",
    "statement_start",
    "statement_end",
    "date",
    "raw_description",
    "merchant",
    "debit",
    "credit",
    "signed_amount",
    "amount",
    "kind",
    "category",
    "subcategory",
    "category_source",
    "rule_id",
    "confidence",
    "review_status",
    "quality_flag",
]

CATEGORY_RULES = [
    ("groceries_supermarket", "Groceries", "Supermarket", [r"\bsupermarket\b", r"\bgrocer(?:y|ies)?\b", r"\baldi\b", r"\bcoles\b", r"\bwoolworths\b"]),
    ("groceries_fresh_food", "Groceries", "Fresh food", [r"\bbutcher\b", r"\bfish market\b", r"\bseafoods?\b", r"\bfruit\b", r"\bvegies?\b", r"\bvegetables?\b", r"\bpoultry\b"]),
    ("groceries_specialty", "Groceries", "Specialty grocery", [r"\basian super\b", r"\basian g\b", r"\bfood store\b", r"\bsmallgoods\b"]),
    ("dining_delivery", "Dining & Takeaway", "Food delivery", [r"\beasi(?: au)?\b", r"\bdoordash\b"]),
    ("dining_fast_food", "Dining & Takeaway", "Fast food", [r"\bmcdonalds?\b", r"\bkfc\b", r"\bhungry jacks?\b", r"\bhjs\b", r"\bkebabs?\b", r"\bpizza\b"]),
    ("dining_restaurant", "Dining & Takeaway", "Restaurant", [r"\brestaurant\b", r"\bbbq\b", r"\bsushi\b", r"\bnoodle\b", r"\bkitchen\b", r"\bbuffe(?:t)?\b", r"\bpizzeria\b", r"\bgrill\b", r"\bfood republic\b", r"\bhaidi(?:lao)?\b", r"\bjapanese\b", r"\bsichuan\b", r"\byunnan\b", r"\bramen\b", r"\bdumpling\b", r"\bpho\b", r"\bpepperlunch\b"]),
    ("dining_named_eatery", "Dining & Takeaway", "Restaurant and takeaway", [r"\broast(?:ed)? chicken\b", r"\bcrepes\b", r"\bsatay\b", r"\bsoul origin\b", r"\bdominos\b"]),
    ("cafes_drinks", "Cafes & Drinks", "Cafe and drinks", [r"\bcafe\b", r"\bcoffee\b", r"\btea\b", r"\bbakery\b", r"\bbreadtop\b", r"\bdessert\b", r"\byo chi\b", r"\bmocha\b"]),
    ("cafes_sweets", "Cafes & Drinks", "Dessert and drinks", [r"\bhokkaido baked chees\b", r"\bkoko black\b", r"\bmessina\b", r"\bcoca cola\b", r"\bcocacola\b"]),
    ("transport_public", "Transport", "Public transport", [r"\bmyki\b", r"\bdepartment of transport\b", r"\bpublic transport\b", r"\btram\b", r"\btrain\b"]),
    ("transport_parking", "Transport", "Parking", [r"\bparking\b"]),
    ("transport_vehicle", "Transport", "Vehicle and licensing", [r"\bvic roads\b", r"\bvicroads\b", r"\btaxi\b", r"\buber\b", r"\bdidi(?:mobility)?\b"]),
    ("transport_coach", "Transport", "Airport coach", [r"\bskybus\b"]),
    ("transport_fuel", "Transport", "Fuel", [r"\bshell\b", r"\bservice station\b", r"\bpetrol\b"]),
    ("health_pharmacy", "Health", "Pharmacy", [r"\bchemist\b", r"\bpharmacy\b", r"\bcwh\b", r"\bamcal\b"]),
    ("health_medical", "Health", "Medical", [r"\bmedical\b", r"\bdental\b", r"\bdoctor\b", r"\bgp\b"]),
    ("retail_clothing", "General Retail", "Clothing", [r"\blululemon\b", r"\buniqlo\b", r"\bshein\b"]),
    ("retail_beauty", "General Retail", "Beauty", [r"\bkiehls\b", r"\bmecca\b"]),
    ("retail_department", "General Retail", "Department store", [r"\bkmart\b", r"\btarget\b", r"\bmyer\b", r"\bdavid jones\b", r"\bbig w\b"]),
    ("retail_home", "General Retail", "Home and hardware", [r"\bbunnings\b"]),
    ("retail_general", "General Retail", "General retail", [r"\bretail\b", r"\bpop mart\b", r"\bminiso\b", r"\bafterpay\b"]),
    ("retail_gift_cards", "General Retail", "Gift cards", [r"\bgift cards?\b", r"\bshopback gift\b"]),
    ("services_repairs", "Services", "Repairs and maintenance", []),
    ("pets", "Pets", "Pet supplies", [r"\bpetbarn\b", r"\bpet\b"]),
    ("entertainment", "Entertainment", "Cinema and recreation", [r"\bhoyts\b", r"\bcinema\b", r"\bmovie\b", r"\bnetflix\b", r"\bspotify\b", r"\bgaming\b", r"\bboard game\b", r"\bkaraoke\b", r"\bmuseums? board\b"]),
    ("housing_rent", "Housing & Rent", "Rent", [r"\brent\b", r"\breal es(?:tate)?\b"]),
    ("utilities", "Utilities", "Household utilities", [r"\belectricity\b", r"\bgas\b", r"\bwater\b", r"\binternet\b", r"\btelstra\b", r"\boptus\b"]),
    ("government_fees", "Government & Fees", "Government and council", [r"\bcouncil\b", r"\baustralia post\b", r"\bpost melb\b"]),
    ("cash_atm", "Cash & ATM", "Cash withdrawal", [r"\batm\b", r"\bcash withdrawal\b"]),
]

STATEMENT_PASSWORD_FILE = "statement_password.txt"

# The dashboard embeds the whole ledger, so every place that serves it (the local
# editing server and the Cloudflare `_headers` file) sends these headers: never
# cache or index the page, never send it as a referrer, never frame it, and only
# let it talk back to its own origin.
DASHBOARD_CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'none'",
        "script-src 'unsafe-inline' 'unsafe-eval'",
        "style-src 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src data:",
        "connect-src 'self'",
        "worker-src blob:",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    ]
)
DASHBOARD_SECURITY_HEADERS = {
    "Cache-Control": "no-store, private",
    "Content-Security-Policy": DASHBOARD_CONTENT_SECURITY_POLICY,
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}


def normalize_merchant(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()
    return re.sub(r"\s+", " ", normalized)


def load_known_categories(path: Path) -> Dict[str, object]:
    if not path.exists():
        return {"version": 2, "manual_overrides": {}, "aliases": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid category configuration: {path}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Category configuration must be a JSON object: {path}")

    if data.get("version") == 2:
        data.setdefault("manual_overrides", {})
        data.setdefault("aliases", [])
        data.setdefault("rule_patterns", {})
        return data

    # Legacy flat maps remain readable, but are treated as reviewable overrides.
    return {
        "version": 2,
        "manual_overrides": {
            normalize_merchant(key): {"category": str(value), "subcategory": "Legacy override"}
            for key, value in data.items()
        },
        "aliases": [],
    }


def _parse_money(value: str) -> Optional[float]:
    cleaned = str(value).replace(",", "").strip()
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", cleaned):
        return None
    return float(cleaned)


def _group_word_rows(words: Iterable[Tuple]) -> List[Tuple[float, List[Tuple[float, str]]]]:
    positioned = sorted((float(word[1]), float(word[0]), str(word[4])) for word in words)
    rows: List[Tuple[float, List[Tuple[float, str]]]] = []
    for y, x, text_value in positioned:
        if rows and abs(rows[-1][0] - y) <= 1.2:
            rows[-1][1].append((x, text_value))
        else:
            rows.append((y, [(x, text_value)]))
    for _, items in rows:
        items.sort(key=lambda item: item[0])
    return rows


def _row_text(items: List[Tuple[float, str]], start_x: float = 0, end_x: float = 10_000) -> str:
    return " ".join(text_value for x, text_value in items if start_x <= x < end_x).strip()


def _column_money(items: List[Tuple[float, str]], start_x: float, end_x: float) -> Optional[float]:
    candidates = [_parse_money(text_value) for x, text_value in items if start_x <= x < end_x]
    values = [value for value in candidates if value is not None]
    return values[-1] if values else None


def _is_hsbc_transaction_row(date_cell: str, date_match: Optional[re.Match[str]]) -> bool:
    """Reject account-summary rows that place an account number in the debit column.

    HSBC's zero-activity statements repeat account headers inside the transaction
    area. Those headers have a non-date value in the date column and an account
    number where the parser normally expects a debit. Real transaction rows either
    carry a date or leave the date column blank while continuing the current date.
    """
    return not date_cell.strip() or date_match is not None


def _extract_statement_period(text: str) -> Optional[Tuple[date, date]]:
    match = re.search(
        r"STATEMENT\s+PERIOD(?:\s+FROM)?\s+(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})\s+TO\s+(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})",
        text,
        re.I,
    )
    if not match:
        return None
    return (
        datetime.strptime(match.group(1).title(), "%d %b %Y").date(),
        datetime.strptime(match.group(2).title(), "%d %b %Y").date(),
    )


def _extract_ing_statement_period(text: str) -> Optional[Tuple[date, date]]:
    """Read the start/end fields used by ING interim statements."""
    match = re.search(
        r"Statement\s+Start\s+Date\s+(\d{1,2}\s+[A-Za-z]{3}\s+\d{4}).*?"
        r"Statement\s+End\s+Date\s+(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})",
        text,
        re.I | re.S,
    )
    if not match:
        return None
    return (
        datetime.strptime(match.group(1).title(), "%d %b %Y").date(),
        datetime.strptime(match.group(2).title(), "%d %b %Y").date(),
    )


def _parse_ing_money(value: str) -> Optional[float]:
    """Parse ING's currency cells, which include `$` and may include a minus sign."""
    cleaned = str(value).replace(",", "").replace("$", "").strip()
    if not re.fullmatch(r"-?\d+(?:\.\d{1,2})?", cleaned):
        return None
    return float(cleaned)


def _ing_column_money(items: List[Tuple[float, str]], start_x: float, end_x: float) -> Optional[float]:
    values = [_parse_ing_money(text_value) for x, text_value in items if start_x <= x < end_x]
    parsed = [value for value in values if value is not None]
    return parsed[-1] if parsed else None


def _date_within_statement(day: int, month_name: str, period_start: date, period_end: date) -> date:
    month = MONTHS[month_name.lower()]
    candidates = [date(year, month, day) for year in sorted({period_start.year, period_end.year})]
    valid = [candidate for candidate in candidates if period_start <= candidate <= period_end]
    if len(valid) != 1:
        raise ValueError(
            f"Could not resolve {day} {month_name} inside statement period {period_start} to {period_end}"
        )
    return valid[0]


def _clean_merchant(raw_description: str) -> str:
    raw = re.sub(r"\s+", " ", raw_description).strip()
    lower = raw.lower()

    if "cashback" in lower:
        match = re.search(r"\d{1,2}:\d{2}:\d{2}\s+(.+?)\s+2%\s+Cashback", raw, re.I)
        return match.group(1).strip(" -") if match else "Cashback"

    if re.search(r"\brent\b", raw, re.I) and raw.upper().startswith("RTP"):
        return "Rent payment"

    if raw.upper().startswith("RTP"):
        return "Bank transfer"

    if raw.upper().startswith(("TRANSFER TO", "TRANSFER FROM")):
        return "Bank transfer"

    if re.match(r"^EFTPOS\s+VISA\s+AUD\s+", raw, re.I):
        body = re.sub(r"^EFTPOS\s+VISA\s+AUD\s+", "", raw, flags=re.I)
        tokens = body.split()
        merchant_tokens: List[str] = []
        for token in tokens:
            if re.search(r"\d{5,}", token):
                break
            merchant_tokens.append(token)
        merchant = " ".join(merchant_tokens).strip(" -")
        return merchant or "Unknown merchant"

    body = re.sub(r"^(?:TEN|TAN)\d+\s+", "", raw, flags=re.I)
    body = re.sub(r"\s+\d{6,}(?:\s.*)?$", "", body)
    body = re.sub(r"^\d{1,2}[A-Z]{3}\d{2}\s+", "", body, flags=re.I)
    return body.strip(" -") or "Unknown merchant"


def _looks_like_named_transfer(raw_description: str) -> bool:
    if raw_description.upper().startswith("EFTPOS"):
        return False
    return bool(
        re.fullmatch(r"[A-Za-z][A-Za-z .'-]+\s+\d{6,}(?:\s+.*)?", raw_description.strip())
    )


def _rule_patterns(known_map: Optional[Dict[str, object]], rule_id: str, patterns: List[str]) -> List[str]:
    """Built-in patterns for a category rule plus any added in the local category file.

    Merchant names that only make sense for one person (a local cafe, a landlord's
    agency) belong in the git-ignored `merchant_categories.json` under
    `rule_patterns`, keyed by rule id, rather than in this file.
    """
    extra = known_map.get("rule_patterns", {}) if isinstance(known_map, dict) else {}
    local = extra.get(rule_id, []) if isinstance(extra, dict) else []
    if not isinstance(local, list):
        return list(patterns)
    return list(patterns) + [str(pattern) for pattern in local if pattern]


def _infer_kind(raw_description: str, debit: float, credit: float, known_map: Optional[Dict[str, object]] = None) -> str:
    lower = raw_description.lower()
    is_transfer = raw_description.upper().startswith(("RTP", "TAN", "TEN", "TRANSFER TO", "TRANSFER FROM")) or _looks_like_named_transfer(raw_description)
    housing_patterns = next(patterns for rule_id, _, _, patterns in CATEGORY_RULES if rule_id == "housing_rent")
    is_housing = any(re.search(pattern, lower) for pattern in _rule_patterns(known_map, "housing_rent", housing_patterns))

    if credit > 0:
        if "cashback" in lower:
            return "cashback"
        if raw_description.upper().startswith("EFTPOS"):
            return "refund"
        if is_transfer:
            return "incoming_transfer"
        return "income"
    if debit > 0:
        if is_housing:
            return "expense"
        if is_transfer:
            return "outgoing_transfer"
        return "expense"
    return "unknown"


def _infer_ing_kind(raw_description: str, debit: float, credit: float) -> str:
    """Infer ING transaction direction without treating merchant references as transfers."""
    upper = raw_description.upper()
    if credit > 0:
        if upper.startswith("TRANSFER FROM"):
            return "incoming_transfer"
        return "income"
    if debit > 0:
        if upper.startswith("TRANSFER TO"):
            return "outgoing_transfer"
        return "expense"
    return "unknown"


def _phrase_matches(normalized_text: str, phrase: str) -> bool:
    normalized_phrase = normalize_merchant(phrase)
    return bool(re.search(rf"(?:^|\s){re.escape(normalized_phrase)}(?:$|\s)", normalized_text))


def infer_category(
    merchant: str,
    amount: float,
    raw_text: str,
    known_map: Dict[str, object],
    kind: str = "expense",
) -> Dict[str, object]:
    if kind in {"incoming_transfer", "outgoing_transfer"}:
        direction = "Incoming transfer" if kind == "incoming_transfer" else "Outgoing transfer"
        return {
            "merchant": merchant,
            "category": "Transfers",
            "subcategory": direction,
            "category_source": "system",
            "rule_id": "transaction_direction",
            "confidence": 1.0,
            "review_status": "auto",
        }
    if kind == "cashback":
        return {
            "merchant": merchant,
            "category": "Income",
            "subcategory": "Cashback",
            "category_source": "system",
            "rule_id": "credit_cashback",
            "confidence": 1.0,
            "review_status": "auto",
        }
    if kind == "income":
        return {
            "merchant": merchant,
            "category": "Income",
            "subcategory": "Other income",
            "category_source": "system",
            "rule_id": "credit_income",
            "confidence": 0.8,
            "review_status": "review",
        }

    merchant_key = normalize_merchant(merchant)
    manual = known_map.get("manual_overrides", {}) if isinstance(known_map, dict) else {}
    override = manual.get(merchant_key) if isinstance(manual, dict) else None
    if isinstance(override, str):
        override = {"category": override}
    if isinstance(override, dict):
        return {
            "merchant": str(override.get("canonical_merchant") or merchant),
            "category": str(override.get("category") or "Uncategorised"),
            "subcategory": str(override.get("subcategory") or "Manual override"),
            "category_source": "manual_override",
            "rule_id": "manual_override",
            "confidence": 1.0,
            "review_status": "reviewed",
        }

    aliases = known_map.get("aliases", []) if isinstance(known_map, dict) else []
    normalized_raw = normalize_merchant(f"{merchant} {raw_text}")
    for alias in aliases if isinstance(aliases, list) else []:
        if not isinstance(alias, dict) or not alias.get("match"):
            continue
        if _phrase_matches(normalized_raw, str(alias["match"])):
            return {
                "merchant": str(alias.get("canonical_merchant") or merchant),
                "category": str(alias.get("category") or "Uncategorised"),
                "subcategory": str(alias.get("subcategory") or "Other"),
                "category_source": "merchant_alias",
                "rule_id": str(alias.get("id") or f"alias_{normalize_merchant(str(alias['match'])).replace(' ', '_')}"),
                "confidence": float(alias.get("confidence", 0.98)),
                "review_status": str(alias.get("review_status") or "auto"),
            }

    for rule_id, category, subcategory, patterns in CATEGORY_RULES:
        if any(re.search(pattern, normalized_raw, re.I) for pattern in _rule_patterns(known_map, rule_id, patterns)):
            return {
                "merchant": merchant,
                "category": category,
                "subcategory": subcategory,
                "category_source": "category_rule",
                "rule_id": rule_id,
                "confidence": 0.9,
                "review_status": "auto",
            }

    return {
        "merchant": merchant,
        "category": "Uncategorised",
        "subcategory": "Needs review",
        "category_source": "unmatched",
        "rule_id": "unmatched",
        "confidence": 0.0,
        "review_status": "review",
    }


def _new_transaction_id(record: Dict[str, object], occurrence: int) -> str:
    identity = "|".join(
        str(record.get(field, ""))
        for field in (
            "statement_start",
            "statement_end",
            "date",
            "raw_description",
            "debit",
            "credit",
        )
    )
    return hashlib.sha256(f"{identity}|{occurrence}".encode("utf-8")).hexdigest()[:20]


def _open_statement_pdf(path: Path) -> fitz.Document:
    """Open a statement, authenticating encrypted PDFs without rewriting them."""
    document = fitz.open(path)
    if not document.needs_pass:
        return document

    password_path = path.parent / STATEMENT_PASSWORD_FILE
    if not password_path.is_file():
        document.close()
        raise ValueError(
            f"{path.name} is password-protected, but {STATEMENT_PASSWORD_FILE} "
            f"was not found beside it"
        )

    password = password_path.read_text(encoding="utf-8").rstrip("\r\n")
    if not password:
        document.close()
        raise ValueError(f"{STATEMENT_PASSWORD_FILE} is empty")
    if document.authenticate(password) <= 0:
        document.close()
        raise ValueError(
            f"Could not unlock {path.name} with the password in {STATEMENT_PASSWORD_FILE}"
        )
    return document


def parse_statement_pdf_with_summary(
    path: Path,
    known_map: Dict[str, object],
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    document = _open_statement_pdf(path)
    try:
        return _parse_open_statement_document(path, known_map, document)
    finally:
        document.close()


def _parse_ing_open_statement_document(
    path: Path,
    known_map: Dict[str, object],
    document: fitz.Document,
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    """Parse ING interim statements with Deposit/Withdrawal/Balance columns."""
    full_text = "\n".join(page.get_text() for page in document)
    period = _extract_ing_statement_period(full_text)
    if period is None:
        raise ValueError(f"Could not read the ING statement dates from {path.name}")

    period_start, period_end = period
    summary: Dict[str, object] = {
        "statement_start": period_start.isoformat(),
        "statement_end": period_end.isoformat(),
        "opening_balance": None,
        "closing_balance": None,
        "printed_debits": None,
        "printed_credits": None,
        "computed_debits": 0.0,
        "computed_credits": 0.0,
        "debit_difference": None,
        "credit_difference": None,
        "balance_difference": None,
        "reconciliation_method": "opening_plus_deposits_minus_withdrawals_equals_closing",
        "reconciled": False,
        "bank": "ING",
    }
    transactions: List[Dict[str, object]] = []

    for page_index, page in enumerate(document):
        rows = _group_word_rows(page.get_text("words"))
        for _, items in rows:
            full_row = _row_text(items)
            lower = full_row.lower()

            if "brought forward" in lower:
                opening = _ing_column_money(items, 490, 580)
                if opening is not None:
                    summary["opening_balance"] = abs(opening)
                continue
            if "closing balance" in lower:
                closing = _ing_column_money(items, 490, 580)
                if closing is not None:
                    summary["closing_balance"] = abs(closing)
                continue

            date_cell = _row_text(items, 30, 130)
            date_match = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})", date_cell)
            if not date_match:
                continue

            transaction_date = datetime.strptime(date_cell.title(), "%d %b %Y").date()
            if not (period_start <= transaction_date <= period_end):
                raise ValueError(
                    f"Transaction date {transaction_date} is outside the ING statement period "
                    f"{period_start} to {period_end} on PDF page {page_index + 1}"
                )

            description = _row_text(items, 130, 345)
            deposit = _ing_column_money(items, 330, 405)
            withdrawal = _ing_column_money(items, 400, 505)
            if not description or (deposit is None and withdrawal is None):
                continue

            credit = round(abs(deposit or 0.0), 2)
            debit = round(abs(withdrawal or 0.0), 2)
            raw_description = re.sub(r"\s+", " ", description).strip()
            merchant = _clean_merchant(raw_description)
            kind = _infer_ing_kind(raw_description, debit, credit)
            category = infer_category(
                merchant,
                max(debit, credit),
                raw_description,
                known_map,
                kind,
            )
            transactions.append(
                {
                    "source_file": path.name,
                    "source_page": page_index + 1,
                    "statement_page": page_index + 1,
                    "statement_start": period_start.isoformat(),
                    "statement_end": period_end.isoformat(),
                    "date": transaction_date.isoformat(),
                    "raw_description": raw_description,
                    "merchant": category["merchant"],
                    "debit": debit,
                    "credit": credit,
                    "signed_amount": round(credit - debit, 2),
                    "amount": round(max(debit, credit), 2),
                    "kind": kind,
                    "category": category["category"],
                    "subcategory": category["subcategory"],
                    "category_source": category["category_source"],
                    "rule_id": category["rule_id"],
                    "confidence": category["confidence"],
                    "review_status": category["review_status"],
                }
            )

    occurrence_counts: Dict[Tuple, int] = defaultdict(int)
    for record in transactions:
        identity = tuple(
            record.get(field)
            for field in ("statement_start", "statement_end", "date", "raw_description", "debit", "credit")
        )
        occurrence_counts[identity] += 1
        record["transaction_id"] = _new_transaction_id(record, occurrence_counts[identity])

    summary["computed_debits"] = round(sum(float(row["debit"]) for row in transactions), 2)
    summary["computed_credits"] = round(sum(float(row["credit"]) for row in transactions), 2)
    if summary["opening_balance"] is not None and summary["closing_balance"] is not None:
        calculated_closing = round(
            float(summary["opening_balance"])
            + float(summary["computed_credits"])
            - float(summary["computed_debits"]),
            2,
        )
        summary["balance_difference"] = round(
            calculated_closing - float(summary["closing_balance"]),
            2,
        )
    summary["reconciled"] = (
        summary["balance_difference"] is not None
        and abs(float(summary["balance_difference"])) <= 0.01
    )
    summary["transaction_count"] = len(transactions)

    for record in transactions:
        flags: List[str] = []
        if not summary["reconciled"]:
            flags.append("statement_unreconciled")
        if record["review_status"] == "review":
            flags.append("needs_category_review")
        record["quality_flag"] = ";".join(flags)

    return transactions, [summary]


BOQ_RECONCILIATION_METHOD = "every_row_balance_and_printed_totals"


def _extract_boq_statement_period(text: str) -> Optional[Tuple[date, date]]:
    """Read BOQ's "26 Aug 2025 - 26 Feb 2026" statement period."""
    match = re.search(
        r"(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})\s+-\s+(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})",
        text,
    )
    if not match:
        return None
    return (
        datetime.strptime(match.group(1).title(), "%d %b %Y").date(),
        datetime.strptime(match.group(2).title(), "%d %b %Y").date(),
    )


def _boq_merchant(raw_description: str) -> str:
    """Name a BOQ transfer by its counterparty: "To: Everyday Account 123456789" -> "Everyday Account"."""
    body = re.sub(r"^(?:To|From):\s*", "", raw_description, flags=re.I)
    body = re.sub(r"\s+(?:NA|\d{3}-\d{3}\s+\d+|\d{6,})(?:\s.*)?$", "", body)
    return body.strip() or raw_description


def _parse_boq_open_statement_document(
    path: Path,
    known_map: Dict[str, object],
    document: fitz.Document,
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    """Parse BOQ savings statements with Debits/Credits/Balance columns.

    Every transaction row prints the running balance, so each row is checked
    against it, and the totals against the account summary on page 1.
    """
    first_page = document[0].get_text()
    period = _extract_boq_statement_period(first_page)
    if period is None:
        raise ValueError(f"Could not read the BOQ statement dates from {path.name}")
    period_start, period_end = period
    money = re.compile(r"\$\s*(-?[\d,]+\.\d{2})")
    totals = re.search(r"Closing balance\s+((?:\$\s*-?[\d,]+\.\d{2}\s+){4})", first_page)
    if not totals:
        raise ValueError(f"Could not read the BOQ account summary from {path.name}")
    opening, printed_credits, printed_debits, closing = (
        float(value.replace(",", "")) for value in money.findall(totals.group(1))
    )
    account_name = re.search(r"\n([^\n]+)\nAccount name", first_page)
    summary: Dict[str, object] = {
        "statement_start": period_start.isoformat(),
        "statement_end": period_end.isoformat(),
        "opening_balance": opening,
        "closing_balance": closing,
        "printed_debits": abs(printed_debits),
        "printed_credits": printed_credits,
        "computed_debits": 0.0,
        "computed_credits": 0.0,
        "debit_difference": None,
        "credit_difference": None,
        "balance_difference": None,
        "reconciliation_method": BOQ_RECONCILIATION_METHOD,
        "reconciled": False,
        "bank": "BOQ",
        "account_name": account_name.group(1).strip() if account_name else "",
    }
    transactions: List[Dict[str, object]] = []
    descriptions: List[List[str]] = []
    row_balances: List[float] = []
    last_row: Optional[Tuple[int, float]] = None  # (page, y) of the last description line

    for page_index, page in enumerate(document):
        for y, items in _group_word_rows(page.get_text("words")):
            date_cell = _row_text(items, 40, 95)
            date_match = re.fullmatch(r"(\d{1,2})-([A-Za-z]{3})", date_cell)
            balance = _ing_column_money(items, 470, 580)
            if date_match and balance is not None:
                transaction_date = _date_within_statement(
                    int(date_match.group(1)), date_match.group(2), period_start, period_end
                )
                debit = abs(_ing_column_money(items, 280, 370) or 0.0)
                credit = abs(_ing_column_money(items, 370, 470) or 0.0)
                transactions.append(
                    {
                        "source_file": path.name,
                        "source_page": page_index + 1,
                        "statement_page": page_index + 1,
                        "statement_start": period_start.isoformat(),
                        "statement_end": period_end.isoformat(),
                        "date": transaction_date.isoformat(),
                        "debit": round(debit, 2),
                        "credit": round(credit, 2),
                    }
                )
                descriptions.append([_row_text(items, 170, 280)])
                row_balances.append(balance)
                last_row = (page_index, y)
                continue
            # Continuation lines sit directly under the transaction (about 11pt
            # apart); anything further down is page furniture or notes.
            if (
                last_row
                and last_row[0] == page_index
                and 0 < y - last_row[1] <= 16
                and items
                and all(170 <= x < 280 for x, _ in items)
            ):
                descriptions[-1].append(_row_text(items, 170, 280))
                last_row = (page_index, y)

    running = opening
    mismatched_rows = 0
    for record, lines, printed_balance in zip(transactions, descriptions, row_balances):
        running = round(running + record["credit"] - record["debit"], 2)
        if abs(running - printed_balance) > 0.01:
            mismatched_rows += 1
        raw_description = re.sub(r"\s+", " ", " ".join(lines)).strip()
        debit, credit = record["debit"], record["credit"]
        upper = raw_description.upper()
        # BOQ shows every incoming payment as "From: ...", so an employer is
        # named in the local rule_patterns["income_salary"] (never in this file).
        is_salary = credit > 0 and any(
            re.search(pattern, raw_description, re.I) for pattern in _rule_patterns(known_map, "income_salary", [])
        )
        if is_salary:
            kind = "income"
        elif credit > 0:
            kind = "incoming_transfer" if upper.startswith("FROM:") else "income"
        else:
            kind = "outgoing_transfer" if upper.startswith("TO:") else "expense"
        category = infer_category(_boq_merchant(raw_description), max(debit, credit), raw_description, known_map, kind)
        if is_salary:
            category.update(category="Income", subcategory="Salary", category_source="local_rule", rule_id="income_salary", confidence=1.0, review_status="auto")
        elif "INTEREST" in upper and kind == "income":
            category.update(category="Income", subcategory="Interest", category_source="system", rule_id="boq_interest", confidence=1.0, review_status="auto")
        record.update(
            raw_description=raw_description,
            merchant=category["merchant"],
            signed_amount=round(credit - debit, 2),
            amount=round(max(debit, credit), 2),
            kind=kind,
            category=category["category"],
            subcategory=category["subcategory"],
            category_source=category["category_source"],
            rule_id=category["rule_id"],
            confidence=category["confidence"],
            review_status=category["review_status"],
        )

    occurrence_counts: Dict[Tuple, int] = defaultdict(int)
    for record in transactions:
        identity = tuple(
            record.get(field)
            for field in ("statement_start", "statement_end", "date", "raw_description", "debit", "credit")
        )
        occurrence_counts[identity] += 1
        record["transaction_id"] = _new_transaction_id(record, occurrence_counts[identity])

    summary["computed_debits"] = round(sum(float(row["debit"]) for row in transactions), 2)
    summary["computed_credits"] = round(sum(float(row["credit"]) for row in transactions), 2)
    summary["debit_difference"] = round(summary["computed_debits"] - summary["printed_debits"], 2)
    summary["credit_difference"] = round(summary["computed_credits"] - summary["printed_credits"], 2)
    summary["balance_difference"] = round(running - closing, 2)
    summary["mismatched_rows"] = mismatched_rows
    summary["reconciled"] = (
        bool(transactions)
        and mismatched_rows == 0
        and all(abs(summary[field]) <= 0.01 for field in ("debit_difference", "credit_difference", "balance_difference"))
    )
    summary["transaction_count"] = len(transactions)

    for record in transactions:
        flags: List[str] = []
        if not summary["reconciled"]:
            flags.append("statement_unreconciled")
        if record["review_status"] == "review":
            flags.append("needs_category_review")
        record["quality_flag"] = ";".join(flags)

    return transactions, [summary]


def _detect_open_document_bank(document: fitz.Document) -> Optional[str]:
    """Return "ing", "hsbc", "boq", or None when the PDF is not a supported statement."""
    first_page_text = document[0].get_text() if len(document) else ""
    if "Bank of Queensland" in first_page_text and "Account summary" in first_page_text:
        return "boq"
    if "ING is a business name" in first_page_text and re.search(
        r"\bDeposit\(\$\).*\bWithdrawal\(\$\).*\bBalance\(\$\)",
        first_page_text,
        re.I | re.S,
    ):
        return "ing"
    for page in document:
        page_text = page.get_text()
        if _extract_statement_period(page_text) is not None:
            return "hsbc"
        # Still claim HSBC-looking PDFs whose dates cannot be read, so a layout
        # change fails loudly instead of being skipped as an unrelated file.
        lower = page_text.lower()
        if "hsbc" in lower and "debits/withdrawals" in lower:
            return "hsbc"
    return None


def detect_statement_bank(path: Path) -> Optional[str]:
    """Identify a statement PDF's bank without parsing its transactions."""
    document = _open_statement_pdf(path)
    try:
        return _detect_open_document_bank(document)
    finally:
        document.close()


def _add_to_summary(summary: Dict[str, object], key: str, value: Optional[float]) -> None:
    """Add one account's printed figure to the statement period's running total."""
    if value is None:
        return
    current = summary.get(key)
    summary[key] = round(float(value) + (float(current) if current is not None else 0.0), 2)


def _parse_open_statement_document(
    path: Path,
    known_map: Dict[str, object],
    document: fitz.Document,
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    bank = _detect_open_document_bank(document)
    if bank == "ing":
        return _parse_ing_open_statement_document(path, known_map, document)
    if bank == "boq":
        return _parse_boq_open_statement_document(path, known_map, document)

    transactions: List[Dict[str, object]] = []
    summaries: Dict[Tuple[date, date], Dict[str, object]] = {}
    current_dates: Dict[Tuple[date, date], date] = {}

    for page_index, page in enumerate(document):
        page_text = page.get_text()
        period = _extract_statement_period(page_text)
        if period is None:
            continue
        period_start, period_end = period
        period_key = (period_start, period_end)
        summary = summaries.setdefault(
            period_key,
            {
                "statement_start": period_start.isoformat(),
                "statement_end": period_end.isoformat(),
                "opening_balance": None,
                "closing_balance": None,
                "printed_debits": None,
                "printed_credits": None,
                "computed_debits": 0.0,
                "computed_credits": 0.0,
                "debit_difference": None,
                "credit_difference": None,
                "balance_difference": None,
                "reconciled": False,
                "bank": "HSBC",
            },
        )

        page_match = re.search(r"Page\s+(\d+)\s+of\s+(\d+)", page_text, re.I)
        statement_page = int(page_match.group(1)) if page_match else None
        rows = _group_word_rows(page.get_text("words"))
        header_index = next(
            (
                index
                for index, (_, items) in enumerate(rows)
                if "date" in _row_text(items).lower()
                and "transaction" in _row_text(items).lower()
                and "debits/withdrawals" in _row_text(items).lower()
            ),
            None,
        )
        if header_index is None:
            continue

        pending: Optional[Dict[str, object]] = None

        def finalize_pending() -> None:
            nonlocal pending
            if pending is None:
                return
            raw_description = re.sub(r"\s+", " ", " ".join(pending.pop("description_parts"))).strip()
            if not raw_description:
                pending = None
                return
            debit = float(pending["debit"])
            credit = float(pending["credit"])
            merchant = _clean_merchant(raw_description)
            kind = _infer_kind(raw_description, debit, credit, known_map)
            category = infer_category(merchant, max(debit, credit), raw_description, known_map, kind)
            pending.update(
                {
                    "raw_description": raw_description,
                    "merchant": category["merchant"],
                    "signed_amount": round(credit - debit, 2),
                    "amount": round(max(debit, credit), 2),
                    "kind": kind,
                    "category": category["category"],
                    "subcategory": category["subcategory"],
                    "category_source": category["category_source"],
                    "rule_id": category["rule_id"],
                    "confidence": category["confidence"],
                    "review_status": category["review_status"],
                }
            )
            transactions.append(pending)
            pending = None

        for _, items in rows[header_index + 1 :]:
            full_text = _row_text(items)
            lower = full_text.lower()
            if "statement period" in lower or lower.startswith("important information"):
                finalize_pending()
                break

            date_cell = _row_text(items, 0, 90)
            date_match = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]{3})", date_cell)
            if date_match:
                current_dates[period_key] = _date_within_statement(
                    int(date_match.group(1)), date_match.group(2), period_start, period_end
                )

            description = _row_text(items, 90, 306)
            debit = _column_money(items, 306, 412)
            credit = _column_money(items, 412, 515)
            balance = _column_money(items, 515, 555)

            # A statement can hold several accounts, each with its own opening
            # balance, closing balance and printed totals, so they add up per period.
            if "balance brought forward" in lower:
                _add_to_summary(summary, "opening_balance", balance)
                continue
            if "closing balance" in lower:
                finalize_pending()
                _add_to_summary(summary, "closing_balance", balance)
                continue
            if lower.startswith("transaction total") or "transaction total" in lower:
                finalize_pending()
                _add_to_summary(summary, "printed_debits", debit)
                _add_to_summary(summary, "printed_credits", credit)
                continue
            if lower.startswith("transaction number") or "end of statement" in lower:
                finalize_pending()
                continue

            if debit is not None or credit is not None:
                if not _is_hsbc_transaction_row(date_cell, date_match):
                    continue
                finalize_pending()
                transaction_date = current_dates.get(period_key)
                if transaction_date is None:
                    raise ValueError(
                        f"Transaction without a date on PDF page {page_index + 1}: {description}"
                    )
                pending = {
                    "source_file": path.name,
                    "source_page": page_index + 1,
                    "statement_page": statement_page,
                    "statement_start": period_start.isoformat(),
                    "statement_end": period_end.isoformat(),
                    "date": transaction_date.isoformat(),
                    "description_parts": [description] if description else [],
                    "debit": round(float(debit or 0.0), 2),
                    "credit": round(float(credit or 0.0), 2),
                }
            elif pending is not None and description:
                pending["description_parts"].append(description)

        finalize_pending()

    occurrence_counts: Dict[Tuple, int] = defaultdict(int)
    for record in transactions:
        identity = tuple(
            record.get(field)
            for field in ("statement_start", "statement_end", "date", "raw_description", "debit", "credit")
        )
        occurrence_counts[identity] += 1
        record["transaction_id"] = _new_transaction_id(record, occurrence_counts[identity])

    for summary in summaries.values():
        statement_rows = [
            row
            for row in transactions
            if row["statement_start"] == summary["statement_start"]
            and row["statement_end"] == summary["statement_end"]
        ]
        computed_debits = round(sum(float(row["debit"]) for row in statement_rows), 2)
        computed_credits = round(sum(float(row["credit"]) for row in statement_rows), 2)
        summary["computed_debits"] = computed_debits
        summary["computed_credits"] = computed_credits

        if summary["printed_debits"] is not None:
            summary["debit_difference"] = round(computed_debits - float(summary["printed_debits"]), 2)
        if summary["printed_credits"] is not None:
            summary["credit_difference"] = round(computed_credits - float(summary["printed_credits"]), 2)
        if summary["opening_balance"] is not None and summary["closing_balance"] is not None:
            calculated_closing = round(
                float(summary["opening_balance"]) + computed_credits - computed_debits,
                2,
            )
            summary["balance_difference"] = round(calculated_closing - float(summary["closing_balance"]), 2)

        checks = [
            summary["debit_difference"],
            summary["credit_difference"],
            summary["balance_difference"],
        ]
        summary["reconciled"] = all(value is not None and abs(float(value)) <= 0.01 for value in checks)
        summary["transaction_count"] = len(statement_rows)

    reconciliation_by_period = {
        (summary["statement_start"], summary["statement_end"]): bool(summary["reconciled"])
        for summary in summaries.values()
    }
    for record in transactions:
        flags: List[str] = []
        period_key = (str(record["statement_start"]), str(record["statement_end"]))
        if not reconciliation_by_period.get(period_key, False):
            flags.append("statement_unreconciled")
        if record["review_status"] == "review":
            flags.append("needs_category_review")
        transaction_date = datetime.strptime(str(record["date"]), "%Y-%m-%d").date()
        statement_end = datetime.strptime(str(record["statement_end"]), "%Y-%m-%d").date()
        if transaction_date > statement_end:
            flags.append("date_outside_statement")
        record["quality_flag"] = ";".join(flags)

    return transactions, sorted(summaries.values(), key=lambda item: item["statement_start"])


def parse_statement_pdf(path: Path, known_map: Dict[str, object]) -> List[Dict[str, object]]:
    records, _ = parse_statement_pdf_with_summary(path, known_map)
    return records


def load_history(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=EXPECTED_COLUMNS)
    df = pd.read_csv(path)
    if df.empty:
        return pd.DataFrame(columns=EXPECTED_COLUMNS)
    return df


def append_history(path: Path, df: pd.DataFrame) -> None:
    if df.empty:
        return
    ordered = df.reindex(columns=EXPECTED_COLUMNS)
    write_header = not path.exists() or path.stat().st_size == 0
    ordered.to_csv(path, mode="a", header=write_header, index=False)


def dashboard_category_options(df: pd.DataFrame) -> List[str]:
    options = {
        str(value).strip()
        for value in df.get("category", pd.Series(dtype=str)).tolist()
        if str(value).strip() and str(value).lower() != "nan"
    }
    options.update(category for _, category, _, _ in CATEGORY_RULES)
    options.update({"Gifts", "Income", "Transfers", "Uncategorised"})
    return sorted(options)


def _write_text_atomically(path: Path, content: str) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(content, encoding="utf-8")
    temporary_path.replace(path)


def _save_history_atomically(path: Path, history: pd.DataFrame) -> None:
    ordered = history.reindex(columns=EXPECTED_COLUMNS)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    ordered.to_csv(temporary_path, index=False)
    temporary_path.replace(path)


def update_transaction_category(
    history_path: Path,
    categories_path: Path,
    reconciliation_path: Path,
    output_path: Path,
    transaction_id: str,
    category: str,
    apply_to_future: bool = False,
    apply_to_merchant: bool = False,
) -> Dict[str, object]:
    """Persist a dashboard category edit and rebuild the dashboard."""
    selected_category = str(category).strip()
    history = load_history(history_path)
    if history.empty or "transaction_id" not in history.columns:
        raise ValueError("The transaction ledger is empty or has no transaction IDs")
    if selected_category not in dashboard_category_options(history):
        raise ValueError(f"Unknown category: {selected_category}")

    transaction_key = str(transaction_id).strip()
    matches = history["transaction_id"].astype(str).eq(transaction_key)
    if int(matches.sum()) != 1:
        raise ValueError(f"Expected exactly one transaction for ID {transaction_key}")

    row_index = history.index[matches][0]
    merchant_value = str(history.at[row_index, "merchant"])
    if pd.isna(history.at[row_index, "merchant"]):
        merchant_value = ""
    kind_value = str(history.at[row_index, "kind"])
    merchant_key = normalize_merchant(merchant_value)
    if apply_to_merchant and not merchant_key:
        raise ValueError("Cannot apply a category to a merchant with no name")
    affected = (
        history.index[history["merchant"].fillna("").map(normalize_merchant).eq(merchant_key)]
        if apply_to_merchant else [row_index]
    )
    for index in affected:
        history.at[index, "category"] = selected_category
        history.at[index, "subcategory"] = "Manual selection"
        history.at[index, "category_source"] = "dashboard_manual"
        history.at[index, "rule_id"] = "dashboard_manual"
        history.at[index, "confidence"] = 1.0
        history.at[index, "review_status"] = "reviewed"
        existing_quality = history.at[index, "quality_flag"]
        quality_flags = [] if pd.isna(existing_quality) else str(existing_quality).split(";")
        history.at[index, "quality_flag"] = ";".join(
            flag for flag in quality_flags if flag and flag != "needs_category_review"
        )

    _save_history_atomically(history_path, history)

    # Saving one transaction is the default. Add a merchant override only when
    # the user explicitly opts in to applying this category to future imports.
    future_rule_saved = bool(apply_to_future and merchant_key and kind_value == "expense")
    if future_rule_saved:
        category_config = load_known_categories(categories_path)
        manual_overrides = category_config.setdefault("manual_overrides", {})
        manual_overrides[merchant_key] = {
            "canonical_merchant": merchant_value,
            "category": selected_category,
            "subcategory": "Manual selection",
        }
        _write_text_atomically(
            categories_path,
            json.dumps(category_config, ensure_ascii=False, indent=2) + "\n",
        )

    reconciliation = load_reconciliation(reconciliation_path)
    build_dashboard(history, output_path, reconciliation)
    return {
        "transaction_id": transaction_key,
        "category": selected_category,
        "merchant": merchant_value,
        "updated_count": len(affected),
        "updated_transaction_ids": history.loc[affected, "transaction_id"].astype(str).tolist(),
        "apply_to_merchant": bool(apply_to_merchant),
        "apply_to_future": future_rule_saved,
    }


def load_reconciliation(path: Path) -> List[Dict[str, object]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid reconciliation file: {path}") from exc
    if not isinstance(data, list):
        raise ValueError(f"Reconciliation file must contain a JSON array: {path}")
    return [item for item in data if isinstance(item, dict)]


def save_reconciliation(path: Path, summaries: List[Dict[str, object]]) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    temporary_path.replace(path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_import_log(path: Path) -> Dict[str, object]:
    if not path.exists():
        return {"version": 1, "files": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid statement import log: {path}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        raise ValueError(f"Invalid statement import log structure: {path}")
    data["version"] = 1
    return data


def save_import_log(path: Path, import_log: Dict[str, object]) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(json.dumps(import_log, indent=2), encoding="utf-8")
    temporary_path.replace(path)


def discover_statement_files(root: Path) -> List[Path]:
    """Return top-level PDFs that may be new statement inputs."""
    return sorted(root.glob("*.pdf"), key=lambda path: path.name.lower())


def statement_periods_from_history(
    history: pd.DataFrame,
    reconciliation: List[Dict[str, object]],
) -> List[Tuple[str, str]]:
    periods = {
        (str(item.get("statement_start", "")), str(item.get("statement_end", "")))
        for item in reconciliation
        if item.get("statement_start") and item.get("statement_end")
    }
    if not history.empty and {"statement_start", "statement_end"}.issubset(history.columns):
        periods.update(
            (str(start), str(end))
            for start, end in zip(history["statement_start"], history["statement_end"])
            if pd.notna(start) and pd.notna(end)
        )
    return sorted(periods)


def trust_existing_combined_pdf(
    root: Path,
    history: pd.DataFrame,
    reconciliation: List[Dict[str, object]],
    import_log: Dict[str, object],
) -> bool:
    """Register the existing combined history without parsing it during migration."""
    files = import_log.setdefault("files", {})
    combined = root / "combined.pdf"
    periods = statement_periods_from_history(history, reconciliation)
    if files or history.empty or not combined.exists() or not periods:
        return False
    digest = file_sha256(combined)
    files[digest] = {
        "source_file": combined.name,
        "status": "trusted_existing_history",
        "imported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "statement_periods": [
            {"statement_start": start, "statement_end": end} for start, end in periods
        ],
        "transaction_count": int(len(history)),
    }
    return True


def deduplicate_statement_summaries(
    summaries: List[Dict[str, object]],
) -> List[Dict[str, object]]:
    """Collapse identical statement periods and reject conflicting duplicates."""
    by_period: Dict[Tuple[str, str], Dict[str, object]] = {}
    comparison_fields = (
        "opening_balance",
        "closing_balance",
        "printed_debits",
        "printed_credits",
        "computed_debits",
        "computed_credits",
        "transaction_count",
        "reconciled",
    )

    for summary in summaries:
        key = (str(summary["statement_start"]), str(summary["statement_end"]))
        existing = by_period.get(key)
        if existing is None:
            by_period[key] = summary
            continue
        if any(existing.get(field) != summary.get(field) for field in comparison_fields):
            raise ValueError(
                "Conflicting PDFs contain different data for statement period "
                f"{key[0]} to {key[1]}"
            )

    return [by_period[key] for key in sorted(by_period)]


def _safe_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


DASHBOARD_TEMPLATE_PATH = Path(__file__).with_name("dashboard_template.html")
ING_RECONCILIATION_METHOD = "opening_plus_deposits_minus_withdrawals_equals_closing"


def statement_account(summary: Dict[str, object]) -> str:
    """Name the account a reconciliation summary belongs to.

    Newer summaries record their bank; older ones are recognised by how they
    were reconciled (only ING statements are reconciled from balances alone).
    """
    bank = str(summary.get("bank") or "").strip()
    if bank:
        return bank
    return "ING" if summary.get("reconciliation_method") == ING_RECONCILIATION_METHOD else "HSBC"


def build_dashboard(
    df: pd.DataFrame,
    output_path: Path,
    reconciliation: Optional[List[Dict[str, object]]] = None,
    net_worth: Optional[Dict[str, object]] = None,
) -> None:
    data = df.reindex(columns=EXPECTED_COLUMNS).copy()
    for column in ("debit", "credit", "signed_amount", "amount", "confidence"):
        data[column] = pd.to_numeric(data[column], errors="coerce").fillna(0.0)
    for column in EXPECTED_COLUMNS:
        if column not in {"debit", "credit", "signed_amount", "amount", "confidence"}:
            data[column] = data[column].astype(object).where(data[column].notna(), "").astype(str)

    reconciliation = reconciliation or []
    statements = [dict(summary, account=statement_account(summary)) for summary in reconciliation]
    # Statement periods are unique across banks (deduplicate_statement_summaries
    # rejects clashes), so a transaction's period identifies its account.
    account_by_period = {
        (str(item.get("statement_start")), str(item.get("statement_end"))): item["account"]
        for item in statements
    }
    data["account"] = [
        account_by_period.get((start, end), "")
        for start, end in zip(data["statement_start"], data["statement_end"])
    ]

    records = data.to_dict(orient="records")
    category_options = dashboard_category_options(data)
    all_reconciled = bool(reconciliation) and all(bool(row.get("reconciled")) for row in reconciliation)
    statement_start = min((str(row.get("statement_start")) for row in reconciliation), default="")
    statement_end = max((str(row.get("statement_end")) for row in reconciliation), default="")
    generated_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    account_names = sorted({item["account"] for item in statements})

    subtitle_parts = []
    if account_names:
        subtitle_parts.append(" and ".join(account_names) if len(account_names) <= 2 else ", ".join(account_names))
    if statement_start and statement_end:
        subtitle_parts.append(f"Statements {statement_start} to {statement_end}")
    subtitle_parts.append(f"Updated {generated_at}")

    page_title = "Bank Statement Dashboard"
    status_label = "All statements reconciled" if all_reconciled else "Reconciliation needs attention"
    values = {
        "PAGE_TITLE": html.escape(page_title),
        "SUBTITLE": html.escape(" · ".join(subtitle_parts)),
        "STATUS_CLASS": "ok" if all_reconciled else "warn",
        "STATUS_LABEL": html.escape(status_label),
        "GENERATED_AT": html.escape(generated_at),
        "TRANSACTIONS_JSON": _safe_json(records),
        "STATEMENTS_JSON": _safe_json(statements),
        "CATEGORY_OPTIONS_JSON": _safe_json(category_options),
        "NET_WORTH_JSON": _safe_json(net_worth or {"available": False, "problems": []}),
        "PLOTLY_JS": get_plotlyjs(),
    }
    template = DASHBOARD_TEMPLATE_PATH.read_text(encoding="utf-8")
    # One pass, so placeholders inside inserted data are never expanded.
    document = re.sub(r"__([A-Z_]+)__", lambda match: values.get(match.group(1), match.group(0)), template)
    document = "\n".join(line.rstrip() for line in document.splitlines()) + "\n"
    output_path.write_text(document, encoding="utf-8")


def load_net_worth_settings(path: Optional[str]) -> Dict[str, object]:
    """Read the git-ignored Net worth settings; a missing file means no settings."""
    if not path or not Path(path).is_file():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Append new validated bank statements and update the dashboard")
    parser.add_argument("--statement-file", nargs="+", default=None, help="One or more PDF statements to process")
    parser.add_argument("--statement-dir", default=".", help="Folder scanned for PDFs when statement files are omitted")
    parser.add_argument("--history-file", default="bank_transactions.csv", help="CSV transaction history")
    parser.add_argument("--categories-file", default="merchant_categories.json", help="Reviewed merchant aliases and overrides")
    parser.add_argument("--reconciliation-file", default="statement_reconciliation.json", help="Statement-level reconciliation output")
    parser.add_argument("--import-log-file", default="statement_import_log.json", help="Registry of previously imported statement PDFs")
    parser.add_argument("--output-html", default="index.html", help="Dashboard HTML output")
    parser.add_argument("--investment-dir", default=None, help="Investment project folder with portfolio/current_portfolio.json, for the Net worth section")
    parser.add_argument("--price-cache-file", default=None, help="Where the last good Yahoo prices are kept for offline builds")
    parser.add_argument("--net-worth-settings", default=None, help="Local JSON settings for the Net worth section, e.g. {\"saver_account\": \"<bank>\"}")
    args = parser.parse_args()

    root = Path(args.statement_dir).resolve()
    history_path = Path(args.history_file).resolve()
    categories_path = Path(args.categories_file).resolve()
    reconciliation_path = Path(args.reconciliation_file).resolve()
    import_log_path = Path(args.import_log_file).resolve()
    output_path = Path(args.output_html).resolve()

    category_config = load_known_categories(categories_path)
    history = load_history(history_path)
    reconciliation = load_reconciliation(reconciliation_path)
    import_log = load_import_log(import_log_path)
    logged_transaction_count = sum(
        int(item.get("transaction_count", 0))
        for item in import_log.get("files", {}).values()
        if isinstance(item, dict)
        and item.get("status") in {"trusted_existing_history", "validated_and_appended"}
    )
    if history.empty and logged_transaction_count:
        raise ValueError(
            "The statement import log contains validated transactions but the CSV is empty. "
            "Restore bank_transactions.csv from backup before importing another statement."
        )
    import_log_changed = trust_existing_combined_pdf(
        root,
        history,
        reconciliation,
        import_log,
    )

    if args.statement_file:
        statement_files = [Path(value).resolve() for value in args.statement_file]
    else:
        statement_files = discover_statement_files(root)

    if not statement_files and history.empty:
        raise FileNotFoundError("No PDF statements or existing transaction history were found")

    logged_files = import_log.setdefault("files", {})
    statement_candidates: List[Tuple[Path, str]] = []
    skipped_files = 0
    for statement_file in statement_files:
        if not statement_file.exists():
            raise FileNotFoundError(statement_file)
        digest = file_sha256(statement_file)
        if digest in logged_files:
            skipped_files += 1
        else:
            statement_candidates.append((statement_file, digest))

    records: List[Dict[str, object]] = []
    new_summaries: List[Dict[str, object]] = []
    existing_periods = set(statement_periods_from_history(history, reconciliation))
    existing_ids = (
        set(history["transaction_id"].dropna().astype(str))
        if not history.empty and "transaction_id" in history.columns
        else set()
    )
    imported_pdf_count = 0
    unrelated_files = 0

    for statement_file, digest in statement_candidates:
        if detect_statement_bank(statement_file) is None:
            # Unrelated PDFs are left alone and not logged, so they are checked
            # again (and skipped again) on the next run.
            print(
                f"Skipped {statement_file.name}: not a recognised bank statement. "
                "Move it out of this folder if it is not a bank statement."
            )
            unrelated_files += 1
            continue
        parsed, parsed_summaries = parse_statement_pdf_with_summary(statement_file, category_config)
        if not parsed_summaries:
            raise ValueError(
                f"No compatible bank statement periods were found in {statement_file.name}"
            )

        unreconciled = [summary for summary in parsed_summaries if not summary.get("reconciled")]
        if unreconciled:
            periods = ", ".join(
                f"{item['statement_start']} to {item['statement_end']}" for item in unreconciled
            )
            raise ValueError(
                f"No data was appended because {statement_file.name} did not reconcile: {periods}"
            )

        parsed_periods = {
            (str(item["statement_start"]), str(item["statement_end"]))
            for item in parsed_summaries
        }
        parsed_ids = {str(item["transaction_id"]) for item in parsed}
        overlap = parsed_periods & existing_periods
        if overlap:
            if parsed_periods.issubset(existing_periods) and parsed_ids.issubset(existing_ids):
                logged_files[digest] = {
                    "source_file": statement_file.name,
                    "status": "duplicate_of_validated_history",
                    "imported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "statement_periods": [
                        {"statement_start": start, "statement_end": end}
                        for start, end in sorted(parsed_periods)
                    ],
                    "transaction_count": len(parsed),
                }
                import_log_changed = True
                skipped_files += 1
                continue
            overlap_text = ", ".join(f"{start} to {end}" for start, end in sorted(overlap))
            raise ValueError(
                "No data was appended because a PDF overlaps validated history but does "
                f"not match it exactly: {statement_file.name} ({overlap_text})"
            )

        duplicate_ids = parsed_ids & existing_ids
        if duplicate_ids:
            raise ValueError(
                f"No data was appended because {statement_file.name} contains "
                "transactions already present in the validated CSV"
            )

        records.extend(parsed)
        new_summaries.extend(parsed_summaries)
        existing_periods.update(parsed_periods)
        existing_ids.update(parsed_ids)
        logged_files[digest] = {
            "source_file": statement_file.name,
            "status": "validated_and_appended",
            "imported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "statement_periods": [
                {"statement_start": start, "statement_end": end}
                for start, end in sorted(parsed_periods)
            ],
            "transaction_count": len(parsed),
        }
        import_log_changed = True
        imported_pdf_count += 1

    new_frame = pd.DataFrame(records, columns=EXPECTED_COLUMNS)
    if not new_frame.empty:
        append_history(history_path, new_frame)
        history = pd.concat([history, new_frame], ignore_index=True)

    summaries = deduplicate_statement_summaries(reconciliation + new_summaries)
    if new_summaries:
        save_reconciliation(reconciliation_path, summaries)
    if import_log_changed:
        save_import_log(import_log_path, import_log)

    dashboard_frame = history.copy()
    if not dashboard_frame.empty:
        dashboard_frame = dashboard_frame.sort_values(
            ["date", "source_page", "transaction_id"], kind="stable"
        )
    net_worth = None
    if args.investment_dir:
        net_worth = net_worth_for_dashboard(
            Path(args.investment_dir).expanduser(),
            dashboard_frame,
            [dict(summary, account=statement_account(summary)) for summary in summaries],
            Path(args.price_cache_file).resolve() if args.price_cache_file else None,
            saver_account=load_net_worth_settings(args.net_worth_settings).get("saver_account"),
        )
        for problem in net_worth.get("problems", []):
            print(f"Net worth: {problem}")
    build_dashboard(dashboard_frame, output_path, summaries, net_worth)

    if imported_pdf_count:
        print(f"Imported {imported_pdf_count} new PDF(s)")
        print(f"Appended {len(new_frame)} transactions")
        print(f"Validated and reconciled {len(new_summaries)} new statement(s)")
    else:
        print("No new statements found; transaction CSV left unchanged")
    print(f"Skipped {skipped_files} previously imported PDF(s)")
    if unrelated_files:
        print(f"Skipped {unrelated_files} PDF(s) that are not bank statements")
    print(f"Validated transaction history: {len(history)} rows")
    print(f"Transaction CSV: {history_path}")
    print(f"Reconciliation file: {reconciliation_path}")
    print(f"Import log: {import_log_path}")
    print(f"Saved dashboard to {output_path}")


if __name__ == "__main__":
    main()
