# Provenance-Backed Catalog Aggregator

The bundled `official_local` import path is now a provenance-backed catalog aggregator rather than a single checked-in file.

The local seed merges:

- `data/official_catalog_sync.json`
- `data/official_catalog_fdm_aggregator.json`
- `data/official_catalog_manufacturer_filaments.json`
- `data/official_catalog_community_crosscheck.json`

This keeps the conservative MSLA manufacturer-backed seed intact while letting the FDM catalog grow from separate, easier-to-refresh slices for generic FDM coverage, manufacturer-backed filament rows, and lower-tier community cross-check duplicates.

## Row Metadata Contract

Every normalized printer, material, and profile row imported through the bundled aggregator carries these provenance fields in `metadata`:

- `source_url`
- `source_urls`
- `retrieved_at`
- `source_type`
- `source_priority_tier`
- `confidence`
- `sync_confidence_score`
- `license_note`

The runtime loader backfills any missing fields so older checked-in rows still import into the DB with a consistent provenance surface.

## Source Priority Tiers

1. Tier 1 — Official manufacturer sources
   Anycubic, Elegoo, Phrozen, Creality, Bambu, Prusa, Formlabs, Siraya Tech, PowerResins, and other manufacturer-owned product, support, and settings pages.
2. Tier 2 — Slicer profile repositories
   Cura definitions, PrusaSlicer settings bundles, OrcaSlicer profiles, Bambu Studio style machine libraries, and the SimplyPrint slicer profile database.
3. Tier 3 — Community databases
   Maker Trainer, public GitHub profile packs, user-maintained profile repos, and other community-maintained catalogs.
4. Tier 4 — Educational/reference sources
   Simplify3D materials guides, technical comparison tables, manufacturer PDFs used as reference envelopes, and other educational sources.

When two rows compete for the same normalized output, import curation now prefers lower `source_priority_tier`, then stronger row confidence, then newer retrieval dates, then row order.

## Current Source Registry

### Tier 1 — Official Manufacturer Sources

- Included in current bundled seed:
  - `https://wiki.anycubic.com/en/filament-and-resin/resin-settings`
  - `https://store.anycubic.com/products/high-speed-pla-filament`
  - `https://store.anycubic.com/products/petg-filament`
  - `https://store.anycubic.com/products/abs-filament`
  - `https://store.anycubic.com/products/asa-filament`
  - `https://store.anycubic.com/products/tpu-filament`
  - `https://www.prusa3d.com/product/prusament-pla-prusa-galaxy-black-1kg-nfc/`
  - `https://www.prusa3d.com/product/prusament-petg-urban-grey-1kg/`
  - `https://www.prusa3d.com/product/prusament-pa11-carbon-fiber-black-800g/`
  - `https://help.prusa3d.com/materials`
  - Existing manufacturer product/support sources already documented in `docs/OFFICIAL_DATASET.md`
- Notes:
  - These sources remain the highest-trust path for direct printer specs and manufacturer-published settings.
  - The current manufacturer slice now overrides older bundled rows for `Anycubic High Speed PLA`, `Anycubic PETG Filament`, and `Prusament PLA Galaxy Black` with fresher Tier 1 provenance and adds Tier 1 Kobra S1 compatibility rows for official Anycubic PLA and PETG.

### Tier 2 — Slicer Profile Repositories

- Included in current bundled FDM aggregator:
  - `https://github.com/SimplyPrint/slicer-profiles-db/`
  - `https://github.com/OrcaSlicer/OrcaSlicer/wiki/How-to-create-profiles/442903d4c97adcc01f036023739d6b41ca119be9`
  - `https://github.com/prusa3d/PrusaSlicer-settings`
- How they are used:
  - Printer rows are normalized from real machine namespaces in upstream slicer profile trees.
  - FDM profiles are stored as normalized compatibility rows instead of a verbatim mirror of upstream files.

### Tier 3 — Community Databases

- Tracked for future ingest and cross-checking:
  - `https://3dprintmatrix.org/`
  - `https://community.element14.com/technologies/3d-printing/b/blog/posts/maker-trainer-a-crowdsourced-resin-setting-database`
- Current bundled use:
  - Community sources are now included as lower-tier cross-check rows in `data/official_catalog_community_crosscheck.json`.
  - They intentionally duplicate a small number of printer/material/profile keys so regression tests can verify that Tier 1 and Tier 2 rows still win.

### Tier 4 — Educational / Reference Sources

- Included in current bundled FDM aggregator:
  - `https://www.simplify3d.com/resources/materials-guide/properties-table/`
- How it is used:
  - Generic filament rows such as `Generic PLA`, `Generic PETG`, `Generic ABS`, `Generic ASA`, `Generic TPU 95A`, and `Generic Nylon` are stored as reference envelopes with lower confidence than manufacturer or slicer-repository rows.

## Credit And Usage Notes

- Upstream GitHub profile repositories keep their own repository licenses. Our catalog stores normalized compatibility rows, not a verbatim mirror of upstream profile trees.
- Educational/reference sources are used to build generic guidance envelopes, not to claim manufacturer-exact tuning.
- Community sources should always be cross-checked against Tier 1 or Tier 2 data before treating them as production defaults.

## Why This Structure

This project already exposes provenance, source references, and confidence in the wizard. A provenance-backed catalog aggregator matches that product direction better than claiming one perfect canonical database.

The practical rule is simple:

- keep raw source attribution on every row,
- keep confidence explicit,
- keep source tiers documented,
- keep credits and license notes with the data so future refreshes stay auditable.