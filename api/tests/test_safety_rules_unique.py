from collections import Counter

from pyxie_core.safety_rules_seed import SAFETY_RULES


def test_rule_ids_are_unique():
    """A duplicate id breaks a FRESH install at startup (the seed inserts every rule), while an existing database silently skips it."""
    dupes = [k for k, n in Counter(r[0] for r in SAFETY_RULES).items() if n > 1]
    assert not dupes, dupes
