"""Shared settings for tests that request pages from the running web app."""

# Demo mode shows the database switch and the per-page timings (docs/conventions.md, 6). Page tests that check
# those send this cookie, as a presenter's browser would after turning demo mode on.
DEMO = {"demo": "1"}
