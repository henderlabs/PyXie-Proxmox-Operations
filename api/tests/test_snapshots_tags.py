from datetime import datetime, timezone

import pytest

from pyxie_core.snapshot_workflow import real_snapshots, suggest_name, valid_name
from pyxie_core.tags_workflow import TagsError, clean_tags, diff_tags, split_tags


def test_snapshot_names():
    assert valid_name("before-update") is None
    assert valid_name("pyxie-20261015-1830") is None
    assert valid_name("a") is not None  # too short
    assert valid_name("1abc") is not None  # must start with a letter
    assert valid_name("has space") is not None
    assert valid_name("x" * 41) is not None
    assert valid_name("current") is not None
    assert valid_name("Current") is not None
    assert valid_name("") is not None
    assert valid_name(None) is not None


def test_real_snapshots_drops_current():
    rows = [{"name": "a"}, {"name": "current", "parent": "a"}]
    assert real_snapshots(rows) == [{"name": "a"}]
    assert real_snapshots(None) == []


def test_suggest_name_is_valid():
    name = suggest_name(datetime(2026, 10, 15, 18, 30, tzinfo=timezone.utc))
    assert name == "pyxie-20261015-1830"
    assert valid_name(name) is None


def test_split_tags_all_separators_and_case_kept():
    assert split_tags("a;b;c") == ["a", "b", "c"]
    assert split_tags("a,b c") == ["a", "b", "c"]
    assert split_tags("linux;AI") == ["linux", "AI"]
    assert split_tags(None) == []
    assert split_tags("") == []


def test_clean_tags_keeps_case_dedupes_and_validates():
    assert clean_tags(["Prod", "prod", " web ", ""]) == ["Prod", "web"]
    assert clean_tags(["AI", "linux"]) == ["AI", "linux"]
    for bad in ("has space", "semi;colon", "-leading", "x" * 41, "bad!"):
        with pytest.raises(TagsError):
            clean_tags([bad])


def test_diff_tags_ignores_case():
    assert diff_tags(["a", "b"], ["b", "c"]) == (["c"], ["a"])
    assert diff_tags(["AI"], ["ai"]) == ([], [])
    assert diff_tags([], []) == ([], [])
