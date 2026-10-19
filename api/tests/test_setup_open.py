from pyxie_core.setup_status import open_steps


def step(key, state, **kw):
    return {"key": key, "state": state, **kw}


def test_required_open_steps_are_counted_apart_from_recommended_ones():
    steps = [step("sites", "done"), step("accounts", "todo"), step("cluster", "waiting"), step("hosts", "next"), step("notifications", "optional"), step("check", "optional")]
    assert open_steps(steps) == {"required": 2, "recommended": 2}


def test_the_backup_server_step_only_counts_once_a_server_has_been_added():
    assert open_steps([step("pbs", "optional", items=[])]) == {"required": 0, "recommended": 0}
    assert open_steps([step("pbs", "todo", items=[{"name": "x"}])]) == {"required": 0, "recommended": 1}


def test_everything_done_means_no_dot():
    assert open_steps([step("sites", "done"), step("accounts", "done"), step("cluster", "done"), step("check", "optional")]) == {"required": 0, "recommended": 0}
