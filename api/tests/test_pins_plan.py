from pyxie_core.pins import restrict_to_hard_pin

D = [{"node": "n2", "fits": True}, {"node": "n3", "fits": True}]


def test_no_hard_pin_leaves_destinations_alone():
    assert restrict_to_hard_pin(D, None, "n1") == (D, None)


def test_hard_pin_keeps_only_the_pinned_hosts():
    allowed, why = restrict_to_hard_pin(D, {"n3", "n1"}, "n1")
    assert allowed == [{"node": "n3", "fits": True}] and why is None


def test_hard_pin_to_the_host_being_drained_leaves_nothing_and_says_why():
    allowed, why = restrict_to_hard_pin(D, {"n1"}, "n1")
    assert allowed == [] and "may only run on n1" in why and "cannot be moved off n1" in why


def test_nothing_eligible_anyway_is_not_blamed_on_the_pin():
    assert restrict_to_hard_pin([], {"n1"}, "n1") == ([], None)
