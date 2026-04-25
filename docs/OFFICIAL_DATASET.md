# Official Seed Dataset

This project includes `data/official_catalog_sync.json`, a checked-in, source-attributed sync payload used by the wizard's `official_local` mode and by the standalone import script.

The current bundled file is updated through `2026-04-24`.

## What It Contains

- `printers`: manufacturer, model, technology, technical specs, and source metadata.
- `resins`: material rows for both resin and filament entries, plus published technical properties where available.
- `profiles`: baseline printer-material settings with explicit provenance.

Every row includes `metadata.source_urls` and `metadata.retrieved_at` so operators can audit where the data came from. When settings are generated in the wizard, these metadata fields are surfaced back in `settings.provenance.references` so recommendation origin stays visible before printing.

The `2026-04-24` refresh added bundled baseline FDM coverage so a local Step 1 import no longer collapses to MSLA-only catalog options. The added bundled FDM examples include:

- `Anycubic Kobra 3`
- `Prusa MK4S`
- `Anycubic High Speed PLA`
- `Anycubic PETG Filament`
- `Prusament PLA Galaxy Black`
- `Prusament PETG Urban Grey`

## What This Dataset Is Not

- It is not a full community superset of every printer, material, and slicer profile.
- It does not replace the remote update modes (`github`, `web_json`, `web_scrape`). Those are complementary ingestion paths.
- It is intentionally curated and conservative; unsupported vendors/sites should not be treated as automatically covered by the bundled file.

## Source Priority

1. Manufacturer product pages and official support docs.
2. Manufacturer-maintained settings guides and profile pages.
3. Derived midpoint profiles only when an official source publishes ranges instead of single values.
4. Narrow baseline FDM seed rows only when we need guaranteed local target coverage and have a linked manufacturer source.

## Included Official Sources (April 24, 2026)

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

### Bundled FDM Baseline Sources

- Anycubic printer/material sources:
  - `https://store.anycubic.com/products/kobra-3-combo`
  - `https://store.anycubic.com/products/high-speed-pla-filament`
  - `https://store.anycubic.com/products/petg-filament`
- Prusa printer/material sources:
  - `https://www.prusa3d.com/product/original-prusa-mk4s-3d-printer/`
  - `https://www.prusa3d.com/product/prusament-pla-galaxy-black-1kg/`
  - `https://www.prusa3d.com/product/prusament-petg-urban-grey-1kg/`

## Import and Update Paths

### Scripted Import

```bash
python scripts/import_official_catalog.py --replace-existing
```

Without `--replace-existing`, rows are upserted and existing catalog data is preserved.

### Wizard Import

In `/wizard`, choose Step 1 `Catalog source -> Official local dataset` and run `Import / Update Catalog`.

### Docker Persistence Note

When running through Docker Compose, the API persists data into `./local-data`. Rebuilding the image updates the bundled JSON file in the image, but it does not replace the existing persisted `tech_catalog.db`. After changing `data/official_catalog_sync.json`, rerun the Step 1 import/update (or the import script) so the live DB picks up the refreshed rows.

## Trust Surface in Recommendations

After dataset import, `/wizard/settings/recommend` classifies output as:

- `verified_sources`: matching profile exists with source URLs.
- `catalog_unverified`: matching profile exists but source URLs are missing.
- `fallback_defaults`: no matching profile exists; baseline defaults are used.

This is returned in `settings.provenance.data_quality` together with source links and confidence where available.
