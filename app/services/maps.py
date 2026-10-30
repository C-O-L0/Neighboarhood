"""Map tiles (USE_CASES R9). The only resource a page loads from another site: OpenStreetMap's standard tiles.

They are used as OpenStreetMap's tile usage policy asks for light use: attribution on every map, no bulk
downloads, the browser's own requests only. The list beside each map always holds the same blocks, so a page
still works when the tiles do not load (offline, blocked, or the policy changes).
"""

from __future__ import annotations

TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
MAX_ZOOM = 19
