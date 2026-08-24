"""GridRateExtractor — the deterministic parser for text-layer rate sheets.

Covers 32 of the 35 documents in the corpus. It reads the ruled table structure
rather than the flattened text, because line-based extraction misaligns silently:
on the Swahili Beach contract the season labels and the price rows come out
several lines apart, which would attach a rate to the wrong date window and
raise no error. Reading cells from the grid keeps a price with its own row.

The shape it understands is the one the corpus overwhelmingly uses:

    |          |                         | meal | Standard Room   | Superior Room   |
    |          |                         |      | Single | Double | Single | Double |
    | HIGH     | 04/01/2026 - 02/04/2026 | BB   | 23.920 | 31.200 | 31.200 | 38.480 |

Header rows name the room type (spanning several columns) and the occupancy
beneath it; each body row carries a season, a date window, a meal plan, and one
price per column. Every emitted row is a candidate for human confirmation, never
a stored rate.
"""

from __future__ import annotations

import re

import pdfplumber

from app.core.storage import resolve
from app.integrations.rate_extraction import (
    ExtractedRateRow,
    ExtractionHint,
    ExtractionResult,
)
from app.modules.supplier_docs.parsing import (
    document_year,
    parse_currency,
    parse_date_range,
    parse_meal_plan,
    parse_money,
    parse_occupancy,
    parse_season,
)

# Below this, a table is a signature block or a policy note rather than a rate
# grid, and trying to read it produces noise the reviewer has to wade through.
_MIN_COLUMNS = 3
_MIN_ROWS = 2

# Many sheets are ruled, and pdfplumber's default line strategy reads those
# best. Others (Temple Point) align columns with whitespace alone and yield no
# tables at all under that strategy, so a text-position pass is tried as well.
# Both are deterministic; neither guesses.
_TEXT_STRATEGY = {"vertical_strategy": "text", "horizontal_strategy": "text"}

def _clean(cell: str | None) -> str:
    return " ".join((cell or "").split())


def _ambiguous_columns(row: list[str | None], width: int) -> set[int]:
    """Columns whose occupancy cannot be read because the heading says "X OR Y".

    Handles the phrase split across cells as well as intact in one, because
    which of the two happens depends on how the sheet was drawn.
    """
    filled = [(i, _clean(row[i])) for i in range(min(len(row), width)) if _clean(row[i])]
    ambiguous: set[int] = set()
    for position, (index, text) in enumerate(filled):
        if " OR " in f" {text.upper()} ":
            # Intact in one cell: that column alone is ambiguous.
            if parse_occupancy(text) is None and re.search(
                r"(?<![A-Z])(SGL|DBL|SINGLE|DOUBLE|TWIN|TRIPLE)(?![A-Z])", text.upper()
            ):
                ambiguous.add(index)
            # Split across cells: the neighbours are the two occupancies.
            if text.upper().strip() == "OR":
                for neighbour in (position - 1, position + 1):
                    if 0 <= neighbour < len(filled):
                        ambiguous.add(filled[neighbour][0])
    return ambiguous


def _looks_like_a_name(text: str) -> bool:
    """Whether a header cell could be a room name rather than stray data."""
    if not text:
        return True  # blank cells are handled by the carry-forward rule
    if parse_money(text) is not None:
        return False
    if not re.search(r"[A-Za-z]{3}", text):
        return False
    start, end = parse_date_range(text)
    return not (start and end)


class GridRateExtractor:
    """Reads candidate rates out of a text-layer PDF's table structure."""

    name = "pdf-grid"

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
                pages = [(n, p, p.extract_text() or "") for n, p in enumerate(pdf.pages, 1)]
                text_chars = sum(len(t) for _, _, t in pages)
                # Season windows are often written without a year ("03 Jan - 02
                # Apr") because the sheet's title carries it. See document_year
                # for why this is the most frequently named year rather than the
                # earliest one.
                doc_year = document_year(" ".join(t for _, _, t in pages))
                if doc_year is None:
                    result.warnings.append(
                        "the document does not name a year, so any season window "
                        "written without one cannot be dated"
                    )
                for number, page, _text in pages:
                    tables = page.extract_tables()
                    rows = [
                        r
                        for table in tables
                        for r in self._read_table(
                            table, hint, page=number, result=result, doc_year=doc_year
                        )
                    ]
                    if not rows:
                        # No ruling lines on this page: retry on text positions.
                        for table in page.extract_tables(_TEXT_STRATEGY):
                            rows.extend(
                                self._read_table(
                                    table,
                                    hint,
                                    page=number,
                                    result=result,
                                    doc_year=doc_year,
                                )
                            )
                    result.rows.extend(rows)
        except Exception as exc:  # noqa: BLE001 - one bad file must not 500
            # A provider that raises takes down the upload endpoint for every
            # other document, so failures are reported as data.
            result.warnings.append(f"could not read the PDF: {type(exc).__name__}: {exc}")
            return result

        if not result.rows and text_chars < 50:
            result.warnings.append(
                "no text layer — this looks like a scanned image and needs the "
                "vision provider rather than the grid parser"
            )
        elif not result.rows:
            result.warnings.append(
                "text was found but no rate grid was recognised; the rates may be "
                "laid out without ruling lines, so they need entering by hand"
            )
        return result

    # -- internals -------------------------------------------------------- #

    def _read_table(
        self,
        table: list[list[str | None]],
        hint: ExtractionHint,
        *,
        page: int,
        result: ExtractionResult,
        doc_year: int | None,
    ) -> list[ExtractedRateRow]:
        if len(table) < _MIN_ROWS or max((len(r) for r in table), default=0) < _MIN_COLUMNS:
            return []

        width = max(len(r) for r in table)
        body_start = self._first_body_row(table, doc_year)
        if body_start is None:
            return []

        columns = self._column_meanings(table[:body_start], width)
        # Nothing to hang prices on. Reporting it beats emitting rows whose room
        # type is a guess.
        if not any(room or occ for room, occ in columns):
            result.warnings.append(
                f"page {page}: found a rate grid but could not read its column "
                f"headings; rows from it are skipped"
            )
            return []

        header_text = " ".join(_clean(c) for row in table[:body_start] for c in row)
        table_currency = parse_currency(header_text)
        # Plenty of sheets name the board basis once, in the caption above the
        # grid ("STO NET RATES - HALF BOARD"), and never again per row.
        table_meal_plan = parse_meal_plan(header_text)
        rows: list[ExtractedRateRow] = []
        for raw_row in table[body_start:]:
            rows.extend(
                self._read_body_row(
                    raw_row,
                    columns,
                    hint,
                    page=page,
                    table_currency=table_currency,
                    table_meal_plan=table_meal_plan,
                    doc_year=doc_year,
                )
            )
        return rows

    @staticmethod
    def _first_body_row(
        table: list[list[str | None]], doc_year: int | None
    ) -> int | None:
        """Index of the first row that carries a date window.

        The date window is what makes a row a rate rather than a heading, and it
        is more reliable than counting header rows — the corpus uses one, two and
        three header rows for the same kind of table.
        """
        for index, row in enumerate(table):
            joined = " ".join(_clean(c) for c in row)
            start, end = parse_date_range(joined, default_year=doc_year)
            if start and end:
                return index
        return None

    @staticmethod
    def _column_meanings(
        header_rows: list[list[str | None]], width: int
    ) -> list[tuple[str | None, int | None]]:
        """Work out (room type, occupancy) for each column.

        Room names span the occupancy columns beneath them and are written once,
        so a blank cell inherits the last name seen to its left. Occupancy is read
        from whichever header row states it.
        """
        room_by_column: list[str | None] = [None] * width
        occ_by_column: list[int | None] = [None] * width

        # Read the header upwards, from the row nearest the body. The nearest
        # non-empty cell above a column is what describes it; anything further up
        # is a title or a caption. Reading downwards instead let a spanning title
        # claim the columns first — which is how "STO RATE AGREEMENT" became a
        # room type — and blank spacer rows make a fixed row window unreliable.
        for row in reversed(header_rows):
            # A heading like "SGL OR DBL" states one price for either occupancy.
            # It survives as a single cell sometimes and as three cells other
            # times, and the fragment "SGL" on its own reads as single occupancy
            # — inventing a rate the supplier never quoted. Columns either side
            # of a bare "OR" are therefore locked as unknown.
            ambiguous = _ambiguous_columns(row, width)
            for index in ambiguous:
                occ_by_column[index] = None
            # Resolve this row on its own, then fill only columns still unknown.
            row_rooms: list[str | None] = [None] * width
            carried: str | None = None
            # A row that states occupancies is a column-label row, not the row
            # that names rooms, so no room name is taken from it. Without this,
            # the "meal" label carried rightwards and became the room type of
            # every column to its right that had no label of its own.
            labels_occupancy = any(
                parse_occupancy(_clean(row[i])) is not None
                for i in range(min(len(row), width))
                if i not in ambiguous
            )
            for index in range(width):
                text = _clean(row[index]) if index < len(row) else ""
                if index in ambiguous:
                    continue
                occupancy = parse_occupancy(text)
                if occupancy is not None:
                    occ_by_column[index] = occupancy
                    # "Single"/"Double" is an occupancy label, not a room name, so
                    # it must not become the carried room name.
                    continue
                # A room name is prose. Numbers, money and dates appearing in a
                # header row are stray data, and letting one become a room name
                # produces rows labelled "345" that a reviewer has to unpick.
                if not _looks_like_a_name(text):
                    continue
                if labels_occupancy:
                    continue
                if text and parse_meal_plan(text) is None and parse_season(text) is None:
                    carried = text
                    row_rooms[index] = text
                elif carried and not text:
                    # A room name is written once and spans the occupancy columns
                    # beneath it, so a blank inherits from its left.
                    row_rooms[index] = carried
            for index, name in enumerate(row_rooms):
                if name and room_by_column[index] is None:
                    room_by_column[index] = name
        return list(zip(room_by_column, occ_by_column, strict=True))

    def _read_body_row(
        self,
        raw_row: list[str | None],
        columns: list[tuple[str | None, int | None]],
        hint: ExtractionHint,
        *,
        page: int,
        table_currency: str | None,
        table_meal_plan: str | None,
        doc_year: int | None,
    ) -> list[ExtractedRateRow]:
        cells = [_clean(c) for c in raw_row]
        joined = " ".join(cells)

        season = next((s for s in (parse_season(c) for c in cells) if s), None)
        # The row's own wording first, then the table caption, then what the
        # uploader declared. Each is progressively less specific but none is a
        # guess: the last one is a human's statement about the document.
        meal_plan = (
            next((m for m in (parse_meal_plan(c) for c in cells) if m), None)
            or table_meal_plan
            or hint.default_meal_plan
        )
        start, end = parse_date_range(joined, default_year=doc_year)

        row_warnings: list[str] = []
        if not (start and end):
            row_warnings.append("could not read the date window for this row")
        if not meal_plan:
            row_warnings.append("could not read the meal plan for this row")

        out: list[ExtractedRateRow] = []
        for index, cell in enumerate(cells):
            amount = parse_money(cell)
            if amount is None:
                continue
            room, occupancy = columns[index] if index < len(columns) else (None, None)
            currency = parse_currency(cell) or table_currency or hint.default_currency

            warnings = list(row_warnings)
            if occupancy is None:
                warnings.append(
                    "the column heading does not say how many guests this price "
                    "covers (for example 'SGL OR DBL'), so it needs setting by hand"
                )
            if room is None:
                warnings.append("could not read the room type for this column")
            if currency is None:
                warnings.append("the sheet does not state a currency")

            out.append(
                ExtractedRateRow(
                    room_type=room,
                    meal_plan=meal_plan,
                    occupancy=occupancy,
                    season_name=season,
                    effective_from=start,
                    effective_to=end,
                    currency=currency,
                    rate_per_night=amount,
                    confidence=self._confidence(warnings),
                    source_note=f"page {page}: {joined}"[:500],
                    page=page,
                    warnings=tuple(warnings),
                )
            )
        return out

    @staticmethod
    def _confidence(warnings: list[str]) -> float:
        """Advisory only — it sorts the reviewer's queue, it authorises nothing.

        A clean row still has to be confirmed by a person before it becomes a
        rate, so this number never gates anything.
        """
        return round(max(0.1, 1.0 - 0.25 * len(warnings)), 2)


def default_extractor():
    """The provider the ingestion service uses unless one is injected.

    Composite rather than the grid reader alone: the corpus contains at least two
    incompatible layouts and which one a document uses cannot be told reliably
    before parsing it.
    """
    from app.modules.supplier_docs.composite import CompositeRateExtractor

    return CompositeRateExtractor()


__all__ = ["GridRateExtractor", "default_extractor"]
