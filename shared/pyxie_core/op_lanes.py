"""Which worker lane runs each operation type, and how long its job may run -- one registry (it used to be two tables kept in sync by hand).

Two lanes. `quick` is for short actions (a shutdown, a tag, a NIC edit) so they never wait behind a disk copy; `bulk` is everything else
(migrations, storage moves, restores, evacuations, host updates) and is exactly the single operations worker PyXie has always had.
The quick lane only exists while its worker is alive (a heartbeat key in Redis, refreshed every 15 s); with it off, or if it dies, every
operation goes to the bulk queue as before. Two lanes still share the database locks, so two jobs on the same guest or node cannot collide.
"""

BULK_QUEUE = "operations"  # the name PyXie has always used: jobs queued before an update survive it
QUICK_QUEUE = "operations-quick"
QUICK_ALIVE_KEY = "pyxie:lane:quick:alive"
QUICK_ALIVE_SECONDS = 45

JOB_TIMEOUT_BY_TYPE = {
    # A storage-relocating migration is disk-transfer-bound, not RAM-bound
    # -- migration_workflow.py's own internal monitoring timeout now scales
    # up to MIGRATION_TIMEOUT_CAP_SECONDS (4h) for that case, learned from a
    # real 80GB relocation that legitimately took 942s against the old
    # (too-short) fixed timeout. This RQ job timeout must always stay
    # comfortably above that internal cap, or RQ kills the job outright
    # before the internal timeout can fire and be handled gracefully.
    "vm.live_migrate": 16200,  # 4.5h -- 30min margin over the 4h internal cap
    # node.evacuate/maintenance.run process multiple workloads sequentially
    # within ONE job, each potentially a storage-relocating migration --
    # these are widened proportionally, but a node with many large-disk
    # workloads to move could still exceed this; known scaling limit, not
    # fully solved here.
    "node.evacuate": 10800,  # 3h
    "workload.shutdown": 600,
    "workload.bulk_lifecycle": 14400,  # up to 50 guests one after another, each with its own shutdown timeout
    "workload.start": 300,
    "workload.force_stop": 300,
    "workload.reboot": 600,
    "workload.move_storage": 21600,  # 6h: one Proxmox disk move per disk, each with its own size-based limit
    "workload.snapshot_create": 2400,
    "workload.snapshot_delete": 2400,
    "workload.snapshot_rollback": 2400,
    "workload.tags_change": 120,  # synchronous config write
    "cluster.tag_colors": 120,  # synchronous datacenter option write
    "workload.disk_grow": 1200,
    "workload.nic_change": 300,
    "workload.disk_change": 1200,
    "backup.policy_change": 300,
    "workload.create": 1800,
    "storage.iso_download": 7800,
    "storage.iso_delete": 900,
    "storage.iso_enable": 120,
    "workload.delete": 2400,
    "workload.clone": 8400,
    "workload.cdrom_change": 300,
    "workload.ct_create": 1800,
    "workload.ct_resize": 300,
    "pool.create": 600,
    "pool.delete": 600,
    "pool.members": 600,
    "storage.create": 600,
    "storage.remove": 600,
    "workload.ct_nic_change": 300,
    "storage.ct_template_download": 3900,
    "storage.ct_template_delete": 900,
    "workload.hw_change": 3600,  # optional snapshot + shutdown + config + start
    "workload.resize": 600,  # shutdown + config PUT + start, generous margin over each's own wait_for_task timeout
    "host.reboot": 1800,
    "host.clear_locks": 600,
    "workload.config_change": 300,
    "host.config_change": 300,
    "host.network_stage": 300,
    "host.shutdown": 900,
    "host.network_apply": 600,
    "host.network_discard": 300,
    "pbs.update": 3900,
    "pbs.reboot": 1800,
    "pbs.datastore_change": 300,
    "pbs.storage_add": 300,
    "workload.restore": 41000,  # up to 10 h for the restore task plus configuring and starting
    "pbs.maintenance": 7200,
    "pbs.job_change": 300,
    "host.update": 3900,  # comfortably above host_maintenance_client's 3600s apply() timeout
    "maintenance.run": 21600,  # 6h
    "node.enter_maintenance": 10800,  # 3h -- same evacuation shape as node.evacuate
    "node.exit_maintenance": 1800,
    "cluster.rebalance": 10800,  # 3h -- same evacuation shape as node.evacuate
    "protection.backup_membership": 120,  # synchronous PVE config write, no task to wait on
    "protection.backup_retention": 120,
    "workload.network_vlan_change": 120,  # synchronous PVE config write, no task to wait on -- same shape
}

QUICK_TYPES = frozenset({
    "workload.start", "workload.shutdown", "workload.force_stop", "workload.reboot",
    "workload.tags_change", "cluster.tag_colors",
    "workload.snapshot_create", "workload.snapshot_delete", "workload.snapshot_rollback",
    "workload.disk_grow", "workload.resize", "workload.ct_resize",
    "workload.nic_change", "workload.ct_nic_change", "workload.cdrom_change",
    "workload.config_change", "host.config_change", "host.clear_locks",
    "host.network_stage", "host.network_discard",
    "protection.backup_membership", "protection.backup_retention", "workload.network_vlan_change",
})

# Types an operation worker re-enqueues on start when they were mid-flight (the others are left for a person, as before).
RESUMABLE_TYPES = frozenset({
    "vm.live_migrate", "maintenance.run", "node.enter_maintenance", "node.exit_maintenance", "cluster.rebalance", "workload.bulk_lifecycle",
    "protection.backup_membership", "protection.backup_retention", "workload.network_vlan_change",
})


def lane_for(operation_type_id: str) -> str:
    return "quick" if operation_type_id in QUICK_TYPES else "bulk"


def timeout_for(operation_type_id: str, default: int = 1200) -> int:
    return JOB_TIMEOUT_BY_TYPE.get(operation_type_id, default)


def quick_lane_active(conn) -> bool:
    try:
        return bool(conn.exists(QUICK_ALIVE_KEY))
    except Exception:  # noqa: BLE001 -- Redis trouble must never send an operation nowhere: fall back to the bulk queue
        return False


def queue_name_for(conn, operation_type_id: str) -> str:
    return QUICK_QUEUE if lane_for(operation_type_id) == "quick" and quick_lane_active(conn) else BULK_QUEUE


def enqueue_operation(conn, operation_id, operation_type_id: str, timeout: int | None = None):
    """Put an approved operation on its lane's queue."""
    from rq import Queue
    return Queue(queue_name_for(conn, operation_type_id), connection=conn).enqueue(
        "worker.jobs.execute_operation_job", str(operation_id), job_timeout=timeout or timeout_for(operation_type_id))
