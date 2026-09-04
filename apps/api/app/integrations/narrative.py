"""NarrativeProvider seam — how a property's description gets written (§4.4).

The roadmap's last Stage 4 item is an "AI-generated narrative". This is the
half of it that does not depend on which model: a **brief** of the facts, a
**draft** that carries its own provenance, and a protocol a provider plugs
into.

Three rules, and the middle one is the whole design.

**A brief carries facts, never adjectives.** What goes to a provider is the
property's name, category, destination, board bases and the room types on
file — things the catalogue knows. "Stunning" and "unspoilt" are what a
provider is for; feeding them in and getting them back is how a document ends
up describing a beach nobody has seen.

**Nothing generated reaches a client until a person approves it.** Exactly the
rule :mod:`app.integrations.rate_extraction` applies to money, for the same
reason: a wrong figure on a proposal is a commercial incident, and a
confidently wrong sentence about a hotel is a smaller version of the same
thing. So a provider produces a *draft*, an agent edits it, and approval is a
separate act with a permission of its own.

**A draft says where it came from.** Provider, model, and the brief it was
given, kept beside the text — the same instinct as the source strings on the
costing worksheet (§3.12). A sentence in front of a client that nobody can
trace is a sentence nobody can defend.

No provider ships. There is no model configured for this project and inventing
an HTTP client for a vendor nobody has chosen would be worse than a seam: the
default raises, the endpoint says so plainly, and an agent writing the copy by
hand goes through the identical review path. Which is also the honest order to
build it in — the review gate is the part that protects the client.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

#: What a narrative is *for*. A property's description on the option page and a
#: destination's on the cover are different jobs — one sells a hotel, the other
#: sets a scene — so the brief says which, and a provider can answer them
#: differently.
ACCOMMODATION = "accommodation"
DESTINATION = "destination"
SUBJECTS = (ACCOMMODATION, DESTINATION)


class NarrativeUnavailable(RuntimeError):
    """No provider is configured, or the configured one could not answer."""


@dataclass(frozen=True)
class Brief:
    """The facts a narrative may be written from.

    Deliberately small and deliberately factual. Everything here is something
    the catalogue holds and an operator could check; nothing here is an
    opinion. A provider that needs more than this to write eighty words about
    a hotel is a provider that is about to invent something.
    """

    subject: str
    name: str
    #: hotel / lodge / camp / resort, or a destination's type.
    category: str = ""
    #: The destination a property sits in, for a property brief.
    place: str = ""
    #: Board bases the property actually has rates on — "full board" is a fact
    #: about our data, and a narrative promising half board we cannot sell is
    #: a narrative that costs a booking.
    meal_plans: tuple[str, ...] = ()
    room_types: tuple[str, ...] = ()
    #: What the agent wants said, in their words. The one free-text input, and
    #: the reason it exists is that an agent who has visited the property knows
    #: the thing worth saying.
    steer: str = ""
    #: Roughly how long, in words. A proposal's option page has room for a
    #: paragraph and not for an essay (§3.11).
    words: int = 80

    def __post_init__(self) -> None:
        if self.subject not in SUBJECTS:
            raise ValueError(f"unknown narrative subject: {self.subject!r}")
        if not self.name:
            raise ValueError("a narrative needs something to be about")
        if self.words <= 0:
            raise ValueError("words must be positive")

    def as_dict(self) -> dict:
        """The brief as JSON, to store beside the draft it produced."""
        return {
            "subject": self.subject,
            "name": self.name,
            "category": self.category,
            "place": self.place,
            "meal_plans": list(self.meal_plans),
            "room_types": list(self.room_types),
            "steer": self.steer,
            "words": self.words,
        }


@dataclass(frozen=True)
class Draft:
    """One piece of proposed copy, and where it came from.

    ``provider`` and ``model`` are stored rather than inferred so a sentence on
    a two-year-old proposal can still be attributed. ``hand`` is the provider
    name an agent's own writing gets, because copy typed by a person and copy
    written by a model must be told apart on the record — not because one is
    better, but because only one of them can be asked what it meant.
    """

    text: str
    provider: str
    model: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("a draft with no text is not a draft")
        if not self.provider:
            raise ValueError("a draft must say what produced it")


#: The provider name for copy a person wrote. Not "manual": what matters on the
#: record is that a human is answerable for the sentence.
HAND = "hand"


class NarrativeProvider(Protocol):
    """Writes proposal copy from a brief.

    Implementations MUST:

    * return a :class:`Draft` naming themselves, so the text stays traceable;
    * raise :class:`NarrativeUnavailable` rather than returning filler when
      they cannot answer — an empty paragraph on a client's proposal is
      visible, and a plausible invented one is not;
    * treat the brief as the only input. A provider that reaches for the
      property's own marketing site is a provider that will quote a competitor.
    """

    async def write(self, brief: Brief) -> Draft: ...


class UnavailableProvider:
    """The default: no model is configured, and it says so.

    Ships as the real default on purpose. The alternative — a template that
    stitches the brief's facts into a sentence — would produce "Coral Sands
    Resort is a resort in Diani offering full board", which is not a narrative;
    it is the facts panel above it, retyped, and it would go out on client
    documents looking like something nobody wrote. Better to have nothing there
    than filler, and better to say why.
    """

    name = "unavailable"

    async def write(self, brief: Brief) -> Draft:
        raise NarrativeUnavailable(
            "No narrative provider is configured, so copy cannot be generated. "
            "Write the description and it goes through the same review before "
            "it reaches a client."
        )
