"""CompositeRateExtractor — tries each reader and keeps the best result.

Supplier sheets come in at least two incompatible layouts and there is no
reliable way to tell them apart before parsing:

* :class:`~app.modules.supplier_docs.extraction.GridRateExtractor` reads sheets
  where occupancy is a column and the season window is a row.
* :class:`~app.modules.supplier_docs.blocks.BlockRateExtractor` reads the
  transposed shape, where the room name heads a block, meal plans are columns
  and occupancy is the row label.

So both are run and the better result wins. "Better" is judged on how many rows
came out **confirmable** — complete enough to become a rate without a reviewer
filling in blanks — falling back to the total row count when neither produced a
complete row. Deciding by evidence rather than by sniffing the document keeps
the choice explainable: the summary records which reader was used.

Results are never merged. Two readers describing the same page produce the same
rates twice, and a reviewer cannot tell a genuine duplicate from an artefact of
how it was parsed.
"""

from __future__ import annotations

from dataclasses import replace

import pdfplumber

from app.core.storage import resolve
from app.integrations.rate_extraction import (
    ExtractionHint,
    ExtractionResult,
    RateExtractionProvider,
)
from app.modules.supplier_docs.blocks import BlockRateExtractor
from app.modules.supplier_docs.extraction import GridRateExtractor
from app.modules.supplier_docs.parsing import document_year, season_windows


def _score(result: ExtractionResult) -> tuple[int, int]:
    complete = sum(1 for row in result.rows if row.is_complete)
    return complete, len(result.rows)


class CompositeRateExtractor:
    name = "pdf-composite"

    def __init__(self, providers: list[RateExtractionProvider] | None = None) -> None:
        self.providers: list[RateExtractionProvider] = providers or [
            GridRateExtractor(),
            BlockRateExtractor(),
        ]

    def supports(self, content_type: str, filename: str) -> bool:
        return any(p.supports(content_type, filename) for p in self.providers)

    def extract(self, path: str, hint: ExtractionHint) -> ExtractionResult:
        attempts: list[ExtractionResult] = [
            provider.extract(path, hint)
            for provider in self.providers
            if provider.supports("application/pdf", path)
        ]
        if not attempts:
            return ExtractionResult(
                warnings=["no extraction provider can read this file"],
                provider=self.name,
            )

        best = max(attempts, key=_score)
        if _score(best) == (0, 0):
            # Nothing was read by anybody. Keep every reader's explanation: one
            # will say "no text layer" and another "no grid recognised", and
            # which it is decides whether the answer is the vision provider or
            # entering the sheet by hand.
            merged = ExtractionResult(provider=self.name, page_count=best.page_count)
            for attempt in attempts:
                merged.warnings.extend(
                    f"[{attempt.provider}] {warning}" for warning in attempt.warnings
                )
            if not merged.warnings:
                merged.warnings.append(
                    "no rates were recognised in this document; enter them by hand "
                    "against the stored file"
                )
            return merged

        # Name the reader that won, so a surprising result can be traced to the
        # code that produced it rather than to "the parser".
        best.provider = f"{self.name}:{best.provider}"
        self._date_undated_seasons(best, path)
        return best

    @staticmethod
    def _date_undated_seasons(result: ExtractionResult, path: str) -> None:
        """Fill in dates for rows that name a season the document defines in prose.

        Several sheets state their seasons once, in a sentence ("High Season 6th
        January - 28th February; 3rd April - 6th April"), and then label the rate
        table with nothing but the season name. Those rows arrive with a season
        and no window, and are unusable until the two are joined.

        A season with more than one window is left alone and flagged instead:
        picking the first would attach the rate to part of its real season, which
        is a quieter error than having no dates at all.
        """
        undated = [
            row
            for row in result.rows
            if row.season_name and not (row.effective_from and row.effective_to)
        ]
        if not undated:
            return

        try:
            with pdfplumber.open(resolve(path)) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        except Exception:  # noqa: BLE001 - the rows are already usable without this
            return

        windows = season_windows(text, default_year=document_year(text))
        if not windows:
            return

        for index, row in enumerate(result.rows):
            if row not in undated or row.season_name is None:
                continue
            found = windows.get(row.season_name)
            if not found:
                continue
            if len(found) > 1:
                result.rows[index] = replace(
                    row,
                    warnings=(
                        *row.warnings,
                        f"the sheet gives {len(found)} separate windows for the "
                        f"{row.season_name} season, so which one this rate belongs "
                        f"to has to be chosen by hand",
                    ),
                )
                continue
            start, end = found[0]
            result.rows[index] = replace(
                row,
                effective_from=start,
                effective_to=end,
                warnings=tuple(
                    w
                    for w in row.warnings
                    if "season window" not in w and "date window" not in w
                ),
                source_note=(
                    f"{row.source_note} (dates from the sheet's "
                    f"{row.season_name} season definition)"
                )[:500],
            )
