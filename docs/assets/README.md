# README visual assets

These files are durable source assets for CoPenguin's GitHub presentation.

| Asset | Purpose | Source of truth |
| --- | --- | --- |
| `../../assets/copenguin-logo.png` | Primary chibi penguin-suit mascot for README and raster use | Original reference-guided image-generation output, created for CoPenguin on 2026-08-18 |
| `../../assets/copenguin-logo.svg` | Scalable flat-color companion mark | Hand-authored vector interpretation of the approved mascot direction |
| `../../assets/readme-banner.svg` | Repository hero banner | CoPenguin product name, vector mascot, and current runtime flow |
| `copenguin-runtime-terminal.svg` | Test-backed runtime contract visual | `tests/test_runtime_*.py` and `docs/RUNTIME_ARCHITECTURE.md` |

The mascot is an original chibi character wearing a slate-charcoal penguin
kigurumi with a visible human face, white belly, wing sleeves, and yellow beak
and feet. It follows a user-supplied visual reference at the concept level while
excluding that image's background, watermark, identity, and distinctive
accessories. The dark grid and terminal motif keep the banner aligned with the
EvolveKB brand family. Do not replace the mascot with an emoji or an external
image URL: the repository must keep reusable PNG and SVG assets even if the
banner changes later.

## Generation brief

The raster mascot was generated with the built-in OpenAI image-generation path.
The user-supplied image was a design reference, not an edit target. The
`logo-brand` brief requested one original full-body anime chibi character in a
penguin kigurumi, with a compact forward-glide pose and transparent background.
The final pose uses a planted leading foot, lifted trailing foot, forward body
lean, offset wing sleeves, and a trailing tail to make the weight transfer legible.
The brief explicitly excluded the original character identity, scenery, watermark,
hair clip, chain, collar, skates, text, and added brand decorations. The output
was downscaled without removing alpha and saved as `assets/copenguin-logo.png`.

## Refresh checklist

1. Keep the PNG mascot, SVG mark, and banner in sync.
2. Do not show planned capabilities as implemented.
3. Preserve transparency in the PNG and readable silhouettes at 48 px.
4. Update the runtime visual only when the referenced tests and architecture do.
5. Validate the SVG files with an XML parser and inspect rendered previews.
6. Verify every README-relative path before publishing.
