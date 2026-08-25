"""PdfRenderProvider seam — how a rendered HTML document becomes a PDF.

The document service depends only on this protocol. That matters because the
choice of engine is a deployment decision, not a design one, and the realistic
options behave quite differently:

* **A headless Chromium** renders the template exactly as a browser does, which
  is the whole point: the layout was designed and visually checked in one, and
  CSS grid, ``object-fit`` and modern colour handling all work. It costs a
  subprocess and about a second.
* **A pure-Python engine** (WeasyPrint) needs no browser but does not implement
  CSS grid, so it would silently reflow every page of this template. Plugging one
  in here is possible; making the document survive it is a different job.
* **A hosted rendering API** would plug in here too, and is what a container
  without a browser would reach for.

The protocol is deliberately narrow — HTML in, PDF bytes out — so nothing about
the quotation leaks into the renderer, and a renderer cannot be given anything to
do beyond printing.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class PdfRenderError(RuntimeError):
    """The renderer could not produce a PDF.

    Distinct from a missing renderer: this means one was found and failed, which
    is worth surfacing differently to an operator.
    """


@runtime_checkable
class PdfRenderProvider(Protocol):
    """Turns a complete HTML document into PDF bytes."""

    #: Names the engine in errors and logs, so a surprising PDF can be traced to
    #: the thing that produced it rather than to "the renderer".
    name: str

    def is_available(self) -> bool:
        """Whether this provider can run in this environment right now.

        Checked before use so a deployment without a browser fails with an
        explanation an operator can act on, rather than a stack trace.
        """
        ...

    async def render(self, html: str, *, timeout_seconds: int = 60) -> bytes:
        """Render ``html`` and return the PDF bytes.

        The HTML must be self-contained: a renderer is given no network access
        and no credentials, so anything it cannot resolve from the document
        itself is simply missing from the output.
        """
        ...
