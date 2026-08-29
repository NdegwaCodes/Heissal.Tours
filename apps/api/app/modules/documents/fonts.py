"""The brand typefaces, embedded in the document rather than linked.

Cormorant Garamond for display and Libre Franklin for body, confirmed by the
client 2026-08-25 — the faces the reference proposal was set in. Until then the
template ran on labelled placeholders (design doc §3.11, open question 1).

**Why the files live in this repo instead of a `<link>` to Google Fonts.**
A linked stylesheet is a live network dependency at render time, and the print
path is exactly where that fails worst: the PDF renderer opens a local
``file://`` page in headless Chromium, and if the font request does not resolve
the page still renders — in a fallback face, with no error, at different metrics.
A proposal that reflows because the render host had no route to
``fonts.gstatic.com`` is precisely the class of difference nobody thinks to look
for. Embedding them is the same decision already taken for photographs in 3.6,
for the same reason: the document has to be self-contained.

**Both families are variable fonts**, so one file per style covers the whole
declared weight range — 400–700 for Cormorant Garamond, 300–700 for Libre
Franklin. Downloading the five Cormorant weights the client listed produced five
byte-identical files, which is what gave this away. Three files, 112 KB, instead
of nine files and 302 KB.

Licensing: both are SIL Open Font License 1.1, which permits redistribution. See
``LICENSE-OFL.txt`` beside the files.
"""

from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

FONT_DIR = Path(__file__).parent / "fonts"

# family, style, the weight range the variable axis is exposed over, filename.
# The ranges are the ones the client specified; a browser clamps a request
# outside the file's real axis, so declaring the brand range is safe and keeps
# this table readable as a statement of the brand rather than of the binary.
FACES: tuple[tuple[str, str, str, str], ...] = (
    ("Cormorant Garamond", "normal", "400 700", "cormorant-garamond-normal.woff2"),
    ("Cormorant Garamond", "italic", "400 700", "cormorant-garamond-italic.woff2"),
    ("Libre Franklin", "normal", "300 700", "libre-franklin-normal.woff2"),
)

# What the template falls back to per family if a file is missing. Deliberately
# a real stack rather than the generic keyword: if the brand face cannot load,
# the document should still set in something of the same character.
FALLBACKS = {
    "Cormorant Garamond": "'EB Garamond', Garamond, 'Times New Roman', serif",
    "Libre Franklin": "'Helvetica Neue', Helvetica, Arial, sans-serif",
}


def font_stack(family: str) -> str:
    """The CSS value for a family, brand face first then its fallback."""
    return f"'{family}', {FALLBACKS[family]}"


@lru_cache(maxsize=1)
def face_css() -> str:
    """``@font-face`` rules with the fonts embedded as data URIs.

    Cached for the process: the files never change while it runs, and base64
    encoding 112 KB on every render of every document would be pure waste.

    A missing file is skipped rather than raised. The document is worth more set
    in a fallback face than not produced at all, and
    :func:`missing_faces` is what surfaces the problem to an operator.
    """
    rules: list[str] = []
    for family, style, weight, filename in FACES:
        path = FONT_DIR / filename
        try:
            payload = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError:
            continue
        rules.append(
            "@font-face{"
            f"font-family:'{family}';"
            f"font-style:{style};"
            f"font-weight:{weight};"
            "font-display:block;"
            f"src:url(data:font/woff2;base64,{payload}) format('woff2');"
            "}"
        )
    return "".join(rules)


def missing_faces() -> list[str]:
    """Filenames the deployment is missing, for the health check to report.

    Worth checking rather than trusting: the failure is silent by nature — the
    document renders, it just renders in the wrong typeface, and nobody notices
    until a client has it.
    """
    return [
        filename
        for _family, _style, _weight, filename in FACES
        if not (FONT_DIR / filename).is_file()
    ]
