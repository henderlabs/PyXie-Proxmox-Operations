"""The nightly metrics prune must honour the metrics.retention_days policy.

It used to call prune_old_metrics(db) with no argument and so always kept
400 days, whatever the policy said (found on CRE: policy 200 days, prune
deleting 0 rows every night).
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from fakes import make_query_router
from pyxie_core import metrics
from pyxie_core.models import MetricPoint, Policy


def _db(policy_value):
    rows = {Policy: [SimpleNamespace(value=policy_value)]} if policy_value is not None else {}
    db = MagicMock()
    db.query.side_effect = make_query_router(rows)
    return db


@pytest.mark.parametrize(
    "value, expected",
    [
        ({"days": 200}, 200),
        ({"days": "90"}, 90),
        (None, metrics.DEFAULT_RETENTION_DAYS),  # no policy row
        ({}, metrics.DEFAULT_RETENTION_DAYS),
        ({"days": "soon"}, metrics.DEFAULT_RETENTION_DAYS),
        ("200", metrics.DEFAULT_RETENTION_DAYS),  # not a dict
        ({"days": 0}, metrics.MIN_RETENTION_DAYS),
        ({"days": 1}, metrics.MIN_RETENTION_DAYS),
        ({"days": 100000}, metrics.MAX_RETENTION_DAYS),
    ],
)
def test_retention_days_for(value, expected):
    assert metrics.retention_days_for(_db(value)) == expected


def _prune_cutoff(db, **kwargs):
    """Runs prune_old_metrics and returns the cutoff it filtered MetricPoint on."""
    seen = {}
    real_query = db.query.side_effect

    def query(model):
        q = real_query(model)
        if model is MetricPoint:
            def capture(cond):
                seen["cutoff"] = cond.right.value
                return q
            q.filter.side_effect = capture
            q.delete.return_value = 3
        return q

    db.query.side_effect = query
    with patch("pyxie_core.mem_pressure.prune_memory_pressure", return_value=2):
        deleted = metrics.prune_old_metrics(db, **kwargs)
    assert deleted == 5
    return seen["cutoff"]


def test_prune_uses_policy_when_no_argument():
    cutoff = _prune_cutoff(_db({"days": 200}))
    expected = datetime.now(timezone.utc) - timedelta(days=200)
    assert abs((cutoff - expected).total_seconds()) < 60


def test_explicit_argument_still_wins():
    cutoff = _prune_cutoff(_db({"days": 200}), retention_days=365)
    expected = datetime.now(timezone.utc) - timedelta(days=365)
    assert abs((cutoff - expected).total_seconds()) < 60
