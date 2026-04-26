# Official Seed Dataset

This project now treats `official_local` as a bundled, provenance-backed catalog aggregator.

The local import path merges these checked-in sync payloads:

- `data/official_catalog_sync.json`
- `data/official_catalog_fdm_aggregator.json`
- `data/official_catalog_manufacturer_filaments.json`
- `data/official_catalog_community_crosscheck.json`

The current bundled aggregator is updated through `2026-04-26`.

## What It Contains

- `printers`: manufacturer, model, technology, technical specs, and source metadata.
- `resins`: material rows for both resin and filament entries, plus published technical properties where available.
- `profiles`: baseline printer-material settings with explicit provenance.

Every row imported through the bundled aggregator includes `source_url`, `source_urls`, and retrieval timestamps, and the runtime loader backfills `source_priority_tier`, `confidence`, `sync_confidence_score`, and `license_note` when older seed rows do not already carry them. When settings are generated in the wizard, these metadata fields are surfaced back in `settings.provenance.references` so recommendation origin stays visible before printing.

The `2026-04-26` refresh expands bundled FDM coverage beyond the earlier baseline rows. Added bundled examples now include:

- `Anycubic Kobra 3`
- `Anycubic Kobra S1`
- `Bambu Lab A1`
- `Bambu Lab A1 mini`
- `Creality Ender-3 S1`
- `Elegoo Neptune 4`
- `Prusa MK4S`
- `Anycubic High Speed PLA`
- `Anycubic PETG Filament`
- `Prusament PLA Galaxy Black`
- `Prusament PETG Urban Grey`
- `Generic PLA`
- `Generic PETG`
- `Generic ABS`
- `Generic ASA`
- `Generic TPU 95A`
- `Generic Nylon`
- `Anycubic ABS Filament`
- `Anycubic ASA Filament`
- `Anycubic TPU 95A`
- `Anycubic High Speed PLA Rapid` on `Anycubic Kobra S1`
- `Anycubic PETG Durable` on `Anycubic Kobra S1`
- `Prusament ASA Natural 800g (NFC)`
- `Prusament PA11 Carbon Fiber Black 800g (NFC)`
- refreshed `Prusament PLA Galaxy Black` provenance using the live NFC product page

## What This Dataset Is Not

- It is not a full community superset of every printer, material, and slicer profile.
- It does not replace the remote update modes (`github`, `web_json`, `web_scrape`). Those are complementary ingestion paths.
- It is intentionally curated and conservative; unsupported vendors/sites should not be treated as automatically covered by the bundled file.

## Source Priority

The full tier model, credits, and tracked source registry now live in `docs/CATALOG_AGGREGATOR.md`.

The short version:

1. Official manufacturer sources.
2. Slicer profile repositories.
3. Community databases.
4. Educational/reference sources.

## Included Official Sources (April 26, 2026)

### MSLA / Resin Sources

- Anycubic settings guide:
  - `https://store.anycubic.com/blogs/news/resin-settings-for-anycubic-3d-printers`
- Anycubic product specs:
  - `https://store.anycubic.com/products/photon-mono-m7-pro`
  - `https://store.anycubic.com/products/photon-mono-m7-max`
  - `https://store.anycubic.com/products/photon-mono-m7`
  - `https://store.anycubic.com/products/photon-mono-4-ultra`
- ELEGOO product specs:
  - `https://www.elegoo.com/pages/elegoo-mars-5-series`
  - `https://www.elegoo.com/pages/elegoo-saturn-4-ultra`
  - `https://us.elegoo.com/products/elegoo-8k-standard-resin`
- Phrozen specs and profiles:
  - `https://global.phrozen3d.com/products/sonic-mini-8k-s`
  - `https://helpcenter.phrozen3d.com/hc/en-us/articles/28051214909209--Sonic-Mighty-Revo-Getting-to-Know-Your-Printer`
  - `https://global.phrozen3d.com/products/aqua-8k-resin`
  - `https://info.phrozen3d.com/pages/resin-sonic-mini-8k-s`
  - `https://info.phrozen3d.com/pages/resin-sonic-mighty-16k`
  - `https://info.phrozen3d.com/pages/resin-elegoo-saturn-4-ultra-16k`
- UniFormation specs:
  - `https://uniformation3d.com/products/uniformation-3d-resin-printer-gk3-ultra`
- Formlabs specs:
  - `https://formlabs.com/3d-printers/resin/tech-specs/`

### Bundled FDM Aggregator Sources

- Manufacturer-backed rows still included:
  - `https://store.anycubic.com/products/kobra-3-combo`
  - `https://store.anycubic.com/products/high-speed-pla-filament`
  - `https://store.anycubic.com/products/petg-filament`
  - `https://store.anycubic.com/products/abs-filament`
  - `https://store.anycubic.com/products/asa-filament`
  - `https://store.anycubic.com/products/tpu-filament`
  - `https://www.prusa3d.com/product/original-prusa-mk4s-3d-printer/`
  - `https://www.prusa3d.com/product/prusament-pla-prusa-galaxy-black-1kg-nfc/`
  - `https://www.prusa3d.com/product/prusament-petg-urban-grey-1kg/`
  - `https://www.prusa3d.com/product/prusament-pa11-carbon-fiber-black-800g/`
  - `https://help.prusa3d.com/materials`
- Slicer profile repositories used for expanded FDM coverage:
  - `https://github.com/SimplyPrint/slicer-profiles-db/`
  - `https://github.com/OrcaSlicer/OrcaSlicer/wiki/How-to-create-profiles/442903d4c97adcc01f036023739d6b41ca119be9`
  - `https://github.com/prusa3d/PrusaSlicer-settings`
- Community cross-check sources included as lower-tier duplicate inputs:
  - `https://3dprintmatrix.org/`
  - `https://community.element14.com/`
- Reference material guide used for generic filament envelopes:
  - `https://www.simplify3d.com/resources/materials-guide/properties-table/`

## Import and Update Paths

### Scripted Import

```bash
python scripts/import_official_catalog.py --replace-existing
```

Without `--replace-existing`, rows are upserted and existing catalog data is preserved.

### Wizard Import

In `/wizard`, choose Step 1 `Catalog source -> Official local dataset` and run `Import / Update Catalog`.

### Docker Persistence Note

When running through Docker Compose, the API persists data into `./local-data`. Rebuilding the image updates the bundled JSON files in the image, but it does not replace the existing persisted `tech_catalog.db` wholesale.

On startup, the app now records `official_catalog_seed_state.json` beside the active catalog DB and automatically upserts bundled official seed rows when the bundled dataset `updated_at` value is newer than the last recorded bundled-seed import or no seed state file exists. This keeps the persisted DB current with checked-in official seed updates, including the expanded distinct official ELEGOO resin line, without wiping operator-added rows.

Use Step 1 import/update or `python scripts/import_official_catalog.py --replace-existing` when you want to force a clean replacement import, refresh from a remote source, or update the live DB immediately without restarting the app.

## Trust Surface in Recommendations

After dataset import, `/wizard/settings/recommend` classifies output as:

- `verified_sources`: matching profile exists with source URLs.
- `catalog_unverified`: matching profile exists but source URLs are missing.
- `fallback_defaults`: no matching profile exists; baseline defaults are used.

This is returned in `settings.provenance.data_quality` together with source links and confidence where available.
