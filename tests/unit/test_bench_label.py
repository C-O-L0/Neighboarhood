"""Results labels: one folder name, or folder/subfolder (task 4.2 writes 2026-11-full/f100)."""

import pytest

from bench.run import LABEL_PATTERN


@pytest.mark.parametrize("label", ["sample", "2026-11-full/f100", "a.b_c/d-e", "run-20261101T000000Z"])
def test_accepted(label):
    assert LABEL_PATTERN.fullmatch(label)


REFUSED = ["", "/abs", "a/", "a//b", "a/b/c", "../x", "a/../b", ".hidden", "a/.b", "a b", "a" + chr(92) + "b"]


@pytest.mark.parametrize("label", REFUSED)
def test_refused(label):
    assert not LABEL_PATTERN.fullmatch(label)
