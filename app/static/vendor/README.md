# Vendored files

## Chart.js

- File: `chart.umd.js`, committed unmodified (the copyright banner at the top is intact). `.gitattributes` marks `app/static/vendor/**` as `-text` so git never rewrites its bytes.
- Source: `https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.js`, the npm package's own build (already minified).
- Version: Chart.js 4.4.7.
- Note: `dist/chart.umd.min.js` is not a package file. It is only a jsDelivr alias with a jsDelivr comment prepended and no published hash, so the package file `dist/chart.umd.js` was vendored instead.
- Upstream hash as published by `https://data.jsdelivr.com/v1/packages/npm/chart.js@4.4.7` for `dist/chart.umd.js` (base64): `KBLLiCX9xXRp6y97sFXpQpJE5ZmSBRHuR36ChJm2Mss=`
- SHA-256 in hex (checked by `tests/unit/test_static_assets.py`): `2812cb8825fdc57469eb2f7bb055e9429244e599920511ee477e828499b632cb`
- Licences: `LICENSE.chartjs.txt` (Chart.js, MIT, from the `v4.4.7` tag) and `LICENSE.kurkle-color.txt` (`@kurkle/color`, MIT, from the `v0.3.2` tag).
- Bundled dependency: `@kurkle/color` 0.3.2 (Chart.js 4.4.7 depends on `^0.3.0`; the bundle's own banner says v0.3.2).
- The app loads `/static/vendor/chart.umd.js` from its own server. No CDN is used at runtime.

## Figtree font

- File: `figtree-latin.woff2`, committed unmodified: the Latin subset of the Figtree variable font (weights 400 to 800) as Google Fonts serves it.
- Source: `https://fonts.gstatic.com/s/figtree/v9/_Xms-HUzqDCFdgfMm4S9DQ.woff2`, the file named for the `latin` range by `https://fonts.googleapis.com/css2?family=Figtree:wght@400..800`.
- Font SHA-256 in hex (checked by `tests/unit/test_static_assets.py`): `4ba7d3d096695818fe0686be4f1e82c6b05134e18a22260336130335027462dd`
- Licence: `LICENSE.figtree-OFL.txt` (SIL Open Font License 1.1, from `https://github.com/erikdkennedy/figtree`).
- `app.css` loads it with `@font-face` from the app's own server; no font is loaded from another site at runtime.

## Leaflet

- Files: `leaflet.js` and `leaflet.css`, committed unmodified from the npm package's `dist` folder.
- Source: `https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js` and `.../dist/leaflet.css`.
- Version: Leaflet 1.9.4.
- Upstream hashes as published by `https://data.jsdelivr.com/v1/packages/npm/leaflet@1.9.4` (base64): `leaflet.js` `20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=`, `leaflet.css` `p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=`.
- Leaflet SHA-256 in hex (checked by `tests/unit/test_static_assets.py`): `leaflet.js` `db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a`, `leaflet.css` `a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6`
- Licence: `LICENSE.leaflet.txt` (BSD 2-Clause, from the `v1.9.4` tag).
- Not vendored: Leaflet's `images/` folder. The CSS refers to it only for the default marker and the layers control, which the app does not use (its pins are its own `price-pin` elements).
- Map tiles are the one resource loaded from another site: OpenStreetMap's standard tiles, address and attribution in `app/services/maps.py`. The app's own scripts and styles still load nothing off site.
