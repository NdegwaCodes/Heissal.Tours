# Brand typefaces

The faces the quotation document is set in, confirmed by the client 2026-08-25.

| File | Family | Role | Weights |
|---|---|---|---|
| `cormorant-garamond-normal.woff2` | Cormorant Garamond | display — cover headline, section headings, property names, price figures | 400–700 |
| `cormorant-garamond-italic.woff2` | Cormorant Garamond | the italic taglines and VAT footnotes | 400–700 |
| `libre-franklin-normal.woff2` | Libre Franklin | body and UI — paragraphs, tables, spec labels, eyebrows | 300–700 |

**Three files, not nine.** Both families are variable fonts, so a single file per
style covers its whole weight range. Downloading the five Cormorant weights the
client listed produced five byte-identical files, which is what gave it away —
112 KB here against 302 KB for the naive set.

**They are committed on purpose.** The document embeds them as data URIs rather
than linking Google Fonts, because the PDF path renders a local `file://` page in
headless Chromium: a font request that does not resolve leaves the proposal set
in a fallback, at different metrics, with no error raised. Same reasoning as the
photographs in 3.6 — the document has to be self-contained. See
`app/modules/documents/fonts.py`.

## Licence

Both families are licensed under the **SIL Open Font License 1.1**, which permits
bundling and redistribution. Full text in `LICENSE-OFL.txt`.

- Cormorant Garamond — Copyright 2015 the Cormorant Project Authors
  (github.com/CatharsisFonts/Cormorant)
- Libre Franklin — Copyright 2015 the Libre Franklin Project Authors
  (github.com/impallari/Libre-Franklin)

## Replacing or adding a face

Drop the `.woff2` in here and add a row to `FACES` in `fonts.py`. Nothing else
changes — the template reaches type through exactly two CSS custom properties and
is forbidden from naming a font anywhere else.

A missing file is skipped rather than fatal, so the document still renders in the
fallback stack. `missing_faces()` is what reports it, because that failure is
invisible by nature: the page looks fine, it is just the wrong typeface.
