from pyxie_core.host_network_workflow import same_setting


def test_comment_with_trailing_newline_matches():
    assert same_setting("phase 2 test\n", "phase 2 test")


def test_empty_and_missing_are_the_same():
    assert same_setting(None, "")
    assert same_setting("", None)


def test_real_difference_fails():
    assert not same_setting("a", "b")
    assert not same_setting(None, "b")
