from pyxie_core.backup_policy_workflow import plain_plan_changes, plan_words
from pyxie_core.findings import vmid_from_upid

STD = {"id": "p1", "name": "Standard", "storage": "st-pbs", "schedule": "02:00", "keep_daily": 14, "keep_weekly": 4, "layers": [{"store": "Wasabi"}]}
SHORT = {"id": "p2", "name": "Short-lived", "storage": "st-pbs", "schedule": "03:00", "keep_daily": 3, "layers": []}
CLUSTER = {"scope_type": "cluster", "scope_value": None, "policy_id": "p1", "priority": 0}
GUESTS = [{"id": "g1", "vmid": 101, "tags": []}, {"id": "g2", "vmid": 102, "tags": []}]
label = lambda v: f"guest {v}"  # noqa: E731


def test_reassigning_one_guest_reads_old_plan_to_new_plan():
    old = [CLUSTER, {"scope_type": "vm", "scope_value": "g1", "policy_id": "p2"}]
    new = [CLUSTER]
    out = plain_plan_changes(GUESTS, [STD, SHORT], old, [STD, SHORT], new, label)
    assert len(out) == 1 and out[0].startswith("guest 101: Short-lived (back up to st-pbs at 03:00, keep 3 daily)")
    assert "-> Standard (back up to st-pbs at 02:00, keep 14 daily, 4 weekly; copy to Wasabi)" in out[0]
    assert "Backups already taken stay" in out[0]


def test_no_change_gives_no_lines():
    assert plain_plan_changes(GUESTS, [STD], [CLUSTER], [STD], [CLUSTER], label) == []


def test_choosing_no_backup_says_so():
    out = plain_plan_changes(GUESTS, [STD], [CLUSTER], [STD], [CLUSTER, {"scope_type": "vm", "scope_value": "g2", "policy_id": None}], label)
    assert len(out) == 1 and out[0].startswith("guest 102: Standard") and out[0].split("->")[1].strip().startswith("no backup")


def test_plan_words_without_a_plan():
    assert plan_words([STD], None) == "no backup"


def test_vmid_from_upid():
    assert vmid_from_upid("UPID:st-pve103:0008CD2B:007DA0F1:6AD5CE00:qmreboot:11169:root@pam:") == 11169
    assert vmid_from_upid("UPID:st-pve103:0008CD2B:007DA0F1:6AD5CE00:aptupdate::root@pam:") is None
    assert vmid_from_upid(None) is None
