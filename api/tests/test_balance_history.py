"""Pure parts of the Balance Load check history."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.balance_history import reason_key
from pyxie_core.balance_workflow import summarize_refusals


def test_same_cause_folds_whatever_the_numbers():
    a = reason_key("balanced", "balanced enough (busy-hours score 82, acts below 70)")
    b = reason_key("balanced", "balanced enough (busy-hours score 79, acts below 70)")
    assert a == b
    assert reason_key("skipped", "paused") != reason_key("skipped", "outside the allowed time window")
    assert reason_key("balanced", "x") != reason_key("no_moves", "x")


def test_refusal_summary_is_small_and_readable():
    blocked = [{"name": f"vm{i}", "source_node": "a", "blocked_node": "b", "blocking_reasons": ["keep apart rule"], "improvement": i} for i in range(9)]
    left = [{"reason": "do not move"}] * 3 + [{"reason": "moved recently"}]
    s = summarize_refusals(blocked, left)
    assert s["blocked_total"] == 9 and len(s["blocked"]) == 5 and s["blocked"][0]["name"] == "vm8"
    assert s["left_alone_total"] == 4 and s["left_alone"][0] == {"reason": "do not move", "count": 3}
