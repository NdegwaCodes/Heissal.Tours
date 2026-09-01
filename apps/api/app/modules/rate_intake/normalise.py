"""Turning a filled-in intake workbook into values the catalogue can store.

Pure functions, no I/O, so the decisions that change a price can be tested
against the real corpus. Every mapping here was derived from the workbook the
client actually filled in (3,161 rows, 36 properties), not from what the template
asked for — the difference between those two is the whole job.

The governing rule: **forgiving about how a value is written, strict about
whether it is there.** `B&B` and `BB` are the same meal plan and normalising them
costs nothing. A rate with no validity window is not a rate with a default
window, and guessing one would invent a price the supplier never quoted.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

# --------------------------------------------------------------------------- #
# Meal plans
# --------------------------------------------------------------------------- #

# The codes the corpus uses against the ones the catalogue stores. `BO` is
# "bed only" — the same thing the catalogue calls room only — and `B&B` is how
# most sheets write bed and breakfast.
MEAL_PLAN_SYNONYMS: dict[str, str] = {
    "RO": "RO", "BO": "RO", "ROOM ONLY": "RO", "BED ONLY": "RO",
    "BB": "BB", "B&B": "BB", "B & B": "BB", "BED AND BREAKFAST": "BB",
    "BED & BREAKFAST": "BB",
    "HB": "HB", "HALF BOARD": "HB",
    "FB": "FB", "FULL BOARD": "FB",
    "AI": "AI", "ALL INCLUSIVE": "AI", "ALL-INCLUSIVE": "AI",
}

# --------------------------------------------------------------------------- #
# Residence categories
# --------------------------------------------------------------------------- #

RESIDENCE_SYNONYMS: dict[str, str] = {
    "CITIZEN": "citizen", "KENYAN CITIZEN": "citizen", "KENYAN": "citizen",
    "EA_RESIDENT": "ea_resident", "EA RESIDENT": "ea_resident",
    "EAST AFRICAN CITIZEN": "ea_resident", "EAST AFRICAN RESIDENT": "ea_resident",
    "RESIDENT": "resident", "KENYA RESIDENT": "resident",
    "AFRICAN_CITIZEN": "african_citizen", "AFRICAN CITIZEN": "african_citizen",
    "NON_RESIDENT": "non_resident", "NON RESIDENT": "non_resident",
    "NON-RESIDENT": "non_resident", "NONRESIDENT": "non_resident",
    "INTERNATIONAL": "non_resident",
}

ROW_TYPES = frozenset({"RATE", "SUPPLEMENT", "EXTRA"})


def row_kind(row: object) -> str:
    """What kind of row this is, treating a blank ``row_type`` as ``RATE``.

    A blank first column is the overwhelmingly common typo — an agent fills in
    the price columns and skips the one that never varies — and a row carrying a
    room, a meal plan and an amount is unambiguously a rate. Defaulting is
    therefore safe, but it has to be done in *one* place: the two passes over a
    sheet each decided this for themselves and disagreed, so a whole property's
    rates imported while its room capacities were inferred from nothing.
    """
    if not isinstance(row, dict):
        return key(row) or "RATE"
    return key(row.get("row_type")) or "RATE"

# How the intake's charging vocabulary maps onto what each table stores. Rates
# are always per night in the catalogue, so a per-stay rate is not expressible
# and is refused rather than silently converted.
RATE_BASES = {"room_per_night": "per_room", "person_per_night": "per_person"}
SUPPLEMENT_BASES = {
    "person_per_night": "per_person_per_night",
    "person_per_stay": "per_person",
    "room_per_night": "per_room_per_night",
    "room_per_stay": "per_room",
}


def clean(value: object) -> str:
    """Trim, collapse inner whitespace, and normalise unicode.

    Non-breaking spaces and curly apostrophes arrive from Excel constantly and
    are invisible in a diff; left alone they make "Baobab Beach Resort & Spa"
    and the same name with a nbsp two different properties.
    """
    if value is None:
        return ""
    text = str(value)
    text = unicodedata.normalize("NFKC", text)
    return " ".join(text.split())


def key(value: object) -> str:
    """A case- and punctuation-insensitive lookup key."""
    return clean(value).upper()


def meal_plan_code(value: object) -> str | None:
    return MEAL_PLAN_SYNONYMS.get(key(value))


def residence_key(value: object) -> str | None:
    return RESIDENCE_SYNONYMS.get(key(value).replace("-", "_").replace(" ", "_")) or (
        RESIDENCE_SYNONYMS.get(key(value))
    )


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #

_DMY = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$")
_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")


def date_order(values: list[str]) -> str:
    """Whether a column of ``a/b/yyyy`` dates is day-first or month-first.

    Decided from the file rather than assumed, because the two readings are both
    valid for any date whose components are each 12 or under, and the wrong one
    prices an April stay at March rates without complaining. Only rows where a
    component exceeds 12 carry information; if none do, the file is genuinely
    ambiguous and that is reported instead of guessed.

    The client's workbook answers this decisively: 2,820 of its dates have a
    first component above 12 and none have a second, so it is day-first.
    """
    day_first = month_first = 0
    for value in values:
        match = _DMY.match(clean(value))
        if not match:
            continue
        first, second = int(match.group(1)), int(match.group(2))
        if first > 12:
            day_first += 1
        elif second > 12:
            month_first += 1
    if day_first and month_first:
        return "conflicting"
    if day_first:
        return "day_first"
    if month_first:
        return "month_first"
    return "ambiguous"


def parse_date(value: object, *, order: str) -> date | None:
    """A date, or None if the cell is empty or unreadable.

    ``openpyxl`` hands real Excel dates back as ``datetime``, which is the case
    that needs no parsing at all — and the case a hand-rolled reader gets wrong.
    """
    if value is None or clean(value) == "":
        return None
    if hasattr(value, "date") and not isinstance(value, str):
        return value.date()  # datetime from openpyxl
    if isinstance(value, date):
        return value
    text = clean(value)
    iso = _ISO.match(text)
    if iso:
        return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    match = _DMY.match(text)
    if not match:
        return None
    a, b, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
    day, month = (a, b) if order != "month_first" else (b, a)
    try:
        return date(year, month, day)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Money and counts
# --------------------------------------------------------------------------- #


def parse_amount(value: object) -> Decimal | None:
    """A money amount as Decimal, tolerant of thousands separators and currency
    symbols pasted in from a rate sheet."""
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    text = clean(value).replace(",", "").replace(" ", "")
    text = re.sub(r"^(KES|KSH|USD|EUR|GBP|\$|€|£)", "", text, flags=re.I)
    if not text:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    return amount if amount >= 0 else None


def parse_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    text = clean(value)
    return int(text) if re.fullmatch(r"\d+", text) else None


def parse_percent(value: object) -> Decimal | None:
    if value is None or clean(value) == "":
        return None
    text = clean(value).rstrip("%")
    amount = parse_amount(text)
    return amount if amount is not None and 0 <= amount <= 100 else None


def parse_bool(value: object, *, default: bool) -> bool:
    text = key(value)
    if text in {"YES", "Y", "TRUE", "1", "MANDATORY", "COMPULSORY"}:
        return True
    if text in {"NO", "N", "FALSE", "0", "OPTIONAL"}:
        return False
    return default


# --------------------------------------------------------------------------- #
# Child age bands
# --------------------------------------------------------------------------- #

_BAND = re.compile(r"^(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})$")
_ABOVE = re.compile(r"^(?:above|over|from)\s*(\d{1,2})(?:\s*(?:yrs?|years?))?$", re.I)
_UNDER = re.compile(r"^(?:under|below)\s*(\d{1,2})(?:\s*(?:yrs?|years?))?$", re.I)


def parse_child_ages(value: object) -> tuple[int, int] | None:
    """The child band as ``(min, max)`` inclusive.

    The corpus writes it three ways. ``04-11`` and ``00-02`` are explicit bands.
    ``Above 8`` states where *adult* pricing begins, so the child band runs up to
    the year below — a traveller "above 8" pays adult, which makes 8 the last
    child year. Getting that boundary backwards charges an eight-year-old as an
    adult on 64 rows of this file.
    """
    text = clean(value)
    if not text:
        return None
    band = _BAND.match(text)
    if band:
        low, high = int(band.group(1)), int(band.group(2))
        return (low, high) if low <= high else (high, low)
    above = _ABOVE.match(text)
    if above:
        return (0, int(above.group(1)))
    under = _UNDER.match(text)
    if under:
        return (0, int(under.group(1)) - 1)
    return None


# --------------------------------------------------------------------------- #
# Destinations
# --------------------------------------------------------------------------- #

# Places in the corpus that are not in Kenya. Left as data rather than inferred
# from the name, because a destination's country decides which fee schedule and
# which residence categories apply at all.
FOREIGN_DESTINATIONS = {
    "DAR ES SALAAM": "Tanzania",
    "KIGALI": "Rwanda",
}


def destination_country(name: object) -> str:
    return FOREIGN_DESTINATIONS.get(key(name), "Kenya")


def slugify(name: object) -> str:
    text = clean(name).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def near_duplicates(names: list[str]) -> dict[str, list[str]]:
    """Destination names that are probably the same place, grouped by their
    normalised word set.

    Reported, never merged. "Mombasa", "Mombasa/Nyali" and "Nyali, Mombasa" are
    three spellings in this file and probably two places; but "Maasai Mara" and
    "Maasai Mara (Mara North Conservancy)" are genuinely different — the
    conservancy has its own fees and its own bed-night charge. Only somebody who
    knows the properties can tell those cases apart, so the importer surfaces the
    question rather than answering it.
    """
    groups: dict[frozenset[str], list[str]] = {}
    for name in {clean(n) for n in names if clean(n)}:
        words = frozenset(
            w for w in re.split(r"[^a-z0-9]+", name.lower()) if len(w) > 2
        )
        groups.setdefault(words, []).append(name)
    out: dict[str, list[str]] = {}
    for _words, members in groups.items():
        if len(members) > 1:
            out[sorted(members)[0]] = sorted(members)
    # Also flag names whose word sets overlap but are not equal, one containing
    # the other — "Mombasa" inside "Mombasa/Nyali".
    cleaned = sorted({clean(n) for n in names if clean(n)})
    for name in cleaned:
        contained = [
            other
            for other in cleaned
            if other != name
            and set(re.split(r"[^a-z0-9]+", name.lower()))
            < set(re.split(r"[^a-z0-9]+", other.lower()))
        ]
        if contained:
            out.setdefault(name, sorted({name, *contained}))
    return out
