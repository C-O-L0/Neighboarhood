import json

from etl import download


def test_manifest_lists_three_files_with_checksums():
    m = download.load_manifest()
    assert [f["rows"] for f in m["files"]] == [241357, 13357, 129]
    assert all(len(f["sha256"]) == 64 for f in m["files"])


def test_verify_detects_missing_and_changed_files(tmp_path):
    good = tmp_path / "a.csv"
    good.write_text("x\n1\n")
    manifest = {
        "files": [
            {"name": "a.csv", "sha256": download.sha256(good), "rows": 1},
            {"name": "b.csv", "sha256": "0" * 64, "rows": 1},
        ]
    }
    assert download.verify(manifest, tmp_path) is False
    (tmp_path / "b.csv").write_text("changed")
    assert download.verify(manifest, tmp_path) is False
    manifest["files"].pop()
    assert download.verify(manifest, tmp_path) is True
    json.dumps(manifest)  # manifest stays serialisable
