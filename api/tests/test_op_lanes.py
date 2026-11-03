from pyxie_core import op_lanes
from pyxie_core.op_lanes import (BULK_QUEUE, JOB_TIMEOUT_BY_TYPE, QUICK_QUEUE, QUICK_TYPES, RESUMABLE_TYPES, lane_for, queue_name_for,
                                 quick_lane_active, timeout_for)


class Conn:
    def __init__(self, alive): self.alive = alive
    def exists(self, key):
        assert key == op_lanes.QUICK_ALIVE_KEY
        return 1 if self.alive else 0


class BrokenConn:
    def exists(self, key): raise RuntimeError("redis down")


def test_every_lane_and_resume_type_has_a_timeout():
    assert QUICK_TYPES <= set(JOB_TIMEOUT_BY_TYPE)
    assert RESUMABLE_TYPES <= set(JOB_TIMEOUT_BY_TYPE)


def test_quick_actions_are_short():
    assert all(JOB_TIMEOUT_BY_TYPE[t] <= 3600 for t in QUICK_TYPES)


def test_long_work_is_never_in_the_quick_lane():
    for t in ("vm.live_migrate", "workload.move_storage", "workload.restore", "workload.clone", "node.evacuate", "maintenance.run", "host.update", "host.reboot", "cluster.rebalance"):
        assert lane_for(t) == "bulk", t


def test_unknown_types_default_to_bulk():
    assert lane_for("something.new") == "bulk"


def test_resume_timeouts_unchanged_from_before_the_registry():
    assert {t: timeout_for(t) for t in RESUMABLE_TYPES} == {
        "vm.live_migrate": 16200, "maintenance.run": 21600, "node.enter_maintenance": 10800, "node.exit_maintenance": 1800,
        "cluster.rebalance": 10800, "protection.backup_membership": 120, "protection.backup_retention": 120, "workload.network_vlan_change": 120}


def test_routing_follows_the_heartbeat():
    assert queue_name_for(Conn(True), "workload.shutdown") == QUICK_QUEUE
    assert queue_name_for(Conn(False), "workload.shutdown") == BULK_QUEUE  # lane off or its worker dead: nothing is stranded
    assert queue_name_for(Conn(True), "vm.live_migrate") == BULK_QUEUE


def test_redis_trouble_falls_back_to_bulk():
    assert quick_lane_active(BrokenConn()) is False
    assert queue_name_for(BrokenConn(), "workload.shutdown") == BULK_QUEUE


def test_bulk_queue_keeps_its_old_name():
    assert BULK_QUEUE == "operations"
