"""ChromiumPdfRenderer — prints the quotation through a headless browser.

Chromium is used because the template was designed and visually verified in a
browser: CSS grid, ``object-fit: cover`` (which is how the document centre-crops
its photographs) and ``@page`` all behave as intended. A pure-Python engine would
need no subprocess but does not implement grid, and would silently reflow every
page.

Two details are load-bearing:

* **The HTML goes to a temporary file, not to stdin.** Chromium prints a URL, and
  a ``data:`` URL large enough to hold an illustrated proposal exceeds what the
  command line will carry.
* **The subprocess runs off the event loop.** Rendering takes roughly a second;
  doing it inline would stall every other request on the worker for that long.

The renderer is given no network access and no credentials, so the document must
be self-contained — which is why images are inlined before it ever gets here.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.config import settings
from app.integrations.pdf_render import PdfRenderError

# Where a Chromium-family browser usually is, per platform. Tried in order when
# the path is not configured explicitly, so a normal developer machine and a
# normal container both work without configuration.
_CANDIDATE_BINARIES = (
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "msedge",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/chromium",
    "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def find_browser() -> str | None:
    """The browser to print with, or None if this environment has none."""
    configured = settings.PDF_BROWSER_PATH
    if configured:
        # An explicitly configured path is never second-guessed: if it is wrong,
        # the operator needs the error, not a silent fallback to some other
        # browser that renders differently.
        return configured if Path(configured).exists() else None
    for candidate in _CANDIDATE_BINARIES:
        found = shutil.which(candidate) or (
            candidate if Path(candidate).exists() else None
        )
        if found:
            return found
    return None


class ChromiumPdfRenderer:
    name = "chromium"

    def __init__(self, binary: str | None = None) -> None:
        self._binary = binary or find_browser()

    def is_available(self) -> bool:
        return self._binary is not None

    async def render(self, html: str, *, timeout_seconds: int = 60) -> bytes:
        if self._binary is None:
            raise PdfRenderError(
                "No PDF renderer is available. Install a Chromium-family browser "
                "or set PDF_BROWSER_PATH to one. The HTML document renders "
                "without it."
            )
        return await asyncio.to_thread(self._print, html, timeout_seconds)

    def _print(self, html: str, timeout_seconds: int) -> bytes:
        assert self._binary is not None
        with tempfile.TemporaryDirectory(prefix="heissal-pdf-") as workspace:
            root = Path(workspace)
            source = root / "document.html"
            target = root / "document.pdf"
            source.write_text(html, encoding="utf-8")

            result = subprocess.run(  # noqa: S603 - fixed argv, no shell
                self._command(source, target, root),
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
            if not target.exists():
                detail = (result.stderr or result.stdout or b"").decode(
                    "utf-8", "replace"
                )
                raise PdfRenderError(
                    f"{self.name} produced no PDF (exit {result.returncode}): "
                    f"{detail.strip()[:400]}"
                )
            return target.read_bytes()

    def _command(self, source: Path, target: Path, workspace: Path) -> list[str]:
        assert self._binary is not None
        return [
            self._binary,
            "--headless",
            # Software rendering: a server has no GPU, and asking for one is a
            # common source of a hang rather than an error.
            "--disable-gpu",
            # Chromium refuses to write anywhere it considers unsafe unless it
            # owns a profile directory; without this it fails with "access
            # denied" on a perfectly writable path.
            f"--user-data-dir={workspace / 'profile'}",
            # No browser chrome in the output: the template draws its own footer,
            # and a printed URL and timestamp on a client proposal looks like a
            # web page someone printed rather than a document.
            "--no-pdf-header-footer",
            "--disable-extensions",
            "--disable-dev-shm-usage",
            *(["--no-sandbox"] if settings.PDF_BROWSER_NO_SANDBOX else []),
            # Lets layout and any font loading settle before the snapshot; the
            # document has no scripts, so this is a ceiling and not a wait.
            f"--virtual-time-budget={settings.PDF_RENDER_SETTLE_MS}",
            f"--print-to-pdf={target}",
            source.as_uri(),
        ]


def default_renderer() -> ChromiumPdfRenderer:
    return ChromiumPdfRenderer()
