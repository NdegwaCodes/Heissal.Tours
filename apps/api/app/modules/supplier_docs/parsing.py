"""Pure text-to-value parsing for supplier rate sheets.

Deliberately free of any PDF dependency: these are the functions most likely to
be wrong in a way that quietly changes money, so they are unit-testable against
strings copied verbatim out of the real 2026/27 corpus.

The rule throughout is **return None rather than guess**. A rate the parser
declines to read becomes a row the reviewer fills in; a rate the parser guesses
wrong becomes a price a client is charged.
"""

from __future__ import annotations

import calendar
import re
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation

# --------------------------------------------------------------------------- #
# Money
# --------------------------------------------------------------------------- #

# The corpus uses three separator conventions for the same kind of number:
#   Swahili Beach  "23.920 KES"   dot as a THOUSANDS separator
#   Temple Point   "21,600"       comma as a thousands separator
#   Baobab         "280"          none at all
# Reading "23.920" as 23.92 understates a rate by a factor of a thousand, which
# is exactly the class of error that survives review because it looks plausible.
_MONEY = re.compile(
    r"""
    (?P<sign>-)?
    (?P<num>\d{1,3}(?:[.,]\d{3})+ | \d+(?:[.,]\d{1,2})? )
    """,
    re.VERBOSE,
)
_CURRENCY_TOKENS = {
    "KES": "KES",
    "KSH": "KES",
    "KSHS": "KES",
    "SH": "KES",
    "USD": "USD",
    "US$": "USD",
    "$": "USD",
    "EUR": "EUR",
    "GBP": "GBP",
}


def parse_money(text: str | None) -> Decimal | None:
    """Read one money amount from a cell, or None if there is not exactly one.

    Rejects cells holding several numbers: a merged cell such as "280 370 500"
    is a layout failure, and picking the first number would silently attach the
    single-occupancy price to every occupancy column.
    """
    if not text:
        return None
    cleaned = text.replace("\n", " ").strip()
    if not cleaned:
        return None

    matches = _MONEY.findall(cleaned)
    if len(matches) != 1:
        return None
    sign, num = matches[0]

    # A group of exactly three digits after the separator is a thousands group;
    # one or two digits is a decimal fraction.
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", num):
        num = num.replace(".", "").replace(",", "")
    else:
        num = num.replace(",", ".")

    try:
        value = Decimal(num)
    except InvalidOperation:
        return None
    if sign:
        value = -value
    # A rate sheet never quotes a negative or zero nightly rate; treating one as
    # data would push a nonsense figure into review.
    return value if value > 0 else None


def parse_currency(text: str | None) -> str | None:
    """Read an ISO currency code from free text, if it states one unambiguously."""
    if not text:
        return None
    upper = text.upper()
    found = {
        code
        for token, code in _CURRENCY_TOKENS.items()
        if re.search(rf"(?<![A-Z]){re.escape(token)}(?![A-Z])", upper)
    }
    return found.pop() if len(found) == 1 else None


# --------------------------------------------------------------------------- #
# Meal plans
# --------------------------------------------------------------------------- #

# Left side: what the sheets write. Right side: the seeded MealPlan.code.
# "BO" (bed only) and "RO" (room only) are the same product under two names, and
# four sheets use the former; mapping is a lookup, not a new meal plan.
_MEAL_PLANS = {
    "BO": "RO",
    "RO": "RO",
    "ROOM ONLY": "RO",
    "BED ONLY": "RO",
    "BB": "BB",
    "B&B": "BB",
    "B/B": "BB",
    "BED AND BREAKFAST": "BB",
    "BED & BREAKFAST": "BB",
    "HB": "HB",
    "H/B": "HB",
    "HALF BOARD": "HB",
    "FB": "FB",
    "F/B": "FB",
    "FULL BOARD": "FB",
    "AI": "AI",
    "ALL INCLUSIVE": "AI",
}


def parse_meal_plan(text: str | None) -> str | None:
    """Map a sheet's meal-plan wording onto a seeded meal-plan code."""
    if not text:
        return None
    key = re.sub(r"\s+", " ", text.strip().upper())
    if key in _MEAL_PLANS:
        return _MEAL_PLANS[key]
    # Cells often carry the code plus noise ("meal BB", "FB rate").
    for token in re.findall(r"[A-Z&/]+", key):
        if token in _MEAL_PLANS:
            return _MEAL_PLANS[token]
    return None


# --------------------------------------------------------------------------- #
# Occupancy
# --------------------------------------------------------------------------- #

_OCCUPANCY = {
    "SINGLE": 1,
    "SGL": 1,
    "SINGLE ROOM": 1,
    "DOUBLE": 2,
    "DBL": 2,
    "TWIN": 2,
    "DOUBLE/TWIN": 2,
    "DOUBLE ROOM": 2,
    "TRIPLE": 3,
    "TPL": 3,
    "TRIPLE ROOM": 3,
    "QUAD": 4,
    "QUADRUPLE": 4,
}


def parse_occupancy(text: str | None) -> int | None:
    """Read how many guests a column's price covers.

    Returns None for genuinely ambiguous headers — Swahili Beach prices its club
    rooms "SGL OR DBL", one figure for either occupancy. Choosing one would
    invent a rate, so the reviewer decides.
    """
    if not text:
        return None
    key = re.sub(r"\s+", " ", text.strip().upper())
    if " OR " in key:
        return None
    if key in _OCCUPANCY:
        return _OCCUPANCY[key]
    for token in sorted(_OCCUPANCY, key=len, reverse=True):
        if re.search(rf"(?<![A-Z]){re.escape(token)}(?![A-Z])", key):
            return _OCCUPANCY[token]
    return None


# --------------------------------------------------------------------------- #
# Seasons
# --------------------------------------------------------------------------- #

_SEASONS = ("PEAK", "FESTIVE", "EASTER", "SHOULDER", "HIGH", "LOW", "MID", "GREEN")


def parse_season(text: str | None) -> str | None:
    """Read a season label, keeping the sheet's own vocabulary."""
    if not text:
        return None
    upper = text.upper()
    for name in _SEASONS:
        if re.search(rf"(?<![A-Z]){name}(?![A-Z])", upper):
            return name.title()
    return None


# --------------------------------------------------------------------------- #
# Date ranges
# --------------------------------------------------------------------------- #

_MONTHS = {m.upper(): i for i, m in enumerate(calendar.month_abbr) if m}
_MONTHS |= {m.upper(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS["SEPT"] = 9

# "04/01/2026", "11.01.27", "3-1-2026" — day first, as every sheet in the corpus
# is written, and as Kenyan convention has it.
_NUMERIC = re.compile(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})")
# "03 Jan", "23 Dec '27", "1 Nov", and the ordinal form several sheets write in
# prose ("6th January - 28th February").
# The year is accepted ONLY as four digits or behind an apostrophe. Without that
# restriction the optional group swallows whatever number follows the month, and
# on the Baobab sheet the number following "03 Jan" is the price 280 — which
# parsed as the year 280. A price silently becoming a date is precisely the
# failure this parser exists to avoid.
_NAMED = re.compile(
    r"(\d{1,2})(?:st|nd|rd|th)?\s*([A-Za-z]{3,9})\.?"
    r"(?:\s*(?:'(\d{2})|(\d{4})|(\d{2})(?![\d.,])))?"
)
_SEPARATORS = re.compile(r"\s*(?:-|–|—|to|until|till)\s*", re.IGNORECASE)


def _year(raw: str | None, *, default: int | None) -> int | None:
    if raw is None:
        return default
    value = int(raw)
    if value < 100:
        # Rate sheets run 2025-2028; a two-digit year is this century.
        return 2000 + value
    return value


def _one_date(text: str, *, default_year: int | None) -> tuple[date | None, bool]:
    """Parse one date, and report whether the text stated the year itself.

    Whether a year was explicit decides how a window crossing New Year is
    resolved, so it cannot be thrown away here.
    """
    text = text.strip()
    m = _NUMERIC.search(text)
    if m:
        day, month, raw_year = m.group(1), m.group(2), m.group(3)
        year = _year(raw_year, default=default_year)
        if not year:
            return None, raw_year is not None
        try:
            return date(int(year), int(month), int(day)), raw_year is not None
        except ValueError:
            return None, raw_year is not None
    m = _NAMED.search(text)
    if m:
        day, name = m.group(1), m.group(2).upper()
        # A bare two-digit year after the month ("30 April 26") is accepted only
        # inside a plausible range. Without the range a price would qualify, and
        # the Baobab sheet puts its prices immediately after the season, where
        # "03 Jan - 02 Apr 280" once parsed as the year 280.
        bare = m.group(5)
        if bare is not None and not (24 <= int(bare) <= 35):
            bare = None
        raw_year = m.group(3) or m.group(4) or bare
        month = _MONTHS.get(name) or _MONTHS.get(name[:3])
        year = _year(raw_year, default=default_year)
        if not month or not year:
            return None, raw_year is not None
        try:
            return date(year, month, int(day)), raw_year is not None
        except ValueError:
            return None, raw_year is not None
    return None, False


def parse_date_range(
    text: str | None, *, default_year: int | None = None
) -> tuple[date | None, date | None]:
    """Read a season window such as "04/01/2026 - 02/04/2026".

    Handles the corpus's several notations, including a year stated only on one
    side ("23 Dec - 3 Jan '27") and the wrap into the following January. Returns
    ``(None, None)`` for anything it cannot read confidently — the Swahili Beach
    sheet contains a real typo, "03/04-2026 - 06/04/2026", and a parser that
    tries to be clever about that is a parser that eventually invents a date.
    """
    if not text:
        return None, None
    cleaned = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()

    # Split on the separator, but not on one inside a numeric date ("3-1-2026").
    parts = [p for p in _SEPARATORS.split(cleaned) if p.strip()]
    if len(parts) < 2:
        # Two full numeric dates with no separator left after splitting.
        found = _NUMERIC.findall(cleaned)
        if len(found) == 2:
            parts = [
                f"{d}/{m}/{y}" for d, m, y in found  # noqa: E501
            ]
        else:
            return None, None

    # Prefer the last two fragments that actually contain a date, so a leading
    # label ("HIGH 04/01/2026 - 02/04/2026") does not shift the pairing.
    dated = [p for p in parts if _NUMERIC.search(p) or _NAMED.search(p)]
    if len(dated) < 2:
        return None, None
    left_text, right_text = dated[0], dated[1]

    end, end_had_year = _one_date(right_text, default_year=default_year)
    # A start with no year of its own borrows the end's, which is how
    # "03 Jan - 02 Apr" is meant to be read.
    start, start_had_year = _one_date(
        left_text, default_year=(end.year if end else default_year)
    )
    if start is None or end is None:
        return start, end
    if end >= start:
        return start, end

    # The window crosses New Year, and which side moves depends on which side
    # stated its year. "23 Dec - 3 Jan '27" runs Dec 2026 to Jan 2027: the end is
    # anchored, so the start is the year before. "23/12/2026 - 03/01" is the
    # mirror image: the start is anchored and the end rolls forward. Moving the
    # wrong end of a festive window shifts a rate by a full year.
    try:
        if end_had_year and not start_had_year:
            return start.replace(year=start.year - 1), end
        return start, end.replace(year=end.year + 1)
    except ValueError:
        return start, None


# --------------------------------------------------------------------------- #
# Season definitions stated in prose
# --------------------------------------------------------------------------- #

_SEASON_LINE = re.compile(
    r"(?P<name>PEAK|FESTIVE|EASTER|SHOULDER|HIGH|LOW|MID|GREEN)\s+SEASON(?P<rest>[^\n]*)",
    re.IGNORECASE,
)


def season_windows(
    text: str, *, default_year: int | None = None
) -> dict[str, list[tuple[date, date]]]:
    """Season name to the date windows a document defines for it, in prose.

    Several sheets state their seasons on one page ("High Season 6th January -
    28th February; 3rd April - 6th April") and then label the rate table only
    with the season name. Without this mapping those rates have no dates at all.

    These prose ranges rarely state a year — the sheet's title carries it — so
    ``default_year`` is normally required for anything to parse at all.

    A season may legitimately carry several windows, and they are all returned:
    the caller decides what to do when there is more than one, because silently
    picking the first would attach a rate to part of its real season.
    """
    found: dict[str, list[tuple[date, date]]] = {}
    for match in _SEASON_LINE.finditer(text or ""):
        name = match.group("name").title()
        windows: list[tuple[date, date]] = []
        # Semicolons and "&" separate one season's several windows.
        for chunk in re.split(r";|&|\band\b", match.group("rest")):
            start, end = parse_date_range(chunk, default_year=default_year)
            if start and end:
                windows.append((start, end))
        if windows:
            found.setdefault(name, [])
            for window in windows:
                if window not in found[name]:
                    found[name].append(window)
    return found


_ANY_YEAR = re.compile(r"(?<![0-9])(20[2-3][0-9])(?![0-9])")


def document_year(text: str) -> int | None:
    """The year a sheet is *for*, used when a season window omits it.

    The most frequently named year, not the earliest. Earliest is wrong on real
    documents: the Medina Palms 2026 contract carries a "MAY 2025" revision
    stamp, and the Swahili Beach 2026 contract mentions 2025 twice against 2026
    a hundred and seventeen times. Taking the earliest dated every season in
    those sheets a year early — a silent error, since the dates still look
    entirely plausible. Ties go to the earlier year, because a contract season
    normally starts in the earlier of the two years it spans.
    """
    counts = Counter(_ANY_YEAR.findall(text or ""))
    if not counts:
        return None
    most = max(counts.values())
    return min(int(year) for year, count in counts.items() if count == most)
