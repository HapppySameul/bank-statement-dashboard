"""Generate small, fake HSBC and ING statement PDFs for tests.

The PDFs copy the column positions the parser expects from the real layouts but
contain only invented transactions, so the test suite can run anywhere (including
CI) without the private statement history.
"""

from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple

import fitz


HsbcRow = Tuple[str, str, Optional[str], Optional[str]]
IngRow = Tuple[str, str, Optional[str], Optional[str]]


def _writer(page: fitz.Page):
    def text(x: float, y: float, value: str) -> None:
        page.insert_text((x, y), value, fontsize=8)

    return text


def _money(value: float) -> str:
    return f"{value:,.2f}"


def _add_hsbc_page(
    document: fitz.Document,
    period: str,
    opening_balance: float,
    rows: Sequence[HsbcRow],
    misprinted_debits: float = 0.0,
) -> None:
    """Add one statement period.

    Each row is (date, description, debit, credit). An empty date continues the
    previous date; a row with no amounts is a continuation line of the previous
    transaction's description.
    """
    page = document.new_page(width=612, height=792)
    text = _writer(page)
    text(300, 30, f"STATEMENT PERIOD FROM {period}")
    text(500, 45, "Page 1 of 1")
    text(50, 80, "Date")
    text(100, 80, "Transaction Details")
    text(306, 80, "Debits/Withdrawals")
    text(412, 80, "Credits/Deposits")
    text(520, 80, "Balance")

    first_date = rows[0][0] if rows else period.split(" TO ")[0][:6]
    y = 100
    text(46, y, first_date)
    text(98, y, "BALANCE BROUGHT FORWARD")
    text(520, y, _money(opening_balance))

    total_debits = 0.0
    total_credits = 0.0
    for row_date, description, debit, credit in rows:
        y += 16
        if row_date:
            text(46, y, row_date)
        text(98, y, description)
        if debit:
            text(330, y, debit)
            total_debits += float(debit.replace(",", ""))
        if credit:
            text(436, y, credit)
            total_credits += float(credit.replace(",", ""))

    closing_balance = round(opening_balance + total_credits - total_debits, 2)
    y += 16
    text(98, y, "CLOSING BALANCE")
    text(520, y, _money(closing_balance))
    y += 16
    text(98, y, "Transaction Total")
    text(330, y, _money(total_debits + misprinted_debits))
    text(436, y, _money(total_credits))
    y += 16
    text(98, y, "Transaction Number")
    text(375, y, str(sum(1 for row in rows if row[2] or row[3])))


def write_hsbc_statement(
    path: Path,
    periods: Iterable[Tuple[str, float, Sequence[HsbcRow]]],
    misprinted_debits: float = 0.0,
) -> Path:
    """Write HSBC periods; `misprinted_debits` makes the printed totals disagree."""
    document = fitz.open()
    for period, opening_balance, rows in periods:
        _add_hsbc_page(document, period, opening_balance, rows, misprinted_debits)
    document.save(path)
    document.close()
    return path


def write_ing_statement(
    path: Path,
    start: str,
    end: str,
    opening_balance: float,
    rows: Sequence[IngRow],
) -> Path:
    """Write an ING interim statement. Each row is (date, description, deposit, withdrawal)."""
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    text = _writer(page)
    text(40, 30, "ING is a business name of ING Bank (Australia) Limited")
    text(40, 45, f"Statement Start Date {start}")
    text(40, 60, f"Statement End Date {end}")
    text(40, 90, "Date")
    text(135, 90, "Description")
    text(340, 90, "Deposit($)")
    text(420, 90, "Withdrawal($)")
    text(520, 90, "Balance($)")

    y = 110
    text(135, y, "Opening balance brought forward")
    text(520, y, f"${_money(opening_balance)}")

    balance = opening_balance
    for row_date, description, deposit, withdrawal in rows:
        y += 16
        text(40, y, row_date)
        text(135, y, description)
        if deposit:
            text(345, y, f"${deposit}")
            balance += float(deposit.replace(",", ""))
        if withdrawal:
            text(425, y, f"-${withdrawal}")
            balance -= float(withdrawal.replace(",", ""))
        text(520, y, f"${_money(balance)}")

    y += 16
    text(135, y, "Closing balance")
    text(520, y, f"${_money(round(balance, 2))}")
    document.save(path)
    document.close()
    return path


# Two HSBC statement periods spanning a new year, with a reference number in a
# continuation line, two identical purchases on one day, transfers and cashback.
SAMPLE_HSBC_PERIODS = [
    (
        "15 Dec 2025 TO 14 Jan 2026",
        1000.00,
        [
            ("20 Dec", "EFTPOS VISA AUD COSTCO SAMPLEVILLE 74912345", "120.95", None),
            ("", "Ref 1111", None, None),
            ("24 Dec", "EFTPOS VISA AUD MCDONALDS SAMPLEVILLE 55512345", "1.49", None),
            ("", "EFTPOS VISA AUD MCDONALDS SAMPLEVILLE 55512345", "1.49", None),
            ("28 Dec", "TRANSFER TO 063000-12345678", "2,000.00", None),
            ("02 Jan", "RTP 063000 87654321 FROM SAVINGS", None, "2,000.00"),
            ("05 Jan", "02JAN26 10:15:00 COSTCO SAMPLEVILLE 2% Cashback", None, "2.42"),
            ("10 Jan", "EFTPOS VISA AUD MYSTERY SHOP 77712345", "12.00", None),
        ],
    ),
    (
        "15 Jan 2026 TO 14 Feb 2026",
        866.49,
        [
            ("20 Jan", "EFTPOS VISA AUD WOOLWORTHS 1234 MELBOURNE 88812345", "56.30", None),
            ("01 Feb", "RTP 063000 11112222 RENT FEB", "1,200.00", None),
            ("", "Ref 2222", None, None),
            ("03 Feb", "RTP 063000 33334444 PAYROLL", None, "3,000.00"),
        ],
    ),
]

SAMPLE_ING_ROWS = [
    ("03 Mar 2026", "Transfer from Orange Everyday", "1,500.00", None),
    ("05 Mar 2026", "Woolworths Sampleville", None, "45.20"),
    ("09 Mar 2026", "Transfer to HSBC Everyday", None, "500.00"),
    ("15 Mar 2026", "Salary ACME PTY LTD", "2,000.00", None),
]


def write_sample_hsbc_statement(path: Path) -> Path:
    return write_hsbc_statement(path, SAMPLE_HSBC_PERIODS)


def write_sample_ing_statement(path: Path) -> Path:
    return write_ing_statement(path, "01 Mar 2026", "31 Mar 2026", 250.00, SAMPLE_ING_ROWS)


def write_unrelated_pdf(path: Path) -> Path:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Tax invoice: 1 x coffee grinder, total $89.00")
    document.save(path)
    document.close()
    return path
