"""etl.pipeline --fraction (task 4.1b) against the running stack, on the committed sample.

The pipeline runs here as a subprocess of the test container, which holds every credential it needs (the CI test step
passes PG_OWNER_PASSWORD, MONGO_ETL_PASSWORD and the two DEMO_* passwords; the web service supplies the app ones). It
always loads from a temporary folder inside the container, so data/interim, which the image does not even contain, is
never written. The test reloads the full sample in a `finally`, so the databases end as they started.

It replaces the loaded data twice, so it runs alone (the whole suite is run one copy at a time).

It runs on the sample only. When the loaded sales are 0.1 or more of the import report's 241,357 (the sample is about
0.026; the benchmark fractions are 0.25 and above), it skips before touching any data, because the `finally` below
would otherwise replace the full data with the sample.
"""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

from bench import run as bench_run

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
NEEDED = ["PG_OWNER_PASSWORD", "MONGO_ETL_PASSWORD", "DEMO_BUYER_PASSWORD", "DEMO_ADMIN_PASSWORD"]


def tree_hashes(folder: Path):
    """sha256 of every file under a folder, or None when the folder does not exist."""
    if not folder.exists():
        return None
    files = sorted(p for p in folder.rglob("*") if p.is_file())
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def pipeline(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "etl.pipeline", *args], cwd=ROOT, env=os.environ.copy(), capture_output=True, text=True
    )


def sale_ids(settings, mongo_app) -> tuple[set[int], set[int]]:
    with psycopg.connect(settings.pg_dsn("app_rw")) as conn:
        pg = {r[0] for r in conn.execute("SELECT txn_id FROM resale_transaction WHERE source = 'snapshot'")}
    mongo = {d["_id"] for d in mongo_app["resale_transactions"].find({"source": "snapshot"}, {"_id": 1})}
    return pg, mongo


def test_fraction_loads_the_txn_id_subset_in_both_databases_and_bench_run_checks_it(settings, mongo_app, tmp_path):
    missing = [n for n in NEEDED if not os.environ.get(n)]
    if missing or not settings.pg_app_ro_password or not settings.mongo_etl_password:
        pytest.skip(f"{', '.join(missing) or 'a password'} not set; pass them to the test container")
    interim_before = tree_hashes(ROOT / "data" / "interim")
    report_path = ROOT / "docs" / "import_report.md"
    report_before = hashlib.sha256(report_path.read_bytes()).hexdigest()
    full_pg, full_mongo = sale_ids(settings, mongo_app)
    if len(full_pg) / bench_run.import_report_sales() >= 0.1:
        pytest.skip("runs on the sample only; the full data (or a fraction of it) is loaded")
    assert full_pg == full_mongo and len(full_pg) > 100, "load the sample first (etl.pipeline --sample)"
    wanted = {i for i in full_pg if i % 4 == 0}
    assert 0 < len(wanted) < len(full_pg)

    try:
        run = pipeline("--sample", "--fraction", "0.25")
        assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]
        assert f"{len(wanted):,} sales kept" in run.stdout
        pg_ids, mongo_ids = sale_ids(settings, mongo_app)
        assert pg_ids == mongo_ids == wanted

        # bench.run: the fraction is the loaded sales over the import report's 241,357, so on the sample the value to
        # expect is computed the same way; the default (1) and the full sample's own fraction both refuse.
        expect = len(wanted) / bench_run.import_report_sales()
        results = tmp_path / "results"
        common = ["--cases", "search_type_month", "--runs", "1", "--warmup", "0", "--results-root", str(results)]
        assert bench_run.main([*common, "--label", "ok", "--expect-fraction", f"{expect:.6f}"]) == 0
        assert bench_run.main([*common, "--label", "default"]) == 2
        full_fraction = len(full_pg) / bench_run.import_report_sales()
        assert bench_run.main([*common, "--label", "full", "--expect-fraction", f"{full_fraction:.6f}"]) == 2
        assert not (results / "default").exists() and not (results / "full").exists()
    finally:
        reload = pipeline("--sample")
        assert reload.returncode == 0, reload.stdout[-2000:] + reload.stderr[-2000:]

    assert sale_ids(settings, mongo_app) == (full_pg, full_mongo)
    assert tree_hashes(ROOT / "data" / "interim") == interim_before
    assert hashlib.sha256(report_path.read_bytes()).hexdigest() == report_before
