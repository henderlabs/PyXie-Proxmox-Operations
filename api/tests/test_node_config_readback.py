from pyxie_core.node_config_workflow import same_value


def test_trailing_newline_from_proxmox_is_not_a_difference():
    assert same_value("phase 2 host test\n", "phase 2 host test")


def test_hosts_crlf_and_trailing_whitespace():
    assert same_value("127.0.0.1 localhost\r\n", "127.0.0.1 localhost\n")


def test_real_difference_and_missing_value_still_fail():
    assert not same_value("other", "phase 2 host test")
    assert not same_value(None, "x")


def test_numbers_compare_as_text():
    assert same_value(30, "30")
