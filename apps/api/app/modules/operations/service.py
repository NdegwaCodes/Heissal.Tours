"""Assigning vehicles and crew, and the departure board (§8.1).

The rules are in :mod:`app.modules.operations.roster`; this is the half that
talks to the database. Four things worth reading before changing anything.

**A clash is refused, and an override is recorded.** Two trips over the same
days is one trip that does not happen, so the default is no. But an operator
who knows the first booking is about to be cancelled needs a way through, and
the way through leaves a reason and a name on the row — the same shape as
§5.3's amendment: not "can this be got round" but "who decided, and why".

**Only an active booking can be crewed.** A cancelled trip holds nothing, and
cancelling one releases what it held — otherwise the fleet calendar fills up
with vehicles nobody is using and an operator stops believing it.

**The departure board is one query, not one per booking.** It is the §5.2
lesson about the morning list: a list that takes four seconds to build is a
list nobody opens on a Monday.

**Nothing here decides a trip is ready.** It reports what is missing. Whether a
trip with no guide is a problem depends on whether the client asked for one.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFoundError
from app.modules.bookings.models import ACTIVE_STATUSES, Booking
from app.modules.operations.actuals import (
    DEFAULT_TOLERANCE_PCT,
    Actual,
    Fill,
    FleetTruth,
    LogRefused,
    Odometer,
    audit,
    check_fill,
    check_reading,
    measure,
)
from app.modules.operations.models import (
    VEHICLE,
    CrewMember,
    FuelFill,
    SupplierBooking,
    TripAssignment,
    TripLog,
)
from app.modules.operations.roster import (
    CREW_ROLES,
    DRIVES,
    GUIDES,
    AssignmentRefused,
    Clash,
    Crew,
    Fleet,
    Gap,
    Held,
    Roster,
    Window,
    check_crew,
    check_ready,
    check_vehicle,
    clashes,
    sort_gaps,
)
from app.modules.operations.supply import (
    CANCELLED,
    CONFIRMED,
    DEFAULT_CONFIRM_BY_DAYS,
    REQUESTED,
    TO_REQUEST,
    Committed,
    Concern,
    Exposure,
    SupplyRefused,
    check_invoices,
    check_supply,
    check_transition,
    exposure,
    normalise_status,
    unsettled,
)
from app.modules.suppliers.models import Supplier
from app.modules.vehicles.models import Vehicle


def normalise_roles(values: Sequence[str] | None) -> list[str]:
    """Crew roles as the rules expect them, refusing anything else.

    Refused rather than dropped: a person saved with a typo'd role would be
    silently unassignable, and the failure would surface as "why can I not put
    Joseph on this trip" three weeks later.
    """
    cleaned = []
    for value in values or []:
        role = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
        if role not in CREW_ROLES:
            raise AppError(
                f"'{value}' is not something somebody does on a trip. Say "
                f"{', '.join(CREW_ROLES)}."
            )
        if role not in cleaned:
            cleaned.append(role)
    if not cleaned:
        raise AppError(
            "Say what this person does — driver, guide, or driver_guide. "
            "Somebody with no role cannot be put on a trip at all."
        )
    return cleaned


class CrewService:
    """The register of drivers and guides."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: dict) -> CrewMember:
        data = dict(data)
        data["roles"] = normalise_roles(data.get("roles"))
        row = CrewMember(**data)
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def get(self, crew_id: uuid.UUID) -> CrewMember:
        row = await self.db.get(CrewMember, crew_id)
        if row is None:
            raise NotFoundError("Crew member not found.")
        return row

    async def list(
        self, *, role: str | None = None, active_only: bool = True
    ) -> list[CrewMember]:
        stmt = select(CrewMember).order_by(CrewMember.name)
        if active_only:
            stmt = stmt.where(CrewMember.is_active.is_(True))
        rows = list((await self.db.execute(stmt)).scalars().all())
        if role:
            wanted = DRIVES if role == "driver" else GUIDES if role == "guide" else (role,)
            rows = [one for one in rows if set(one.roles) & set(wanted)]
        return rows

    async def update(self, crew_id: uuid.UUID, data: dict) -> CrewMember:
        row = await self.get(crew_id)
        if "roles" in data and data["roles"] is not None:
            data["roles"] = normalise_roles(data["roles"])
        for field, value in data.items():
            setattr(row, field, value)
        await self.db.commit()
        await self.db.refresh(row)
        return row


class AssignmentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # -- committing something to a trip --------------------------------------- #

    async def assign(
        self,
        booking_id: uuid.UUID,
        *,
        vehicle_id: uuid.UUID | None = None,
        crew_id: uuid.UUID | None = None,
        role: str | None = None,
        starts_on: date | None = None,
        ends_on: date | None = None,
        notes: str | None = None,
        override_reason: str | None = None,
        actor_id: uuid.UUID | None = None,
    ) -> tuple[TripAssignment, list[Clash]]:
        """Put a vehicle or a person on a booking, and say what it clashed with.

        Returns the advisory clashes as well as the row: a same-day handover is
        not a refusal but it is worth an operator's eye, and swallowing it
        would make the tight one indistinguishable from the comfortable one.
        """
        booking = await self.db.get(Booking, booking_id)
        if booking is None:
            raise NotFoundError("Booking not found.")
        if booking.status not in ACTIVE_STATUSES:
            raise AppError(
                f"Booking {booking.reference} is {booking.status}, so there is "
                f"no trip to crew. Cancelling a booking releases what it held."
            )
        if (vehicle_id is None) == (crew_id is None):
            raise AppError(
                "An assignment is one vehicle or one person, not both and not "
                "neither."
            )

        window = Window(
            starts_on=starts_on or booking.arrival_date,
            ends_on=ends_on or booking.departure_date,
        )

        try:
            if vehicle_id is not None:
                found = await self._vehicle(vehicle_id)
                check_vehicle(found)
                subject, kind = found.name, VEHICLE
            else:
                assert crew_id is not None
                member = await self.get_crew(crew_id)
                kind = (role or "").strip().lower() or _default_role(member)
                check_crew(_crew(member), kind, window)
                subject = member.name
        except AssignmentRefused as exc:
            raise AppError(str(exc)) from exc

        held = await self._held(vehicle_id=vehicle_id, crew_id=crew_id)
        found_clashes = clashes(window, held, subject=subject)
        blocking = [one for one in found_clashes if one.blocking]
        if blocking and not (override_reason or "").strip():
            raise AppError(
                blocking[0].message
                + " Assign something else, or say why you are overriding it."
            )

        row = TripAssignment(
            booking_id=booking.id,
            vehicle_id=vehicle_id,
            crew_id=crew_id,
            role=kind,
            starts_on=window.starts_on,
            ends_on=window.ends_on,
            notes=notes,
            override_reason=(override_reason or "").strip() or None,
            assigned_by=actor_id,
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row, [one for one in found_clashes if not one.blocking]

    async def release(self, assignment_id: uuid.UUID) -> None:
        """Take something off a trip.

        A hard delete, unlike §5.3's log. An assignment is a *plan*, not a
        record of something that happened — nobody needs the history of a
        vehicle that was pencilled in on Tuesday and swapped on Wednesday, and
        keeping it would make "what is out on the 5th" a query that has to
        exclude ghosts.
        """
        row = await self.db.get(TripAssignment, assignment_id)
        if row is None:
            raise NotFoundError("Assignment not found.")
        await self.db.delete(row)
        await self.db.commit()

    async def for_booking(self, booking_id: uuid.UUID) -> list[TripAssignment]:
        return list(
            (
                await self.db.execute(
                    select(TripAssignment)
                    .where(TripAssignment.booking_id == booking_id)
                    .order_by(TripAssignment.starts_on, TripAssignment.role)
                )
            )
            .scalars()
            .all()
        )

    # -- what is missing ------------------------------------------------------ #

    async def readiness(
        self, booking_id: uuid.UUID, *, today: date | None = None
    ) -> list[Gap]:
        """What still stands between this booking and a trip that can leave."""
        booking = await self.db.get(Booking, booking_id)
        if booking is None:
            raise NotFoundError("Booking not found.")
        rosters = await self._rosters([booking])
        return sort_gaps(
            check_ready(rosters[booking.id], today=today or date.today())
        )

    async def departures(
        self, *, days: int = 14, today: date | None = None
    ) -> list[tuple[Booking, Roster, list[Gap]]]:
        """Everything leaving in the next ``days``, and what it is missing.

        The operational half of §5.2's morning list. Built in a fixed number of
        queries however many bookings it covers, for the same reason: a list
        that takes four seconds is a list nobody opens on a Monday.
        """
        when = today or date.today()
        horizon = when.toordinal() + max(days, 0)
        bookings = list(
            (
                await self.db.execute(
                    select(Booking)
                    .where(
                        Booking.status.in_(ACTIVE_STATUSES),
                        Booking.arrival_date >= when,
                        Booking.arrival_date <= date.fromordinal(horizon),
                    )
                    .order_by(Booking.arrival_date)
                )
            )
            .scalars()
            .all()
        )
        rosters = await self._rosters(bookings)
        out = []
        for booking in bookings:
            roster = rosters[booking.id]
            out.append(
                (booking, roster, sort_gaps(check_ready(roster, today=when)))
            )
        return out

    async def diary(
        self,
        *,
        starts_on: date,
        ends_on: date,
        vehicle_id: uuid.UUID | None = None,
        crew_id: uuid.UUID | None = None,
    ) -> list[TripAssignment]:
        """What is committed over a span — the fleet and crew calendar.

        The query an operator makes before promising anything: "what have I got
        free that week?" is really "what is already out".
        """
        window = Window(starts_on=starts_on, ends_on=ends_on)
        stmt = select(TripAssignment).where(
            TripAssignment.starts_on <= window.ends_on,
            TripAssignment.ends_on >= window.starts_on,
        )
        if vehicle_id is not None:
            stmt = stmt.where(TripAssignment.vehicle_id == vehicle_id)
        if crew_id is not None:
            stmt = stmt.where(TripAssignment.crew_id == crew_id)
        return list(
            (
                await self.db.execute(
                    stmt.order_by(TripAssignment.starts_on, TripAssignment.role)
                )
            )
            .scalars()
            .all()
        )

    # -- plumbing ------------------------------------------------------------- #

    async def get_crew(self, crew_id: uuid.UUID) -> CrewMember:
        row = await self.db.get(CrewMember, crew_id)
        if row is None:
            raise NotFoundError("Crew member not found.")
        return row

    async def _vehicle(self, vehicle_id: uuid.UUID) -> Fleet:
        row = await self.db.get(Vehicle, vehicle_id)
        if row is None:
            raise NotFoundError("Vehicle not found.")
        return Fleet(
            name=row.name,
            vehicle_type=row.vehicle_type,
            passenger_capacity=row.passenger_capacity,
            is_active=row.is_active,
        )

    async def _held(
        self, *, vehicle_id: uuid.UUID | None, crew_id: uuid.UUID | None
    ) -> list[Held]:
        """Everything this vehicle or person is already committed to.

        Only on **active** bookings: a cancelled trip holds nothing, and a
        fleet calendar that says otherwise fills up with vehicles nobody is
        using until an operator stops believing it.
        """
        stmt = (
            select(TripAssignment, Booking.reference)
            .join(Booking, Booking.id == TripAssignment.booking_id)
            .where(Booking.status.in_(ACTIVE_STATUSES))
        )
        if vehicle_id is not None:
            stmt = stmt.where(TripAssignment.vehicle_id == vehicle_id)
        else:
            stmt = stmt.where(TripAssignment.crew_id == crew_id)
        return [
            Held(
                window=Window(starts_on=row.starts_on, ends_on=row.ends_on),
                reference=reference,
                booking_id=row.booking_id,
            )
            for row, reference in (await self.db.execute(stmt)).all()
        ]

    async def _rosters(
        self, bookings: Sequence[Booking]
    ) -> dict[uuid.UUID, Roster]:
        """Who and what is on each of these bookings, in three queries."""
        ids = [booking.id for booking in bookings]
        out = {
            booking.id: Roster(
                reference=booking.reference,
                departs_on=booking.arrival_date,
                pax_count=booking.pax_count,
            )
            for booking in bookings
        }
        if not ids:
            return out

        rows = list(
            (
                await self.db.execute(
                    select(TripAssignment).where(
                        TripAssignment.booking_id.in_(ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return out

        vehicles = {
            row.id: row
            for row in (
                await self.db.execute(
                    select(Vehicle).where(
                        Vehicle.id.in_(
                            [one.vehicle_id for one in rows if one.vehicle_id]
                        )
                    )
                )
            )
            .scalars()
            .all()
        }
        crew = {
            row.id: row
            for row in (
                await self.db.execute(
                    select(CrewMember).where(
                        CrewMember.id.in_(
                            [one.crew_id for one in rows if one.crew_id]
                        )
                    )
                )
            )
            .scalars()
            .all()
        }

        for row in rows:
            roster = out.get(row.booking_id)
            if roster is None:
                continue
            if row.vehicle_id is not None:
                found = vehicles.get(row.vehicle_id)
                if found is not None:
                    roster.vehicles.append(
                        Fleet(
                            name=found.name,
                            vehicle_type=found.vehicle_type,
                            passenger_capacity=found.passenger_capacity,
                            is_active=found.is_active,
                        )
                    )
            elif row.crew_id is not None:
                member = crew.get(row.crew_id)
                if member is None:
                    continue
                # By the role on the **assignment**, not by what the person is
                # capable of: a driver-guide sent out purely to guide is not a
                # driver on this trip, and counting them as one would report a
                # trip with nobody at the wheel as ready.
                if row.role in DRIVES:
                    roster.drivers.append(_crew(member))
                if row.role in GUIDES:
                    roster.guides.append(_crew(member))
        return out


class TripLogService:
    """What a vehicle actually did, and whether the model is telling the truth.

    Two things worth reading before changing anything.

    **A log is opened and closed, not written in one go.** The vehicle leaves
    on Monday and comes back on Friday, and the days in between are exactly
    when somebody wants to know where it is. So ``odometer_in_km`` is nullable
    and an open log is a visible state rather than a missing row.

    **The audit reports and never applies.** It will tell you a vehicle priced
    at 8.5 km/L has managed 6.9 over nine trips. It will not change the 8.5:
    that is a live pricing input, moving it re-prices work in flight, and
    deciding a fortnight of receipts is the new truth belongs to whoever will
    have to explain the margin.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def open(
        self,
        assignment_id: uuid.UUID,
        *,
        odometer_out_km: Decimal,
        started_on: date | None = None,
        driver_id: uuid.UUID | None = None,
        notes: str | None = None,
        actor_id: uuid.UUID | None = None,
    ) -> tuple[TripLog, list[str]]:
        """Record a vehicle leaving, and say what the odometer implies.

        Returns the observations as well as the row: kilometres between this
        reading and the vehicle's last return are not an error — repositioning,
        a service run — but they are worth somebody knowing about, and a
        response that says only "created" buries them.
        """
        assignment = await self.db.get(TripAssignment, assignment_id)
        if assignment is None:
            raise NotFoundError("Assignment not found.")
        if assignment.vehicle_id is None:
            raise AppError(
                "A trip log belongs to a vehicle. This assignment is a person, "
                "and people do not have odometers."
            )
        existing = (
            await self.db.execute(
                select(TripLog).where(TripLog.assignment_id == assignment_id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise AppError(
                f"This vehicle already has a log on booking "
                f"{await self._reference(assignment.booking_id)}. A second "
                f"would double the distance in every fleet average."
            )

        previous = await self._last_closing(assignment.vehicle_id)
        try:
            notes_out = check_reading(
                Odometer(out_km=odometer_out_km), previous_in=previous
            )
        except LogRefused as exc:
            raise AppError(str(exc)) from exc

        row = TripLog(
            assignment_id=assignment.id,
            vehicle_id=assignment.vehicle_id,
            booking_id=assignment.booking_id,
            odometer_out_km=odometer_out_km,
            started_on=started_on or assignment.starts_on,
            driver_id=driver_id,
            notes=notes,
            recorded_by=actor_id,
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row, notes_out

    async def close(
        self,
        log_id: uuid.UUID,
        *,
        odometer_in_km: Decimal,
        ended_on: date | None = None,
    ) -> TripLog:
        """Record a vehicle coming back.

        An ``in`` below the ``out`` is refused rather than stored: a negative
        distance in a fleet average poisons every figure derived from it, and
        it is always a typed digit rather than a fact.
        """
        row = await self.get(log_id)
        try:
            check_reading(
                Odometer(out_km=row.odometer_out_km, in_km=odometer_in_km)
            )
        except LogRefused as exc:
            raise AppError(str(exc)) from exc
        row.odometer_in_km = odometer_in_km
        row.ended_on = ended_on or date.today()
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def fill(
        self,
        log_id: uuid.UUID,
        *,
        litres: Decimal,
        amount: Decimal,
        currency: str,
        bought_on: date | None = None,
        station: str | None = None,
        receipt_ref: str | None = None,
        notes: str | None = None,
        actor_id: uuid.UUID | None = None,
    ) -> FuelFill:
        """Record a fuel receipt. Litres and money, both from the paper."""
        row = await self.get(log_id)
        when = bought_on or date.today()
        try:
            check_fill(
                Fill(
                    litres=litres,
                    amount=amount,
                    currency=currency,
                    bought_on=when,
                )
            )
        except LogRefused as exc:
            raise AppError(str(exc)) from exc
        fill = FuelFill(
            trip_log_id=row.id,
            litres=litres,
            amount=amount,
            currency=currency.upper(),
            bought_on=when,
            station=station,
            receipt_ref=receipt_ref,
            notes=notes,
            recorded_by=actor_id,
        )
        self.db.add(fill)
        await self.db.commit()
        await self.db.refresh(fill)
        return fill

    async def get(self, log_id: uuid.UUID) -> TripLog:
        row = await self.db.get(TripLog, log_id)
        if row is None:
            raise NotFoundError("Trip log not found.")
        return row

    async def for_booking(self, booking_id: uuid.UUID) -> list[TripLog]:
        return list(
            (
                await self.db.execute(
                    select(TripLog)
                    .where(TripLog.booking_id == booking_id)
                    .order_by(TripLog.started_on)
                )
            )
            .scalars()
            .all()
        )

    async def measured(self, log: TripLog) -> Actual:
        """One log folded into a row of truth, beside the model's prediction."""
        vehicle = await self.db.get(Vehicle, log.vehicle_id)
        try:
            return measure(
                odometer=Odometer(
                    out_km=log.odometer_out_km, in_km=log.odometer_in_km
                ),
                fills=[_fill(one) for one in log.fills],
                model_kmpl=vehicle.fuel_consumption_kmpl if vehicle else None,
            )
        except LogRefused as exc:
            raise AppError(str(exc)) from exc

    async def audit_vehicle(
        self,
        vehicle_id: uuid.UUID,
        *,
        since: date | None = None,
        tolerance_pct: Decimal | None = None,
    ) -> FleetTruth:
        """What a run of receipts says about the number every quote is priced on."""
        vehicle = await self.db.get(Vehicle, vehicle_id)
        if vehicle is None:
            raise NotFoundError("Vehicle not found.")
        stmt = select(TripLog).where(
            TripLog.vehicle_id == vehicle_id, TripLog.odometer_in_km.is_not(None)
        )
        if since is not None:
            stmt = stmt.where(TripLog.started_on >= since)
        logs = list((await self.db.execute(stmt)).scalars().all())
        actuals = []
        for log in logs:
            try:
                actuals.append(
                    measure(
                        odometer=Odometer(
                            out_km=log.odometer_out_km, in_km=log.odometer_in_km
                        ),
                        fills=[_fill(one) for one in log.fills],
                        model_kmpl=vehicle.fuel_consumption_kmpl,
                    )
                )
            except LogRefused:
                # A trip fuelled in two currencies cannot be pooled into a
                # single consumption figure, and dropping it is better than
                # picking a rate. It stays visible on its own trip.
                continue
        return audit(
            vehicle.name,
            actuals,
            model_kmpl=vehicle.fuel_consumption_kmpl,
            tolerance_pct=(
                tolerance_pct if tolerance_pct is not None else DEFAULT_TOLERANCE_PCT
            ),
        )

    async def audit_fleet(
        self, *, since: date | None = None
    ) -> list[FleetTruth]:
        """Every active vehicle, worst model error first.

        Sorted so the report opens on the vehicle costing the most money — the
        §5.2 argument about a list nobody works through, applied to a fleet.
        """
        vehicles = list(
            (
                await self.db.execute(
                    select(Vehicle).where(Vehicle.is_active.is_(True))
                )
            )
            .scalars()
            .all()
        )
        out = [
            await self.audit_vehicle(vehicle.id, since=since)
            for vehicle in vehicles
        ]
        return sorted(out, key=_worst_first)

    async def _last_closing(self, vehicle_id: uuid.UUID) -> Decimal | None:
        """This vehicle's most recent closing reading, for continuity."""
        return (
            await self.db.execute(
                select(TripLog.odometer_in_km)
                .where(
                    TripLog.vehicle_id == vehicle_id,
                    TripLog.odometer_in_km.is_not(None),
                )
                .order_by(TripLog.odometer_in_km.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _reference(self, booking_id: uuid.UUID) -> str:
        booking = await self.db.get(Booking, booking_id)
        return booking.reference if booking else ""


def _fill(row: FuelFill) -> Fill:
    return Fill(
        litres=row.litres,
        amount=row.amount,
        currency=row.currency,
        bought_on=row.bought_on,
    )


def _worst_first(truth: FleetTruth) -> tuple:
    """Most under-costed vehicle first; the ones with no finding last."""
    variances = [
        one.variance_pct for one in truth.findings if one.variance_pct is not None
    ]
    return (min(variances) if variances else Decimal(0), truth.vehicle)


class SupplyService:
    """What we owe a trip's suppliers, and whether they have said yes (§8.3).

    Three things worth reading before changing anything.

    **Confirming needs their reference.** A confirmation with no booking number
    is somebody's recollection of a phone call, and it is exactly the row that
    turns out to be wrong on the day. Refused in the rules, not warned about.

    **The expected figure is typed, never derived.** The snapshot holds one
    ``supplier_paid_total`` for a whole option across every supplier on it, so
    splitting it per hotel would be a guess dressed as a fact.

    **Nothing is summed across currencies.** A lodge billing in dollars beside
    a transfer company billing in shillings is the normal case here.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def add(
        self,
        booking_id: uuid.UUID,
        *,
        supplier_id: uuid.UUID,
        service: str,
        expected_amount: Decimal,
        currency: str,
        check_in: date | None = None,
        check_out: date | None = None,
        confirm_by: date | None = None,
        notes: str | None = None,
        actor_id: uuid.UUID | None = None,
    ) -> SupplierBooking:
        """Record something we are buying for this trip.

        Created ``to_request``: the row exists the moment somebody knows the
        trip needs a hotel, which is well before anybody has rung it. A row
        that only appeared once it was confirmed would be a list of things
        already done rather than a list of things to do.
        """
        booking = await self.db.get(Booking, booking_id)
        if booking is None:
            raise NotFoundError("Booking not found.")
        if booking.status not in ACTIVE_STATUSES:
            raise AppError(
                f"Booking {booking.reference} is {booking.status}, so there is "
                f"nothing to buy for it."
            )
        supplier = await self.db.get(Supplier, supplier_id)
        if supplier is None:
            raise NotFoundError("Supplier not found.")
        if not (service or "").strip():
            raise AppError(
                "Say what is being bought. 'Hotel' is not something anybody can "
                "confirm — '3 x garden twin, half board' is."
            )
        if len((currency or "").strip()) != 3:
            raise AppError(
                "A supplier commitment needs a currency. Money is an amount and "
                "a currency here as everywhere else."
            )
        row = SupplierBooking(
            booking_id=booking.id,
            supplier_id=supplier.id,
            service=service.strip(),
            status=TO_REQUEST,
            expected_amount=expected_amount,
            currency=currency.upper(),
            check_in=check_in or booking.arrival_date,
            check_out=check_out or booking.departure_date,
            confirm_by=confirm_by,
            notes=notes,
            created_by=actor_id,
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def get(self, supply_id: uuid.UUID) -> SupplierBooking:
        row = await self.db.get(SupplierBooking, supply_id)
        if row is None:
            raise NotFoundError("Supplier booking not found.")
        return row

    async def move(
        self,
        supply_id: uuid.UUID,
        *,
        status: str,
        their_reference: str | None = None,
        reason: str | None = None,
        on: date | None = None,
    ) -> SupplierBooking:
        """Move a supplier booking on, and stamp when it happened."""
        row = await self.get(supply_id)
        try:
            target = normalise_status(status)
            check_transition(
                row.status,
                target,
                their_reference=their_reference or row.their_reference,
                reason=reason,
            )
        except SupplyRefused as exc:
            raise AppError(str(exc)) from exc

        when = on or date.today()
        if target == REQUESTED:
            row.requested_on = when
        elif target == CONFIRMED:
            row.confirmed_on = when
            if their_reference:
                row.their_reference = their_reference.strip()
        elif target == CANCELLED:
            row.cancel_reason = (reason or "").strip()
        row.status = target
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def invoice(
        self,
        supply_id: uuid.UUID,
        *,
        amount: Decimal,
        currency: str | None = None,
    ) -> SupplierBooking:
        """Record what they actually billed.

        Refused in another currency, for §7.1's reason: what they invoiced is a
        fact and the exchange rate is a decision. A supplier who has started
        billing in dollars is a new commitment, not an amendment to this one.
        """
        row = await self.get(supply_id)
        if currency and currency.upper() != row.currency:
            raise AppError(
                f"This was committed in {row.currency} and the invoice is in "
                f"{currency.upper()}. Recording it would mean choosing an "
                f"exchange rate, which is a decision and not arithmetic."
            )
        if amount < 0:
            raise AppError("An invoice cannot be for a negative amount.")
        row.invoiced_amount = amount
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def settle(
        self,
        supply_id: uuid.UUID,
        *,
        amount: Decimal,
        on: date | None = None,
        currency: str | None = None,
    ) -> SupplierBooking:
        """Record what has been paid out.

        A running total rather than a list of payments: this is the payables
        side and it is coarse on purpose. A full ledger belongs in accounting,
        and half of one here would be a second place for the figure to be
        wrong.
        """
        row = await self.get(supply_id)
        if currency and currency.upper() != row.currency:
            raise AppError(
                f"This was committed in {row.currency} and the payment is in "
                f"{currency.upper()}."
            )
        if amount < 0:
            raise AppError("A payment cannot be for a negative amount.")
        row.settled_amount = amount
        row.settled_on = on or date.today()
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def for_booking(self, booking_id: uuid.UUID) -> list[SupplierBooking]:
        return list(
            (
                await self.db.execute(
                    select(SupplierBooking)
                    .where(SupplierBooking.booking_id == booking_id)
                    .order_by(SupplierBooking.check_in, SupplierBooking.service)
                )
            )
            .scalars()
            .all()
        )

    async def position(
        self,
        booking_id: uuid.UUID,
        *,
        today: date | None = None,
        confirm_by_days: int = DEFAULT_CONFIRM_BY_DAYS,
    ) -> tuple[Exposure, list[Concern]]:
        """What is owed on this trip, and everything wrong with its suppliers."""
        booking = await self.db.get(Booking, booking_id)
        if booking is None:
            raise NotFoundError("Booking not found.")
        when = today or date.today()
        rows = await self.for_booking(booking_id)
        committed = [await self._committed(row) for row in rows]
        concerns = [
            *check_supply(
                committed,
                departs_on=booking.arrival_date,
                today=when,
                confirm_by_days=confirm_by_days,
            ),
            *check_invoices(committed),
            *unsettled(
                committed, departed_on=booking.departure_date, today=when
            ),
        ]
        return exposure(committed), concerns

    async def payables(
        self, *, supplier_id: uuid.UUID | None = None
    ) -> list[SupplierBooking]:
        """Everything still owed, oldest trip first.

        The finance list this stage exists to make possible: §7.1 could say
        what every client owed us and nothing could say what we owed anybody.
        """
        stmt = (
            select(SupplierBooking)
            .join(Booking, Booking.id == SupplierBooking.booking_id)
            .where(
                SupplierBooking.status != CANCELLED,
                Booking.status.in_(ACTIVE_STATUSES),
            )
            .order_by(SupplierBooking.check_in)
        )
        if supplier_id is not None:
            stmt = stmt.where(SupplierBooking.supplier_id == supplier_id)
        rows = list((await self.db.execute(stmt)).scalars().all())
        out = []
        for row in rows:
            if (await self._committed(row)).owed > 0:
                out.append(row)
        return out

    async def concerns_for(
        self,
        bookings: Sequence[Booking],
        *,
        today: date,
        confirm_by_days: int = DEFAULT_CONFIRM_BY_DAYS,
    ) -> dict[uuid.UUID, list[Concern]]:
        """Supplier problems across a run of bookings, in two queries.

        For the departure board: §8.1 asked who was driving and never asked
        whether anybody had rung the hotel, so a trip could show green with no
        reservation anywhere.
        """
        ids = [booking.id for booking in bookings]
        out: dict[uuid.UUID, list[Concern]] = {one: [] for one in ids}
        if not ids:
            return out
        rows = list(
            (
                await self.db.execute(
                    select(SupplierBooking).where(
                        SupplierBooking.booking_id.in_(ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        names = await self._supplier_names(rows)
        by_booking: dict[uuid.UUID, list[Committed]] = {one: [] for one in ids}
        for row in rows:
            by_booking[row.booking_id].append(_committed(row, names))
        for booking in bookings:
            out[booking.id] = check_supply(
                by_booking[booking.id],
                departs_on=booking.arrival_date,
                today=today,
                confirm_by_days=confirm_by_days,
            )
        return out

    async def _committed(self, row: SupplierBooking) -> Committed:
        names = await self._supplier_names([row])
        return _committed(row, names)

    async def _supplier_names(
        self, rows: Sequence[SupplierBooking]
    ) -> dict[uuid.UUID, str]:
        ids = {row.supplier_id for row in rows}
        if not ids:
            return {}
        return {
            one.id: one.name
            for one in (
                await self.db.execute(select(Supplier).where(Supplier.id.in_(ids)))
            )
            .scalars()
            .all()
        }


def _committed(row: SupplierBooking, names: dict[uuid.UUID, str]) -> Committed:
    return Committed(
        supplier=names.get(row.supplier_id, "This supplier"),
        status=row.status,
        expected=row.expected_amount,
        invoiced=row.invoiced_amount,
        settled=row.settled_amount,
        currency=row.currency,
        check_in=row.check_in,
        confirm_by=row.confirm_by,
        their_reference=row.their_reference or "",
    )


def _crew(member: CrewMember) -> Crew:
    return Crew(
        name=member.name,
        roles=tuple(member.roles or ()),
        is_active=member.is_active,
        licence_expires_on=member.licence_expires_on,
    )


def _default_role(member: CrewMember) -> str:
    """The role to assume where the caller did not say.

    Their single role where they have one, and a refusal where they have
    several: putting a driver-guide down as one or the other by coin-flip is
    how a trip sheet ends up saying something nobody meant.
    """
    roles = list(member.roles or ())
    if len(roles) == 1:
        return roles[0]
    raise AssignmentRefused(
        f"{member.name} is down as {', '.join(roles)}. Say which of those they "
        f"are doing on this trip — a trip sheet has to name one."
    )


__all__ = [
    "AssignmentService",
    "CrewService",
    "SupplyService",
    "TripLogService",
    "normalise_roles",
]
