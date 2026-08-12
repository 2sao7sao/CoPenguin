# README visual assets

These files are durable source assets for CoPenguin's GitHub presentation.

| Asset | Purpose | Source of truth |
| --- | --- | --- |
| `../../assets/copenguin-logo.svg` | Standalone mascot; keep it independent from the banner | EvolveKB penguin geometry, adapted with the CoPenguin `C` tag |
| `../../assets/readme-banner.svg` | Repository hero banner | CoPenguin product name and current runtime flow |
| `copenguin-runtime-terminal.svg` | Test-backed runtime contract visual | `tests/test_runtime_*.py` and `docs/RUNTIME_ARCHITECTURE.md` |

The mascot geometry, dark grid, pink accent, mint scarf, and terminal motif are
intentionally aligned with the EvolveKB brand family. Do not replace the mascot
with an emoji or an external image URL: the repository must keep a reusable logo
asset even if the banner changes later.

## Refresh checklist

1. Keep the standalone logo and banner in sync.
2. Do not show planned capabilities as implemented.
3. Update the runtime visual only when the referenced tests and architecture do.
4. Validate the SVG files with an XML parser and inspect rendered previews.
5. Verify every README-relative path before publishing.
