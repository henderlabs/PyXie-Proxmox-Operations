import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pyxie_core.vlan_view import build_vlan_view
from pyxie_core.models import Cluster, Node, Workload
from pyxie_core.network_view import layout_groups
from pyxie_core.network_workflow import (
    NetworkWorkflowError,
    dry_run_vlan_change,
    list_network_topology,
    list_workload_nics,
    read_cluster_net_facts,
    read_sdn,
)

from ..auth_deps import get_current_user, require_admin
from ..deps import get_db

router = APIRouter(prefix="/api/network", tags=["network"], dependencies=[Depends(get_current_user)])


@router.get("/topology")
def get_topology(db: Session = Depends(get_db)):
    """Read-only host network interfaces (bridges/bonds/VLANs), every
    known cluster combined -- same "all clusters in one list" shape as
    Storage/Workloads. No write path exists for this; see
    network_workflow.py's module docstring for why."""
    out = []
    for cluster in db.query(Cluster).filter(Cluster.is_missing.is_(False)).order_by(Cluster.name).all():
        for node_topo in list_network_topology(db, cluster):
            node_topo["cluster_id"] = str(cluster.id)
            node_topo["cluster_name"] = cluster.name
            out.append(node_topo)
    return out


@router.get("/nics")
def get_nics(db: Session = Depends(get_db)):
    """Read-only: every workload's NIC(s), every known cluster combined."""
    out = []
    for cluster in db.query(Cluster).filter(Cluster.is_missing.is_(False)).order_by(Cluster.name).all():
        for wl_nics in list_workload_nics(db, cluster):
            wl_nics["cluster_id"] = str(cluster.id)
            wl_nics["cluster_name"] = cluster.name
            out.append(wl_nics)
    return out


class VlanChangeDryRunRequest(BaseModel):
    workload_id: uuid.UUID
    net_id: str
    new_tag: int | None = None


@router.post("/vlan-change/dry-run", dependencies=[Depends(require_admin)])
def create_vlan_change_dry_run(
    payload: VlanChangeDryRunRequest, user=Depends(get_current_user), db: Session = Depends(get_db),
):
    workload = db.query(Workload).filter(Workload.id == payload.workload_id).one_or_none()
    if workload is None:
        raise HTTPException(404, "workload not found")
    if payload.new_tag is not None and not (1 <= payload.new_tag <= 4094):
        raise HTTPException(400, "VLAN tag must be between 1 and 4094")
    try:
        op = dry_run_vlan_change(db, workload, net_id=payload.net_id, new_tag=payload.new_tag, actor=user.email)
    except NetworkWorkflowError as exc:
        raise HTTPException(400, str(exc))
    return {"id": str(op.id), "status": op.status, "dry_run_result": op.dry_run_result}


def _sdn_by_cluster(db: Session) -> dict:
    return {str(c.id): read_sdn(db, c) for c in db.query(Cluster).filter(Cluster.is_missing.is_(False)).all()}


@router.get("/vlans")
def get_vlans(db: Session = Depends(get_db)):
    """Read-only: networks (SDN virtual networks and bridge + VLAN pairs) with their guests, VLAN and the hosts that cannot carry each."""
    return build_vlan_view(get_topology(db), get_nics(db), _sdn_by_cluster(db))


@router.get("/sdn")
def get_sdn(db: Session = Depends(get_db)):
    """Read-only: whether PyXie could read each cluster's SDN zones and networks, and how many."""
    out = []
    for c in db.query(Cluster).filter(Cluster.is_missing.is_(False)).order_by(Cluster.name).all():
        s = read_sdn(db, c)
        out.append({"cluster_id": str(c.id), "cluster_name": c.name, "readable": s["readable"], "error": s["error"],
                    "zones": [z["zone"] for z in s["zones"]], "networks": len(s["vnets"])})
    return out


@router.get("/layout")
def get_layout(db: Session = Depends(get_db)):
    """Read-only: each cluster's hosts grouped by identical network layout, with what each link is PROVEN to carry (never guessed from a name)."""
    out = []
    for c in db.query(Cluster).filter(Cluster.is_missing.is_(False)).order_by(Cluster.name).all():
        topo = list_network_topology(db, c)
        for t in topo:
            t["cluster_id"] = str(c.id)
        facts = read_cluster_net_facts(db, c)
        facts["mgmt"] = {n.name: n.management_ip for n in db.query(Node).filter(Node.cluster_id == c.id, Node.is_missing.is_(False)).all() if n.management_ip}
        nics = list_workload_nics(db, c)
        out.append({"cluster_id": str(c.id), "cluster_name": c.name,
                    "groups": layout_groups(topo, facts, nics, read_sdn(db, c)),
                    "facts": {"migration_network": facts.get("migration_cidr"), "corosync_links_known": bool(facts.get("corosync"))}})
    return out
