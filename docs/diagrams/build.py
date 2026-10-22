"""Render every docs/diagrams/*.dot to SVG and a 300 dpi PNG (for the report at 16 cm width).

Needs Graphviz (`dot`) on PATH. Run locally after editing a .dot file and commit
the outputs; CI does not render, because Graphviz versions differ in output.

    python -m docs.diagrams.build
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    if shutil.which("dot") is None:
        print("Graphviz not found. Install it (winget install graphviz / apt install graphviz) and retry.")
        return 1
    for src in sorted(HERE.glob("*.dot")):
        for fmt, extra in (("svg", []), ("png", ["-Gdpi=300"])):
            out = src.with_suffix(f".{fmt}")
            subprocess.run(["dot", f"-T{fmt}", *extra, str(src), "-o", str(out)], check=True)
            print(f"wrote {out.relative_to(HERE.parent.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
