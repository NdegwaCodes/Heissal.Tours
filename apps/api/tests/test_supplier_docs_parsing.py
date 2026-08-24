"""Stage 3.2 — the pure parsers, against strings taken from the real corpus.

No database and no PDF: these are the functions that turn text into money and
dates, which is where a silent error becomes a wrong price on a client's
quotation. Every literal below is copied from a 2026/27 supplier sheet.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.modules.supplier_docs.parsing import (
    parse_currency,
    parse_date_range,
    parse_meal_plan,
    parse_money,
    parse_occupancy,
    parse_season,
)

D = Decimal


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Swahili Beach writes thousands with a DOT. Reading this as 23.92
        # understates the rate by a factor of a thousand, and the result still
        # looks like a plausible number, so it would survive review.
        ("23.920 KES", D("23920")),
        ("48.880 KES", D("48880")),
        ("21.840 KES", D("21840")),
        # Temple Point uses a comma.
        ("21,600", D("21600")),
        ("28,400", D("28400")),
        # Baobab writes bare numbers.
        ("280", D("280")),
        ("1040", D("1040")),
        # Two decimals is a fraction, not a thousands group.
        ("1.50", D("1.50")),
        ("129.50", D("129.50")),
    ],
)
def test_money_survives_every_separator_convention(text, expected):
    assert parse_money(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "   ",
        "-",
        "free of charge",
        "n/a",
        # A merged cell holding three prices. Taking the first would attach the
        # single-occupancy price to every occupancy column.
        "280 370 500",
        "0",
    ],
)
def test_money_refuses_rather_than_guesses(text):
    assert parse_money(text) is None


@pytest.mark.parametrize(
    ("text", "start", "end"),
    [
        ("04/01/2026 - 02/04/2026", date(2026, 1, 4), date(2026, 4, 2)),
        ("01/10/2026 - 30/10/2026", date(2026, 10, 1), date(2026, 10, 30)),
        # Temple Point's dotted two-digit years.
        ("11.01.27 - 19.12.27", date(2027, 1, 11), date(2027, 12, 19)),
        ("20.12.27 - 10.01.28", date(2027, 12, 20), date(2028, 1, 10)),
        # No spaces around the separator, as one sheet writes it.
        ("21/12/2026-03/01/2027", date(2026, 12, 21), date(2027, 1, 3)),
        # Only the END states a year, and the window crosses New Year: the start
        # belongs to the year BEFORE. Moving the wrong end shifts a festive rate
        # by a full twelve months.
        ("23 Dec - 3 Jan '27", date(2026, 12, 23), date(2027, 1, 3)),
    ],
)
def test_date_ranges_from_the_corpus(text, start, end):
    assert parse_date_range(text) == (start, end)


@pytest.mark.parametrize(
    ("text", "year", "start", "end"),
    [
        ("03 Jan - 02 Apr", 2026, date(2026, 1, 3), date(2026, 4, 2)),
        ("01 Nov - 30 Nov", 2026, date(2026, 11, 1), date(2026, 11, 30)),
        # Wraps into the next year when neither side states one.
        ("23 Dec - 3 Jan", 2026, date(2026, 12, 23), date(2027, 1, 3)),
    ],
)
def test_year_comes_from_the_document_when_the_row_omits_it(text, year, start, end):
    assert parse_date_range(text, default_year=year) == (start, end)


def test_a_price_is_never_read_as_a_year():
    """Baobab puts prices immediately after the season: "03 Jan - 02 Apr 280 370".

    The year-parsing regex previously swallowed 280 and produced the year 0280.
    A price becoming a date is the exact failure this parser exists to prevent,
    so the year is only accepted as four digits or behind an apostrophe.
    """
    assert parse_date_range("03 Jan - 02 Apr 280 370 500") == (None, None)
    start, end = parse_date_range("03 Jan - 02 Apr 280 370 500", default_year=2026)
    assert (start, end) == (date(2026, 1, 3), date(2026, 4, 2))


def test_the_real_typo_in_the_swahili_beach_sheet_is_refused():
    """That sheet's Easter row reads "03/04-2026 - 06/04/2026".

    Refusing sends the row to a human. Guessing would eventually invent a date.
    """
    assert parse_date_range("03/04-2026 - 06/04/2026") == (None, None)


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("BB", "BB"),
        ("B&B", "BB"),
        ("Bed & Breakfast", "BB"),
        ("HB", "HB"),
        ("Half Board", "HB"),
        ("FB", "FB"),
        ("Full Board", "FB"),
        ("meal FB", "FB"),
        # "BO" (bed only) is four sheets' name for the seeded RO plan.
        ("BO", "RO"),
        ("Room Only", "RO"),
        ("AI", "AI"),
    ],
)
def test_meal_plan_codes_map_onto_seeded_plans(text, code):
    assert parse_meal_plan(text) == code


@pytest.mark.parametrize(
    ("text", "occupancy"),
    [
        ("Single", 1),
        ("SGL", 1),
        ("Double", 2),
        ("Twin", 2),
        ("Double/Twin", 2),
        ("Triple", 3),
    ],
)
def test_occupancy_from_column_headings(text, occupancy):
    assert parse_occupancy(text) == occupancy


@pytest.mark.parametrize("text", ["SGL OR DBL", "room", "", None, "Standard Room"])
def test_ambiguous_occupancy_is_left_for_the_reviewer(text):
    """Swahili Beach prices club rooms "SGL OR DBL" — one figure for either.

    Picking one would invent a rate that the supplier never quoted.
    """
    assert parse_occupancy(text) is None


@pytest.mark.parametrize(
    ("text", "season"),
    [
        ("HIGH", "High"),
        ("LOW", "Low"),
        ("SHOULDER", "Shoulder"),
        ("PEAK", "Peak"),
        ("EASTER", "Easter"),
        ("FESTIVE SEASON", "Festive"),
    ],
)
def test_seasons_keep_the_sheets_own_vocabulary(text, season):
    assert parse_season(text) == season


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("23.920 KES", "KES"),
        ("KSH STO Rates", "KES"),
        ("USD 370", "USD"),
        ("$40 one-way", "USD"),
    ],
)
def test_currency_when_stated_unambiguously(text, code):
    assert parse_currency(text) == code


def test_currency_is_none_when_a_document_names_two():
    """Temple Point quotes rooms in KSH and transfers in USD on one page.

    Choosing either would misprice one of them by a factor of 130.
    """
    assert parse_currency("rooms in KSH, transfers in USD") is None
    assert parse_currency("no currency here") is None


def test_a_shared_price_heading_blocks_an_invented_occupancy():
    """Swahili Beach prices club rooms "SGL OR DBL" — one figure for either.

    The phrase survives extraction sometimes as one cell and sometimes split
    across three. Split, the fragment "SGL" reads as single occupancy on its
    own, which would store a single-occupancy rate the supplier never quoted, so
    the columns either side of a bare "OR" are locked as unknown.
    """
    from app.modules.supplier_docs.extraction import _ambiguous_columns

    # Intact in one cell.
    assert _ambiguous_columns(["", "SGL OR DBL", "Triple"], 3) == {1}
    # Split across cells: both neighbours of the "OR" are ambiguous.
    assert _ambiguous_columns(["SGL", "OR", "DBL"], 3) == {0, 2}
    # An ordinary heading is left alone.
    assert _ambiguous_columns(["Single", "Double", "Triple"], 3) == set()
    assert _ambiguous_columns(["Standard Room", "", "Superior Room"], 3) == set()
