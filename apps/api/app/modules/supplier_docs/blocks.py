"""BlockRateExtractor — reads the *transposed* rate-sheet layout.

The grid reader assumes occupancy is a column and the season window is a row.
Plenty of sheets are laid out the other way round, with the room name heading a
block, the meal plans as columns, the occupancy as the row label, and two season
blocks side by side. Temple Point's 2027/28 STO sheet is the clearest case:

    HIGH SEASON                          FESTIVE SEASON
    11.01.27 - 19.12.27                  20.12.27 - 10.01.28
    CREEK DELUXE   BO     B&B    HB      FB      BO     B&B    HB     FB
    Single         21,600 24,000 26,500  28,400  30,200 32,600 35,100 37,000
    Double         24,000 28,900 33,700  37,600  33,600 38,400 43,300 47,200

Nothing here can be read by cell position alone, because the ruled table on that
page contains only the price rows — the room name, the meal plans, the seasons
and the dates all live outside it in page text. So this reader works from **word
coordinates**: every price is matched to the meal plan whose heading sits above
it and to the season block whose date window sits above that.

Word positions are also why this reader exists rather than another table
strategy: asking pdfplumber for a text-positioned table on these pages splits
"26,500" into the cells "26", ",5" and "00", which is unusable for money.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

import pdfplumber

from app.core.storage import resolve
from app.integrations.rate_extraction import (
    ExtractedRateRow,
    ExtractionHint,
    ExtractionResult,
)
from app.modules.supplier_docs.parsing import (
    parse_currency,
    parse_date_range,
    parse_meal_plan,
    parse_money,
    parse_occupancy,
    parse_season,
)

# Two words closer than this belong to the same phrase ("HIGH" + "SEASON",
# "11.01.27" + "-" + "19.12.27"). It has to sit between the two real gaps on
# these sheets: within a phrase the gap is 2-7pt, while adjacent price columns
# are about 14pt apart. Too generous and the four prices of a season block merge
# into one span that no longer reads as a single amount.
_PHRASE_GAP = 10.0
# Words whose vertical centres are within this are on the same visual line.
_LINE_TOL = 3.0
# A meal-plan heading row needs at least this many codes, so a stray "HB" in
# prose does not turn a sentence into a column header.
_MIN_MEAL_CODES = 2
# A price belongs to a meal-plan column only if it sits under it. Real rows are
# within a few points; on Temple Point the prices sit about 7pt left of their
# heading.
_COLUMN_TOL = 25.0
# A rate row carries one price per meal plan, so a line with a single stray
# number is prose. Without this, the "Child Policies" paragraph on the Temple
# Point sheet produced rate rows of 3, 12 and 60 - ages read as money.
_MIN_ALIGNED_PRICES = 2
# Sheets that price per guest rather than per room say so in prose. The figure
# then means something different from everything else the system stores, so it is
# flagged for the reviewer instead of being taken as a room price.
_PER_PERSON = re.compile(r"per\s+person|pp\s+sharing|per\s+pax", re.IGNORECASE)
# "per room" wins when a page says both, because a sheet that states its basis as
# per room usually mentions per person only for a supplement. Temple Point is
# exactly that: "Rates are per room per night" alongside "Supplement Christmas:
# KSH 3300 per person per night".
_PER_ROOM = re.compile(r"per\s+room", re.IGNORECASE)


def _is_fragmented(line: list[dict]) -> bool:
    """Whether a line came out as loose characters rather than words."""
    if len(line) < 6:
        return False
    # Only lone letters and digits count. A currency symbol is its own token on
    # some sheets ("$ 193" is eight "$" tokens on a row of eight prices), and
    # counting those made a perfectly readable USD sheet look fragmented.
    singles = sum(
        1
        for word in line
        if len(text := str(word["text"]).strip()) == 1 and text.isalnum()
    )
    return singles / len(line) > 0.4


def _looks_like_a_room_name(text: str) -> bool:
    """Whether a row label could be a room category rather than data."""
    if parse_money(text) is not None or parse_occupancy(text) is not None:
        return False
    if parse_meal_plan(text) is not None or parse_season(text) is not None:
        return False
    return bool(re.search(r"[A-Za-z]{3}", text))


@dataclass(frozen=True)
class _Span:
    """A phrase and the horizontal band it occupies."""

    text: str
    x0: float
    x1: float

    @property
    def centre(self) -> float:
        return (self.x0 + self.x1) / 2


def _lines(words: list[dict]) -> list[list[dict]]:
    """Group words into visual lines, top to bottom."""
    rows: dict[float, list[dict]] = defaultdict(list)
    for word in words:
        rows[round(float(word["top"]) / _LINE_TOL)].append(word)
    return [
        sorted(rows[key], key=lambda w: float(w["x0"])) for key in sorted(rows)
    ]


def _phrases(line: list[dict]) -> list[_Span]:
    """Merge words separated by less than a column gap into one phrase."""
    spans: list[_Span] = []
    for word in line:
        x0, x1, text = float(word["x0"]), float(word["x1"]), str(word["text"])
        # Two amounts are never one phrase however close they sit. Merging them
        # loses both, since a cell holding several numbers is deliberately
        # refused rather than guessed at.
        both_money = bool(
            spans
            and parse_money(spans[-1].text) is not None
            and parse_money(text) is not None
        )
        if spans and not both_money and x0 - spans[-1].x1 <= _PHRASE_GAP:
            previous = spans.pop()
            spans.append(_Span(f"{previous.text} {text}", previous.x0, x1))
        else:
            spans.append(_Span(text, x0, x1))
    return spans


def _nearest(spans: list[_Span], centre: float) -> _Span | None:
    """The span horizontally closest to ``centre``.

    Prices sit directly beneath the heading they belong to, so proximity of
    centres is the relationship being modelled — not left-edge order, which
    breaks as soon as one column is wider than its neighbour.
    """
    if not spans:
        return None
    return min(spans, key=lambda s: abs(s.centre - centre))


class BlockRateExtractor:
    """Reads room-block sheets where meal plans are columns."""

    name = "pdf-block"

    def supports(self, content_type: str, filename: str) -> bool:
        return content_type == "application/pdf" or filename.lower().endswith(".pdf")

    def extract(self, path: str, hint: ExtractionHint) -> ExtractionResult:
        result = ExtractionResult(provider=self.name)
        try:
            absolute = resolve(path)
        except ValueError as exc:  # pragma: no cover - defensive
            result.warnings.append(str(exc))
            return result

        try:
            with pdfplumber.open(absolute) as pdf:
                result.page_count = len(pdf.pages)
                for number, page in enumerate(pdf.pages, start=1):
                    result.rows.extend(self._read_page(page, hint, page_number=number))
        except Exception as exc:  # noqa: BLE001 - one bad file must not 500
            result.warnings.append(f"could not read the PDF: {type(exc).__name__}: {exc}")
        return result

    def _read_page(
        self, page: object, hint: ExtractionHint, *, page_number: int
    ) -> list[ExtractedRateRow]:
        words = page.extract_words()  # type: ignore[attr-defined]
        if not words:
            return []

        page_text = " ".join(str(w["text"]) for w in words)
        page_currency = parse_currency(page_text)
        per_person = bool(_PER_PERSON.search(page_text)) and not _PER_ROOM.search(
            page_text
        )

        # State carried down the page. Each is replaced when a newer heading of
        # that kind appears, which is what makes a block layout readable at all:
        # a price belongs to the most recent room and meal-plan heading above it.
        room: str | None = None
        meal_columns: list[_Span] = []
        season_bands: list[_Span] = []
        date_bands: list[_Span] = []

        out: list[ExtractedRateRow] = []
        for line in _lines(words):
            spans = _phrases(line)
            if not spans:
                continue
            # Some sheets extract a character at a time ("1 4 , 5 0 0" for
            # 14,500), usually a font-encoding quirk. Digits recombined by
            # guesswork would be invented money, and taking the fragments at face
            # value yields nonsense rates of 1 and 4, so such a line is skipped.
            if _is_fragmented(line):
                continue

            seasons = [s for s in spans if parse_season(s.text)]
            dates = [s for s in spans if all(parse_date_range(s.text))]
            meals = [s for s in spans if parse_meal_plan(s.text)]
            monies = [s for s in spans if parse_money(s.text) is not None]

            # A date row: remember the windows and where each one sits.
            if dates and not monies:
                date_bands = dates
                continue
            # A season-name row, which sits above its date row.
            if seasons and not monies and not dates:
                season_bands = seasons
                continue
            # A meal-plan heading row. Its leftmost span is the room name when it
            # is not itself a meal code, which is how these sheets write it.
            if len(meals) >= _MIN_MEAL_CODES and not monies:
                meal_columns = meals
                leftmost = spans[0]
                if leftmost not in meals and parse_occupancy(leftmost.text) is None:
                    room = leftmost.text
                continue

            # A data row, in one of two forms. Either the row label is an
            # occupancy and the room came from the block heading (Temple Point),
            # or the row label is the room category itself and the occupancy is
            # not stated anywhere (Turtle Bay). Both are common; the second
            # leaves occupancy for the reviewer rather than assuming it.
            if not monies or not meal_columns:
                continue
            # Keep only the amounts that line up with a meal-plan column, and
            # require several of them. This is what separates a rate row from a
            # sentence that happens to contain numbers.
            aligned = [
                money
                for money in monies
                if (nearest := _nearest(meal_columns, money.centre)) is not None
                and abs(nearest.centre - money.centre) <= _COLUMN_TOL
            ]
            if len(aligned) < _MIN_ALIGNED_PRICES:
                continue
            occupancy = parse_occupancy(spans[0].text)
            row_room = room
            if occupancy is None:
                if not _looks_like_a_room_name(spans[0].text):
                    continue
                row_room = spans[0].text

            for money in aligned:
                amount = parse_money(money.text)
                if amount is None:
                    continue
                meal_span = _nearest(meal_columns, money.centre)
                date_span = _nearest(date_bands, money.centre)
                season_span = _nearest(season_bands, money.centre)

                start, end = (
                    parse_date_range(date_span.text) if date_span else (None, None)
                )
                meal_plan = (
                    parse_meal_plan(meal_span.text) if meal_span else None
                ) or hint.default_meal_plan

                warnings: list[str] = []
                if meal_plan is None:
                    warnings.append("could not read the meal plan for this column")
                if not (start and end):
                    warnings.append("could not read the season window for this block")
                if row_room is None:
                    warnings.append("could not read the room type for this block")
                if occupancy is None:
                    warnings.append(
                        "the sheet does not say how many guests this price covers"
                    )
                if per_person:
                    warnings.append(
                        "this sheet quotes rates PER PERSON, not per room - confirm "
                        "the basis before storing it as a room rate"
                    )

                currency = page_currency or hint.default_currency
                if currency is None:
                    warnings.append("the sheet does not state a currency")

                out.append(
                    ExtractedRateRow(
                        room_type=row_room,
                        meal_plan=meal_plan,
                        occupancy=occupancy,
                        season_name=(
                            parse_season(season_span.text) if season_span else None
                        ),
                        effective_from=start,
                        effective_to=end,
                        currency=currency,
                        rate_per_night=amount,
                        confidence=round(max(0.1, 1.0 - 0.25 * len(warnings)), 2),
                        source_note=(
                            f"page {page_number}: {row_room} / {spans[0].text} / "
                            f"{meal_span.text if meal_span else '?'} = {money.text}"
                        )[:500],
                        page=page_number,
                        warnings=tuple(warnings),
                    )
                )
        return out
