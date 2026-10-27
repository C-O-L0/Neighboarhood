# Vendored: Chart.js

- File: `chart.umd.js`, committed unmodified (the copyright banner at the top is intact). `.gitattributes` marks `app/static/vendor/**` as `-text` so git never rewrites its bytes.
- Source: `https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.js`, the npm package's own build (already minified).
- Version: Chart.js 4.4.7.
- Note: `dist/chart.umd.min.js` is not a package file. It is only a jsDelivr alias with a jsDelivr comment prepended and no published hash, so the package file `dist/chart.umd.js` was vendored instead.
- Upstream hash as published by `https://data.jsdelivr.com/v1/packages/npm/chart.js@4.4.7` for `dist/chart.umd.js` (base64): `KBLLiCX9xXRp6y97sFXpQpJE5ZmSBRHuR36ChJm2Mss=`
- SHA-256 in hex (checked by `tests/unit/test_static_assets.py`): `2812cb8825fdc57469eb2f7bb055e9429244e599920511ee477e828499b632cb`
- Licences: `LICENSE.chartjs.txt` (Chart.js, MIT, from the `v4.4.7` tag) and `LICENSE.kurkle-color.txt` (`@kurkle/color`, MIT, from the `v0.3.2` tag).
- Bundled dependency: `@kurkle/color` 0.3.2 (Chart.js 4.4.7 depends on `^0.3.0`; the bundle's own banner says v0.3.2).
- The app loads `/static/vendor/chart.umd.js` from its own server. No CDN is used at runtime.
