"""Rate intake: reading a filled-in sheet and turning it into stored rates.

**Every figure here is invented.** The real corpus is confidential supplier
pricing and must never enter git history, so these tests reproduce the *shapes*
the real workbook contains — rack beside NETT, day-first dates, ``B&B``,
per-person-sharing, VAT-exclusive columns, blank capacity — with made-up money.

The shapes are not hypothetical. Each one is annotated with what it cost in the
3,161-row workbook the client supplied on 2026-08-29.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.errors import AppError
from app.db.session import AsyncSessionLocal
from app.modules.accommodations.models import (
    Accommodation,
    AccommodationRate,
    AccommodationSupplement,
    MealPlan,
    RoomType,
)
from app.modules.rate_intake import normalise as N
from app.modules.rate_intake.reader import read_sheet
from app.modules.rate_intake.service import RateIntakeService
from app.modules.residence.models import ResidenceCategory

pytestmark = pytest.mark.asyncio(loop_scope="session")

D = Decimal

HEADER = (
    "row_type,property_name,destination,room_type,room_sleeps,meal_plan,"
    "guest_residence,price_covers,label,valid_from,valid_to,currency,amount,"
    "charged_per,rack_or_sto,discount_percent,vat,child_amount,child_ages,"
    "min_nights,notes"
)


def sheet(tmp_path, *rows: str, name: str = "intake.csv"):
    path = tmp_path / name
    path.write_text("\n".join([HEADER, *rows]) + "\n", encoding="utf-8")
    return path


def unique(prefix: str) -> str:
    return f"{prefix} {uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------------------- #
# Pure normalisation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("written", "code"),
    [
        ("BB", "BB"), ("B&B", "BB"), ("B & B", "BB"), ("bed and breakfast", "BB"),
        ("BO", "RO"), ("RO", "RO"), ("Room Only", "RO"),
        ("HB", "HB"), ("FB", "FB"), ("AI", "AI"), ("all-inclusive", "AI"),
    ],
)
def test_meal_plan_synonyms(written, code):
    """The corpus writes bed and breakfast as ``B&B`` and room only as ``BO`` —
    795 and 108 rows. Rejecting those would have failed a third of the sheet on
    spelling."""
    assert N.meal_plan_code(written) == code


def test_an_unknown_meal_plan_is_not_guessed():
    assert N.meal_plan_code("HALF-PENSION") is None


@pytest.mark.parametrize(
    ("written", "key"),
    [
        ("non_resident", "non_resident"), ("Non-Resident", "non_resident"),
        ("NON RESIDENT", "non_resident"), ("resident", "resident"),
        ("Kenya Resident", "resident"), ("citizen", "citizen"),
        ("East African Citizen", "ea_resident"), ("African Citizen", "african_citizen"),
    ],
)
def test_residence_synonyms(written, key):
    assert N.residence_key(written) == key


def test_date_order_is_decided_from_the_file():
    """Only a component above 12 carries information. The client's workbook has
    2,820 such dates and none the other way, which settles it."""
    assert N.date_order(["19/12/2027", "11/01/2027"]) == "day_first"
    assert N.date_order(["12/19/2027", "01/11/2027"]) == "month_first"
    assert N.date_order(["11/01/2027", "05/06/2026"]) == "ambiguous"
    assert N.date_order(["19/12/2027", "12/19/2027"]) == "conflicting"


def test_a_day_first_date_parses_day_first():
    assert N.parse_date("11/01/2027", order="day_first") == date(2027, 1, 11)
    assert N.parse_date("11/01/2027", order="month_first") == date(2027, 11, 1)
    assert N.parse_date("2027-01-11", order="day_first") == date(2027, 1, 11)


def test_an_unreadable_date_is_none_rather_than_today():
    assert N.parse_date("", order="day_first") is None
    assert N.parse_date("next season", order="day_first") is None
    assert N.parse_date("31/02/2026", order="day_first") is None


def test_amounts_tolerate_how_people_paste_them():
    assert N.parse_amount("24,000") == D("24000")
    assert N.parse_amount("KES 24000") == D("24000")
    assert N.parse_amount(" 320 ") == D("320")
    assert N.parse_amount("not a rate") is None
    assert N.parse_amount("-500") is None


def test_child_age_bands_in_every_form_the_corpus_uses():
    assert N.parse_child_ages("04-11") == (4, 11)
    assert N.parse_child_ages("00-02") == (0, 2)
    # "Above 8" states where ADULT pricing starts, so 8 is the last child year.
    # Getting this backwards charges an eight-year-old as an adult on 64 rows.
    assert N.parse_child_ages("Above 8") == (0, 8)
    assert N.parse_child_ages("under 12") == (0, 11)
    assert N.parse_child_ages("who knows") is None


def test_names_are_normalised_so_one_property_is_one_property():
    """Excel supplies non-breaking spaces that are invisible in a diff and would
    otherwise split a property in two."""
    assert N.clean("Baobab Beach  Resort ") == "Baobab Beach Resort"


def test_places_outside_kenya_are_recorded_as_such():
    """The corpus reaches Dar es Salaam and Kigali. A destination's country
    decides which fee schedule applies at all."""
    assert N.destination_country("Dar es Salaam") == "Tanzania"
    assert N.destination_country("Kigali") == "Rwanda"
    assert N.destination_country("Diani") == "Kenya"


def test_near_duplicate_destinations_are_reported_not_merged():
    """"Mombasa", "Mombasa/Nyali" and "Nyali, Mombasa" are probably two places;
    "Maasai Mara" and "Maasai Mara (Naboisho Conservancy)" are definitely two,
    with different fees. Only a person can tell those apart."""
    groups = N.near_duplicates(
        ["Mombasa", "Mombasa/Nyali", "Nyali, Mombasa", "Diani"]
    )
    flat = {name for members in groups.values() for name in members}
    assert "Mombasa" in flat and "Mombasa/Nyali" in flat
    assert "Diani" not in flat


# --------------------------------------------------------------------------- #
# The reader
# --------------------------------------------------------------------------- #


def test_a_sheet_missing_a_column_is_refused_up_front(tmp_path):
    """Better than importing a column short and blaming the data."""
    path = tmp_path / "short.csv"
    path.write_text("row_type,property_name\nRATE,Somewhere\n", encoding="utf-8")
    with pytest.raises(AppError, match="missing these columns"):
        read_sheet(path)


def test_template_example_rows_are_skipped(tmp_path):
    path = sheet(
        tmp_path,
        "RATE,EXAMPLE Temple Point - delete these rows,Watamu,Creek,2,FB,citizen,2,"
        "High,2026-07-01,2026-10-31,KES,1000,room_per_night,sto,,inclusive,,,,",
    )
    rows, skipped = read_sheet(path)
    assert rows == []
    assert "example" in skipped[0]


def test_an_unreadable_file_type_says_so(tmp_path):
    path = tmp_path / "rates.pdf"
    path.write_bytes(b"%PDF-1.4")
    with pytest.raises(AppError, match="not a readable intake sheet"):
        read_sheet(path)


# --------------------------------------------------------------------------- #
# Importing
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture(loop_scope="session")
async def db():
    async with AsyncSessionLocal() as session:
        yield session


async def _import(db, path, *, commit=True):
    return await RateIntakeService(db).import_sheet(path, dry_run=not commit)


async def _rates(db, property_name: str) -> list[AccommodationRate]:
    return list(
        (
            await db.execute(
                select(AccommodationRate)
                .join(
                    Accommodation,
                    AccommodationRate.accommodation_id == Accommodation.id,
                )
                .where(Accommodation.name == property_name)
                .order_by(AccommodationRate.occupancy)
            )
        )
        .scalars()
        .all()
    )


async def test_a_dry_run_writes_nothing(db, tmp_path):
    name = unique("Dry Run Lodge")
    path = sheet(
        tmp_path,
        f"RATE,{name},Testland,Twin,2,FB,citizen,2,High,01/07/2026,31/10/2026,"
        "KES,20000,room_per_night,sto,,inclusive,,,,",
    )
    report = await _import(db, path, commit=False)
    assert report.committed is False
    assert report.total_rows == 1
    assert await _rates(db, name) == []


async def test_a_rack_and_nett_pair_becomes_one_rate_carrying_the_discount(
    db, tmp_path
):
    """The most valuable thing this importer does. 649 room-nights in the client's
    workbook arrive as two rows — the published rack rate and the agent NETT —
    because the sheets state the concession in prose and the discount column was
    never filled.

    Keeping either row alone loses money in opposite directions: the rack row
    alone believes we pay full rack and discards the concession from margin; the
    NETT row alone hands the client all of it.
    """
    name = unique("Pair Lodge")
    path = sheet(
        tmp_path,
        f"RATE,{name},Testland,Suite,2,FB,non_resident,2,Standard,11/01/2026,"
        "19/12/2026,USD,500,room_per_night,rack,,inclusive,,,,published rack",
        f"RATE,{name},Testland,Suite,2,FB,non_resident,2,Standard,11/01/2026,"
        "19/12/2026,USD,400,room_per_night,sto,,inclusive,,,,agent NETT less 20%",
    )
    report = await _import(db, path)
    assert report.rack_net_merged == 1
    assert report.rates_created == 1

    rate = (await _rates(db, name))[0]
    assert rate.rate_per_night == D("500")       # the rack figure is stored
    assert rate.rate_kind == "rack"
    assert rate.supplier_discount_pct == D("20")  # derived from 1 - 400/500
    # And the two figures the engine derives from it are the sheet's own:
    from app.modules.quotes.options import costed_rate, supplier_paid

    assert supplier_paid(rate.rate_per_night, rate.supplier_discount_pct) == D("400")
    assert costed_rate(rate.rate_per_night, rate.supplier_discount_pct, "rack") == D("450")


async def test_three_rates_for_one_room_night_is_a_conflict_not_a_merge(db, tmp_path):
    """37 groups in the client's workbook are not clean pairs. Picking one of
    three prices is not a decision an importer gets to make."""
    name = unique("Conflict Camp")
    row = (
        f"RATE,{name},Testland,Tent,2,FB,citizen,2,Standard,01/07/2026,"
        "31/10/2026,KES,{amount},room_per_night,sto,,inclusive,,,,"
    )
    path = sheet(
        tmp_path,
        row.format(amount=10000),
        row.format(amount=11000),
        row.format(amount=12000),
    )
    report = await _import(db, path)
    assert report.rates_created == 0
    assert len(report.conflicts) == 1
    assert "3 rates" in report.conflicts[0]


async def test_a_per_person_sharing_rate_is_converted_once(db, tmp_path):
    """540 rows in the corpus are per person sharing while the catalogue stores
    per room. Converting on import is the only place it happens exactly once."""
    name = unique("Sharing Camp")
    path = sheet(
        tmp_path,
        f"RATE,{name},Testland,Tent,2,FB,citizen,2,Standard,01/07/2026,31/10/2026,"
        "KES,9000,person_per_night,sto,,inclusive,,,,per person sharing",
    )
    await _import(db, path)
    rate = (await _rates(db, name))[0]
    assert rate.rate_per_night == D("18000")  # 9,000 x 2 guests


async def test_a_vat_exclusive_row_is_grossed_up(db, tmp_path):
    """56 rows in the corpus say exclusive. Nothing downstream adds tax, so
    storing them as typed under-charges by the whole 16%."""
    name = unique("Exclusive Lodge")
    path = sheet(
        tmp_path,
        f"RATE,{name},Testland,Room,2,FB,citizen,2,Standard,01/07/2026,31/10/2026,"
        "KES,20000,room_per_night,rack,,exclusive,,,,",
    )
    await _import(db, path)
    rate = (await _rates(db, name))[0]
    assert rate.rate_per_night == D("23200")
    assert rate.vat_inclusive is True


async def test_both_transforms_compose_in_the_right_order(db, tmp_path):
    """Per-person conversion then VAT, on a merged pair, and the discount is
    unaffected by either because both figures scale together. This is the
    Mukima Manor shape: 450/360 per person sharing, VAT-exclusive."""
    name = unique("Compose Manor")
    path = sheet(
        tmp_path,
        f"RATE,{name},Testland,Suite,2,FB,non_resident,2,Standard,11/01/2026,"
        "19/12/2026,USD,450,person_per_night,rack,,exclusive,,,,",
        f"RATE,{name},Testland,Suite,2,FB,non_resident,2,Standard,11/01/2026,"
        "19/12/2026,USD,360,person_per_night,sto,,exclusive,,,,",
    )
    await _import(db, path)
    rate = (await _rates(db, name))[0]
    # 450 per person x 2 guests = 900 per room, x 1.16 VAT = 1,044
    assert rate.rate_per_night == D("1044")
    assert rate.supplier_discount_pct == D("20")


async def test_a_row_with_no_validity_window_is_rejected_not_defaulted(db, tmp_path):
    """134 rows in the corpus. A guessed window is a price the supplier never
    quoted, and it would price real quotes."""
    name = unique("No Dates Inn")
    path = sheet(
        tmp_path,
        f"RATE,{name},Testland,Room,2,FB,citizen,2,Standard,,,KES,15000,"
        "room_per_night,sto,,inclusive,,,,",
    )
    report = await _import(db, path)
    assert report.rates_created == 0
    fields = {problem.field for problem in report.rejected}
    assert fields == {"valid_from", "valid_to"}
    assert report.rejected_rows == 1, "one row, two problems"


async def test_rejected_rows_are_counted_as_rows_not_problems(db, tmp_path):
    """A row missing both dates is one rejection, not two. Counting problems
    overstated the damage by a third on the real sheet."""
    name = unique("Count Inn")
    path = sheet(
        tmp_path,
        f"RATE,{name},Testland,Room,2,FB,citizen,2,Standard,,,KES,15000,"
        "room_per_night,sto,,inclusive,,,,",
    )
    report = await _import(db, path, commit=False)
    assert report.total_rows == 1
    assert len(report.rejected) == 2
    assert report.rejected_rows == 1
    assert report.accepted == 0


async def test_a_rejection_names_the_property_not_just_the_row(db, tmp_path):
    """Nobody fixes "row 1039"; they fix a rate sheet."""
    name = unique("Named Inn")
    path = sheet(
        tmp_path,
        f"RATE,{name},Testland,Room,2,FB,citizen,,Standard,01/07/2026,31/10/2026,"
        "KES,15000,room_per_night,sto,,inclusive,,,,",
    )
    report = await _import(db, path, commit=False)
    assert report.rejected[0].property_name == name
    assert report.rejected[0].field == "price_covers"


async def test_capacity_comes_from_the_largest_occupancy_priced(db, tmp_path):
    """Only 416 of 3,016 rate rows state room_sleeps, and where they do they
    mirror price_covers rather than stating capacity. Both columns are lower
    bounds; the largest wins, and erring low over-quotes visibly rather than
    under-quoting silently."""
    name = unique("Capacity Lodge")
    common = f"RATE,{name},Testland,Family Room,,FB,citizen"
    path = sheet(
        tmp_path,
        f"{common},2,Standard,01/07/2026,31/10/2026,KES,20000,room_per_night,sto,,inclusive,,,,",
        f"{common},4,Standard,01/07/2026,31/10/2026,KES,30000,room_per_night,sto,,inclusive,,,,",
    )
    report = await _import(db, path)
    room = (
        await db.execute(
            select(RoomType)
            .join(Accommodation, RoomType.accommodation_id == Accommodation.id)
            .where(Accommodation.name == name)
        )
    ).scalar_one()
    assert room.max_occupancy == 4
    assert any("Family Room" in note for note in report.derived_capacity)


async def test_a_supplement_needs_a_name(db, tmp_path):
    """The label is what the client reads. Defaulting it printed the word
    "Standard" on a proposal and silently collapsed distinct extras onto one
    key, dropping 43 of them."""
    name = unique("Unnamed Extras")
    path = sheet(
        tmp_path,
        f"EXTRA,{name},Testland,,,,,,,01/07/2026,31/10/2026,KES,4500,"
        "person_per_stay,,,inclusive,,,,",
    )
    report = await _import(db, path, commit=False)
    assert {p.field for p in report.rejected} == {"label"}


async def test_a_supplement_and_an_optional_extra_are_told_apart(db, tmp_path):
    """SUPPLEMENT is charged whether asked for or not; EXTRA only if chosen.
    row_type carries that, which is why is_mandatory is not a column."""
    name = unique("Festive Lodge")
    path = sheet(
        tmp_path,
        f"SUPPLEMENT,{name},Testland,,,,,,Gala dinner,31/12/2026,31/12/2026,KES,"
        "7500,person_per_stay,,,inclusive,,,,",
        f"EXTRA,{name},Testland,,,,,,Dhow cruise,01/07/2026,31/10/2026,KES,"
        "4500,person_per_stay,,,inclusive,,,,",
    )
    report = await _import(db, path)
    assert report.supplements_created == 2
    rows = list(
        (
            await db.execute(
                select(AccommodationSupplement)
                .join(
                    Accommodation,
                    AccommodationSupplement.accommodation_id == Accommodation.id,
                )
                .where(Accommodation.name == name)
                .order_by(AccommodationSupplement.label)
            )
        )
        .scalars()
        .all()
    )
    by_label = {row.label: row for row in rows}
    assert by_label["Gala dinner"].is_mandatory is True
    assert by_label["Dhow cruise"].is_mandatory is False
    assert by_label["Gala dinner"].basis == "per_person"


async def test_a_re_import_corrects_rather_than_duplicating(db, tmp_path):
    """A corrected sheet has to be re-importable. The uniqueness key would
    otherwise raise deep inside the flush."""
    name = unique("Reimport Inn")
    first = sheet(
        tmp_path,
        f"RATE,{name},Testland,Room,2,FB,citizen,2,Standard,01/07/2026,31/10/2026,"
        "KES,20000,room_per_night,sto,,inclusive,,,,",
        name="first.csv",
    )
    report = await _import(db, first)
    assert report.rates_created == 1

    second = sheet(
        tmp_path,
        f"RATE,{name},Testland,Room,2,FB,citizen,2,Standard,01/07/2026,31/10/2026,"
        "KES,21500,room_per_night,sto,,inclusive,,,,",
        name="second.csv",
    )
    report = await _import(db, second)
    assert report.rates_created == 0
    assert report.rates_updated == 1
    rates = await _rates(db, name)
    assert len(rates) == 1
    assert rates[0].rate_per_night == D("21500")


async def test_a_conflicting_sheet_of_dates_imports_nothing(db, tmp_path):
    """If a sheet mixes day-first and month-first, every date in it is suspect."""
    name = unique("Mixed Dates")
    path = sheet(
        tmp_path,
        f"RATE,{name},Testland,Room,2,FB,citizen,2,Standard,19/12/2026,31/12/2026,"
        "KES,20000,room_per_night,sto,,inclusive,,,,",
        f"RATE,{name},Testland,Room,2,FB,citizen,1,Standard,12/19/2026,12/31/2026,"
        "KES,15000,room_per_night,sto,,inclusive,,,,",
    )
    report = await _import(db, path)
    assert report.date_order == "conflicting"
    assert report.rates_created == 0
    assert any("BOTH day-first" in note for note in report.warnings)


async def test_the_meal_plan_and_residence_have_to_exist_in_this_database(
    db, tmp_path
):
    name = unique("Unknown Plan Inn")
    path = sheet(
        tmp_path,
        f"RATE,{name},Testland,Room,2,BRUNCH,citizen,2,Standard,01/07/2026,"
        "31/10/2026,KES,20000,room_per_night,sto,,inclusive,,,,",
    )
    report = await _import(db, path, commit=False)
    assert {p.field for p in report.rejected} == {"meal_plan"}


async def test_a_child_band_and_rate_survive_the_round_trip(db, tmp_path):
    name = unique("Family Lodge")
    path = sheet(
        tmp_path,
        f"RATE,{name},Testland,Room,2,FB,citizen,2,Standard,01/07/2026,31/10/2026,"
        "KES,20000,room_per_night,sto,,inclusive,8000,04-11,2,",
    )
    await _import(db, path)
    rate = (await _rates(db, name))[0]
    assert rate.child_rate == D("8000")
    assert (rate.child_min_age, rate.child_max_age) == (4, 11)
    assert rate.min_nights == 2


async def test_a_property_and_its_destination_are_created_once(db, tmp_path):
    name = unique("New Place Lodge")
    destination = unique("Newland")
    path = sheet(
        tmp_path,
        f"RATE,{name},{destination},Room,2,FB,citizen,2,Standard,01/07/2026,"
        "31/10/2026,KES,20000,room_per_night,sto,,inclusive,,,,",
        f"RATE,{name},{destination},Room,2,HB,citizen,2,Standard,01/07/2026,"
        "31/10/2026,KES,18000,room_per_night,sto,,inclusive,,,,",
    )
    report = await _import(db, path)
    assert report.properties_created == [name]
    assert report.destinations_created == [destination]
    assert report.room_types_created == 1
    assert report.rates_created == 2


async def test_a_rate_priced_per_stay_is_refused(db, tmp_path):
    """The catalogue stores nightly rates. A per-stay figure is not one, and
    dividing it by a guessed night count would invent a price."""
    name = unique("Per Stay Inn")
    path = sheet(
        tmp_path,
        f"RATE,{name},Testland,Room,2,FB,citizen,2,Standard,01/07/2026,31/10/2026,"
        "KES,60000,room_per_stay,sto,,inclusive,,,,",
    )
    report = await _import(db, path, commit=False)
    assert {p.field for p in report.rejected} == {"charged_per"}


async def test_the_sheet_can_be_an_xlsx(db, tmp_path):
    """The template is CSV; the data arrives as .xlsx, because agents open it in
    Excel and save. Real Excel dates come through as datetimes, which is the
    case a hand-rolled reader gets wrong."""
    openpyxl = pytest.importorskip("openpyxl")
    name = unique("Excel Lodge")
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.append(HEADER.split(","))
    worksheet.append(
        [
            "RATE", name, "Testland", "Room", 2, "B&B", "Non-Resident", 2,
            "Standard", date(2026, 7, 1), date(2026, 10, 31), "USD", 250,
            "room_per_night", "STO", None, "inclusive", None, None, None, None,
        ]
    )
    path = tmp_path / "book.xlsx"
    workbook.save(path)

    report = await _import(db, path)
    assert report.rates_created == 1
    rate = (await _rates(db, name))[0]
    assert rate.effective_from == date(2026, 7, 1)
    assert rate.rate_per_night == D("250")
    assert rate.rate_kind == "sto"
    plan = (
        await db.execute(select(MealPlan).where(MealPlan.id == rate.meal_plan_id))
    ).scalar_one()
    assert plan.code == "BB"
    residence = (
        await db.execute(
            select(ResidenceCategory).where(
                ResidenceCategory.id == rate.residence_category_id
            )
        )
    ).scalar_one()
    assert residence.key == "non_resident"


# --------------------------------------------------------------------------- #
# Shapes found in the client's second (audited) workbook, 2026-09-02
# --------------------------------------------------------------------------- #


def test_a_blank_row_type_means_rate():
    """All 64 Temple Point rows in the audited workbook arrived with an empty
    first column. A row carrying a room, a plan and an amount is a rate, and the
    two passes over a sheet have to agree about that — one defaulted and the
    other did not, so a property's rates imported while its room capacities were
    inferred from an empty set."""
    assert N.row_kind({"row_type": ""}) == "RATE"
    assert N.row_kind({"row_type": "  "}) == "RATE"
    assert N.row_kind({"row_type": "supplement"}) == "SUPPLEMENT"
    assert N.row_kind({"row_type": "Extra"}) == "EXTRA"


async def test_a_blank_row_type_still_feeds_capacity_inference(db, tmp_path):
    """The regression the shared helper exists to prevent: with the row skipped
    by the capacity pass, a four-sleeper villa fell back to the default two and
    booked twice the rooms it needed."""
    name = unique("Blank Type Villas")
    path = sheet(
        tmp_path,
        f",{name},Testland,Villa,,FB,citizen,4,Standard,01/07/2026,31/10/2026,"
        "KES,40000,room_per_night,sto,,inclusive,,,,",
    )
    report = await _import(db, path)
    assert report.rates_created == 1
    room = (
        await db.execute(
            select(RoomType)
            .join(Accommodation, RoomType.accommodation_id == Accommodation.id)
            .where(Accommodation.name == name)
        )
    ).scalar_one()
    assert room.max_occupancy == 4
    assert f"{name} / Villa" in report.derived_capacity


async def test_one_room_night_in_three_currencies_is_three_rates(db, tmp_path):
    """Kobe Suite Resort publishes every night in KES, USD and EUR and expects
    the agent to bill in whichever the client is invoiced in — 30 groups, 90
    rows. Under the old key those were a collision, and which currency survived
    depended on spreadsheet row order; the survivor could be the EUR figure,
    for which there is no exchange rate on file."""
    name = unique("Tri Currency Suites")
    row = (
        f"RATE,{name},Testland,Suite,2,BB,non_resident,2,Standard,01/07/2026,"
        "31/10/2026,{ccy},{amount},room_per_night,sto,,inclusive,,,,"
    )
    path = sheet(
        tmp_path,
        row.format(ccy="KES", amount=28105),
        row.format(ccy="USD", amount=281),
        row.format(ccy="EUR", amount=256),
    )
    report = await _import(db, path)
    assert report.rates_created == 3
    assert report.conflicts == []
    stored = {r.currency: r.rate_per_night for r in await _rates(db, name)}
    assert stored == {"KES": D("28105"), "USD": D("281"), "EUR": D("256")}


async def test_a_day_of_week_pair_keeps_the_higher_figure_and_says_so(db, tmp_path):
    """One Stop Nanyuki charges 10,000 Sunday-Thursday and 13,500 on Friday and
    Saturday for the same hut. The schema has no weekday column, so the
    distinction cannot be honoured.

    Dropping the rows makes the property unquotable. Keeping the first depends
    on row order, and at One Stop that is the cheaper figure, so every weekend
    stay would under-charge by 35% with nothing to show it. Keeping the higher
    over-quotes a weeknight *visibly*, where the agent can catch it against the
    sheet — the same direction of error as capacity inference.
    """
    name = unique("Weekday Huts")
    row = (
        f"RATE,{name},Testland,Hut,2,RO,non_resident,1,{{label}},05/01/2026,"
        "04/01/2027,KES,{amount},room_per_night,rack,,inclusive,,,,"
    )
    path = sheet(
        tmp_path,
        row.format(label="Weeknights (Sunday-Thursday)", amount=10000),
        row.format(label="Weekends (Friday & Saturday)", amount=13500),
    )
    report = await _import(db, path)
    assert report.rates_created == 1
    assert report.conflicts == []
    assert len(report.label_variants) == 1
    note = report.label_variants[0]
    assert "10000" in note and "13500" in note and "kept 13500" in note
    assert (await _rates(db, name))[0].rate_per_night == D("13500")


async def test_two_rates_under_one_label_stay_a_conflict(db, tmp_path):
    """The escape hatch above must not swallow a genuine ambiguity. The One
    Watamu Bay prices one room-night at 13,500 per person *and* 27,000 per room
    for a single guest, under the identical label — a factor of two with nothing
    to choose between them. Two rows sharing a label are not a day-of-week pair.
    """
    name = unique("Ambiguous Bay")
    row = (
        f"RATE,{name},Testland,Ocean Front,2,HB,non_resident,1,10 Jul - 25 Oct,"
        "10/07/2026,25/10/2026,KES,{amount},{basis},sto,,inclusive,,,,"
    )
    path = sheet(
        tmp_path,
        row.format(amount=13500, basis="person_per_night"),
        row.format(amount=27000, basis="room_per_night"),
    )
    report = await _import(db, path)
    assert report.rates_created == 0
    assert report.label_variants == []
    assert len(report.conflicts) == 1
