"""The contact log's rules, without a database (§5.3).

Every refusal here is a way the log would otherwise lie, and a log that lies is
worse than no log at all: the next person reads "called Tuesday" and does not
call. So they are tested as rules rather than through the API — the same split
as §5.2's pipeline and §7.1's schedule.

The three figures — last contact, chases since a reply, hours to first
response — are tested the same way for the same reason: they are what the
morning list and the pipeline report are built on, and each of them was
unanswerable before this table existed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.modules.comms.rules import (
    CALL,
    EMAIL,
    INBOUND,
    INTERNAL,
    MEETING,
    NOTE,
    OUTBOUND,
    Contact,
    Logged,
    LogRefused,
    check_logged,
    first_response_hours,
    history,
    median_hours,
    normalise_channel,
    normalise_direction,
    silence,
)

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
D = Decimal


def _entry(**over):
    fields = {
        "channel": CALL,
        "direction": OUTBOUND,
        "occurred_at": NOW - timedelta(hours=1),
        "body": "Talked through the coast options.",
    }
    fields.update(over)
    return Logged(**fields)


def _contact(hours_ago: float, **over):
    fields = {
        "channel": EMAIL,
        "direction": OUTBOUND,
        "occurred_at": NOW - timedelta(hours=hours_ago),
    }
    fields.update(over)
    return Contact(**fields)


# --------------------------------------------------------------------------- #
# Normalising
# --------------------------------------------------------------------------- #


def test_a_channel_is_grouped_the_way_a_report_should_group_it():
    """"WhatsApp", "whatsapp " and "WHATSAPP" are one line in a table.

    The same fold as §5.2's lead sources — one convention across the two CRM
    modules — and it stops at separators rather than deleting them, because a
    normaliser that merged words would turn "phone call" into "phonecall".
    """
    assert normalise_channel("WhatsApp") == "whatsapp"
    assert normalise_channel(" WHATSAPP ") == "whatsapp"
    assert normalise_channel("Video call") == normalise_channel("video-call")
    assert normalise_channel("") == NOTE


def test_a_channel_is_not_validated_against_a_list():
    """Channels multiply, and an entry refused is a call nobody records.

    A client moves to Instagram DMs mid-enquiry; the log takes it and a report
    groups it, which is the §5.2 argument about lead sources applied again.
    """
    assert normalise_channel("Instagram DM") == "instagram_dm"


def test_a_direction_is_refused_rather_than_defaulted():
    """It decides whether the client counts as contacted and as having replied.

    Defaulting it would silently move every figure downstream, which is worse
    than an error message.
    """
    assert normalise_direction("Inbound") == INBOUND
    with pytest.raises(LogRefused) as raised:
        normalise_direction("received")
    assert "not a direction" in str(raised.value)
    assert "internal" in str(raised.value)


# --------------------------------------------------------------------------- #
# What may be recorded
# --------------------------------------------------------------------------- #


def test_a_conversation_cannot_be_logged_before_it_happens():
    with pytest.raises(LogRefused) as raised:
        check_logged(_entry(occurred_at=NOW + timedelta(hours=2)), now=NOW)
    assert "has not happened yet" in str(raised.value)
    # And it says where the intention belongs instead.
    assert "next action" in str(raised.value)


def test_an_entry_with_no_words_is_refused():
    """It records that contact happened and nothing about it.

    Which is the worst of the three states: the next person reads "called
    Tuesday" and has to call again anyway.
    """
    with pytest.raises(LogRefused) as raised:
        check_logged(_entry(body="   "), now=NOW)
    assert "Say what was said" in str(raised.value)


def test_an_internal_note_reached_nobody():
    """It is not a conversation with the client, so the question is meaningless.

    Refused rather than ignored, because a note recorded as having reached
    somebody is a note the attention rules count as contact.
    """
    with pytest.raises(LogRefused):
        check_logged(_entry(direction=INTERNAL, reached=True), now=NOW)
    check_logged(_entry(direction=INTERNAL, reached=None), now=NOW)


def test_an_email_does_not_last_nineteen_minutes():
    with pytest.raises(LogRefused) as raised:
        check_logged(_entry(channel=EMAIL, duration_minutes=19), now=NOW)
    assert "call or a meeting" in str(raised.value)
    check_logged(_entry(channel=MEETING, duration_minutes=45), now=NOW)


def test_an_unanswered_call_has_no_length():
    """The attempt is the fact worth keeping; the nineteen minutes is nonsense."""
    with pytest.raises(LogRefused) as raised:
        check_logged(_entry(reached=False, duration_minutes=19), now=NOW)
    assert "not answered has no length" in str(raised.value)
    # The attempt itself is recordable, and should be.
    check_logged(_entry(reached=False), now=NOW)


def test_a_call_of_no_length_is_refused():
    with pytest.raises(LogRefused):
        check_logged(_entry(duration_minutes=0), now=NOW)


# --------------------------------------------------------------------------- #
# What the history says
# --------------------------------------------------------------------------- #


def test_the_history_is_sorted_here_rather_than_trusted():
    """Three callers, one of which promises an order."""
    log = history(
        [_contact(1), _contact(48, direction=INBOUND), _contact(24)]
    )
    assert log.last_contact_at == NOW - timedelta(hours=1)
    assert log.last_inbound_at == NOW - timedelta(hours=48)
    assert log.last_outbound_at == NOW - timedelta(hours=1)


def test_internal_notes_count_as_entries_and_not_as_contact():
    """A lead whose only activity is three notes has not been contacted.

    Saying otherwise is how an unanswered enquiry hides on a dashboard, so the
    two counts are kept apart and both are reported.
    """
    log = history([_contact(1, direction=INTERNAL), _contact(2, direction=INTERNAL)])
    assert log.entries == 2
    assert log.contacts == 0
    assert log.ever_contacted is False
    assert log.by_direction[INTERNAL] == 2


def test_a_voided_entry_stays_visible_and_counts_towards_nothing():
    """The call logged against the wrong client is still what somebody believed.

    So it is not deleted — and it is not counted either, which is the pair of
    properties a soft void has to have to be worth anything.
    """
    log = history([_contact(1, voided=True), _contact(30, direction=INBOUND)])
    assert log.entries == 2
    assert log.contacts == 1
    assert log.last_contact_at == NOW - timedelta(hours=30)
    assert log.chases == 0


def test_chases_are_counted_since_the_client_last_replied():
    """Attempts since they last said something, not attempts ever.

    A client who replies has reset the conversation; counting from the
    beginning of time would leave a long relationship permanently flagged.
    """
    log = history(
        [
            _contact(200),
            _contact(150),
            _contact(100, direction=INBOUND),
            _contact(50),
            _contact(20),
            _contact(2),
        ]
    )
    assert log.chases == 3
    assert log.has_replied is True


def test_unanswered_calls_are_kept_apart_from_a_conversation_that_went_quiet():
    """"Never got them" needs a different next step from "we spoke and they went quiet"."""
    log = history(
        [
            _contact(30, channel=CALL, reached=False),
            _contact(20, channel=CALL, reached=False),
            _contact(10, channel=EMAIL),
        ]
    )
    assert log.unreached_calls == 2
    assert log.chases == 3
    assert log.by_channel == {CALL: 2, EMAIL: 1}


# --------------------------------------------------------------------------- #
# First response — the figure travel sales turns on
# --------------------------------------------------------------------------- #


def test_the_first_response_is_measured_to_the_first_word_back():
    arrived = NOW - timedelta(hours=10)
    assert first_response_hours(
        arrived, [_contact(4), _contact(1)]
    ) == D("6.00")


def test_a_client_chasing_us_is_not_us_answering():
    """Measured to the first outbound entry, not the first entry of any kind."""
    arrived = NOW - timedelta(hours=10)
    entries = [_contact(8, direction=INBOUND), _contact(3)]
    assert first_response_hours(arrived, entries) == D("7.00")


def test_an_unanswered_enquiry_is_none_and_never_zero():
    """A zero would average away the enquiries that were simply dropped.

    They are the ones worth finding, so it is reported as an absence and
    counted separately by the pipeline report.
    """
    arrived = NOW - timedelta(hours=10)
    assert first_response_hours(arrived, []) is None
    assert (
        first_response_hours(arrived, [_contact(2, direction=INBOUND)]) is None
    )
    assert first_response_hours(arrived, [_contact(2, voided=True)]) is None


def test_answering_the_phone_before_typing_the_lead_gives_zero_not_a_negative():
    """Which is what actually happens: the call comes first, the record after."""
    arrived = NOW - timedelta(hours=2)
    assert first_response_hours(arrived, [_contact(5)]) == D("0.00")


def test_the_median_response_ignores_the_fortnight_somebody_was_on_leave():
    """Median for §5.1's reason: the figure should describe a normal Tuesday."""
    assert median_hours([D("1"), D("2"), D("300")]) == D("2")
    assert median_hours([D("1"), D("2"), D("3"), D("300")]) == D("2.50")
    assert median_hours([]) is None


# --------------------------------------------------------------------------- #
# Silence, described and never concluded
# --------------------------------------------------------------------------- #


def test_silence_needs_both_thresholds_and_both_are_the_callers():
    """Two chases in two days is a keen agent; two over three weeks is a loss.

    No default here can tell them apart, so neither is hard-coded.
    """
    log = history([_contact(hours) for hours in (72, 48)])
    assert silence(log, now=NOW, after_chases=2, after_days=1) is not None
    # Enough chases, not enough silence.
    assert silence(log, now=NOW, after_chases=2, after_days=7) is None
    # Enough silence, not enough chases.
    assert silence(log, now=NOW, after_chases=5, after_days=1) is None


def test_silence_refuses_to_conclude():
    log = history([_contact(hours) for hours in (400, 300, 200)])
    quiet = silence(log, now=NOW, after_chases=2, after_days=5)
    assert quiet is not None
    assert quiet.chases == 3
    assert quiet.ever_replied is False
    assert "never replied" in quiet.message
    assert "not something a report can make" in quiet.message


def test_a_conversation_with_no_outbound_word_is_not_silence():
    """Nobody has said anything to go quiet after."""
    log = history([_contact(100, direction=INBOUND)])
    assert silence(log, now=NOW, after_chases=1, after_days=1) is None
