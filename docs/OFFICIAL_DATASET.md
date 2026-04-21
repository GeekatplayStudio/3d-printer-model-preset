# Official Seed Dataset

This project now includes `data/official_catalog_sync.json`, a source-attributed sync payload for local catalog seeding.

## What It Contains

- `printers`: manufacturer, model, technical specs, and source metadata.
- `resins`: manufacturer material entries with technical properties where officially published.
- `profiles`: baseline settings with explicit provenance.

Every entry includes `metadata.source_urls` and `metadata.retrieved_at` so users can audit where settings/specs came from.
When settings are generated in the wizard, these metadata fields are surfaced back in `settings.provenance.references` so the recommendation origin is visible before printing.

## Source Priority

1. Manufacturer product pages and official support docs.
2. Manufacturer-maintained settings guides and profile pages.
3. Derived midpoint profiles only when an official source publishes ranges instead of single values.

## Included Official Sources (April 21, 2026)

- Anycubic settings guide: `https://store.anycubic.com/blogs/news/resin-settings-for-anycubic-3d-printers`
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

## Import Command

```bash
python3 scripts/import_official_catalog.py --replace-existing
```

Without `--replace-existing`, rows are upserted and existing catalog data is preserved.

## Trust Surface in Recommendations

After dataset import, `/wizard/settings/recommend` classifies output as:

- `verified_sources`: matching profile exists with source URLs.
- `catalog_unverified`: matching profile exists but source URLs are missing.
- `fallback_defaults`: no matching profile exists; baseline defaults are used.

This is returned in `settings.provenance.data_quality` together with source links and confidence (when available).
