"""Read the residential gas-cost history from an Atmos monthly rate PDF.

Every region publishes the same kind of table: one row per month (labelled "Oct-26" or
"October-26"), one column per customer class, rates in $/Ccf. The layouts differ (column names,
several tables per page, newest or oldest month first), so the parser works from word positions:

1. find the column header (regex + occurrence from the region's GasCostSource),
2. take the month labels below it in the nearest label column to its left,
3. on each month's row, take the number closest to the header horizontally.

No Home Assistant imports: tests load this module directly.
"""
from __future__ import annotations

import re
from io import BytesIO
from typing import Any

MONTHS = {
    m: i
    for i, names in enumerate(
        [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
         ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
         ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"), ("dec", "december")],
        start=1,
    )
    for m in names
}
MONTH_LABEL = re.compile(r"^([A-Za-z]{3,9})[-\s]?'?(\d{2}|\d{4})$")
NUMBER = re.compile(r"^\(?-?\$?\d*\.\d+\)?$")

ROW_TOLERANCE = 3.0  # points: words on the same row
MAX_COLUMN_DISTANCE = 60.0  # points: how far a value may sit from its header's centre
MIN_RATE, MAX_RATE = -1.0, 5.0  # $/Ccf sanity range


def parse_month_label(text: str) -> tuple[int, int] | None:
    """'Oct-26' / 'October-26' / 'Oct-2026' -> (2026, 10)."""
    match = MONTH_LABEL.match(text.strip())
    if not match:
        return None
    month = MONTHS.get(match.group(1).lower())
    if month is None:
        return None
    year = int(match.group(2))
    return (year + 2000 if year < 100 else year, month)


def parse_number(text: str) -> float | None:
    """'0.58151', '$0.50254', '-0.00820', '(0.0206)' -> float."""
    text = text.strip()
    if not NUMBER.match(text):
        return None
    negative = text.startswith("(") and text.endswith(")")
    value = float(text.strip("()").replace("$", ""))
    return -value if negative else value


def _merge_split_numbers(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join numbers the PDF splits into touching pieces, e.g. '0' + '.1274' or '(' + '0.0260)'."""
    words = sorted(words, key=lambda w: (round(w["top"]), w["x0"]))
    merged: list[dict[str, Any]] = []
    for word in words:
        prev = merged[-1] if merged else None
        if (
            prev is not None
            and abs(prev["top"] - word["top"]) <= ROW_TOLERANCE
            and 0 <= word["x0"] - prev["x1"] <= 2.5
            and parse_number(prev["text"]) is None
            and parse_number(prev["text"] + word["text"]) is not None
        ):
            merged[-1] = {**prev, "text": prev["text"] + word["text"], "x1": word["x1"]}
        else:
            merged.append(dict(word))
    return merged


def _centre(word: dict[str, Any]) -> float:
    return (word["x0"] + word["x1"]) / 2


def extract_rates(words: list[dict[str, Any]], column: str, occurrence: int = 0) -> dict[tuple[int, int], float]:
    """Map (year, month) -> rate for one column of one page's words (pdfplumber word dicts)."""
    words = _merge_split_numbers(words)
    pattern = re.compile(column, re.IGNORECASE)
    headers = sorted((w for w in words if pattern.search(w["text"])), key=lambda w: (round(w["top"]), w["x0"]))
    if len(headers) <= occurrence:
        return {}
    header = headers[occurrence]
    header_x = _centre(header)

    labels = [
        (w, parse_month_label(w["text"]))
        for w in words
        if w["top"] > header["top"] and w["x1"] <= header["x0"] + 5
    ]
    labels = [(w, ym) for w, ym in labels if ym]
    if not labels:
        return {}

    # The table's month column is the one nearest the header on its left
    label_x1 = max(w["x1"] for w, _ in labels)
    column_labels = sorted(
        ((w, ym) for w, ym in labels if abs(w["x1"] - label_x1) <= 8),
        key=lambda item: item[0]["top"],
    )

    # Stop at the first big vertical gap: anything after it belongs to another table
    rows = column_labels[:1]
    for item in column_labels[1:]:
        gaps = [b[0]["top"] - a[0]["top"] for a, b in zip(rows, rows[1:])] or [item[0]["top"] - rows[0][0]["top"]]
        if item[0]["top"] - rows[-1][0]["top"] > 2.5 * min(gaps):
            break
        rows.append(item)

    rates: dict[tuple[int, int], float] = {}
    for label, year_month in rows:
        candidates = []
        for w in words:
            if abs(w["top"] - label["top"]) > ROW_TOLERANCE or w["x0"] < label["x1"]:
                continue
            value = parse_number(w["text"])
            if value is None:
                continue
            distance = abs(_centre(w) - header_x)
            if distance <= MAX_COLUMN_DISTANCE:
                candidates.append((distance, value))
        if candidates:
            value = min(candidates)[1]
            if MIN_RATE < value < MAX_RATE:
                rates[year_month] = value
    return rates


def parse_gas_cost_pdf(content: bytes, column: str, page: int = 0, occurrence: int = 0) -> dict[tuple[int, int], float]:
    """Map (year, month) -> residential gas cost in $/Ccf, from a monthly rate PDF."""
    import pdfplumber  # imported late: slow to import, and only needed when a PDF arrives

    with pdfplumber.open(BytesIO(content)) as pdf:
        if page >= len(pdf.pages):
            return {}
        return extract_rates(pdf.pages[page].extract_words(), column, occurrence)
