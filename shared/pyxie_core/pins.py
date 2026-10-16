"""Host pinning (models.PlacementPinRule): which hosts a guest is pinned to, and how hard.

A guest's pin is the combination of every rule that covers it (its own, plus any tag group it belongs to):
  soft_nodes  every host named by any matching rule -- preferred; a guest on one of them is left alone by Balance Load
  hard_nodes  None when no matching rule is hard; else the hosts ALL hard rules allow (their intersection): the only hosts allowed
Pure functions plus one loader, so the logic is testable without a database."""

import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from .models import Node, PlacementPinRule


@dataclass
class PinInfo:
    soft_nodes: set = field(default_factory=set)
    hard_nodes: set | None = None
    rule_ids: list = field(default_factory=list)

    @property
    def pinned(self) -> bool:
        return bool(self.soft_nodes) or self.hard_nodes is not None

    def on_pin(self, node_id) -> bool:
        return str(node_id) in self.soft_nodes


def rule_covers(rule, workload) -> bool:
    if rule.scope_type == "tag_group":
        return bool(rule.tag) and rule.tag in (workload.tags or [])
    return rule.workload_id is not None and str(rule.workload_id) == str(workload.id)


def compute_pin_info(rules, workload) -> PinInfo:
    info = PinInfo()
    hard: set | None = None
    for r in rules:
        if not rule_covers(r, workload):
            continue
        nodes = {str(n) for n in (r.node_ids or [])}
        info.soft_nodes |= nodes
        info.rule_ids.append(str(r.id))
        if r.strict:
            hard = nodes if hard is None else (hard & nodes)
    info.hard_nodes = hard
    return info


def restrict_to_hard_pin(destinations: list[dict], hard_names: set | None, source_name: str) -> tuple[list[dict], str | None]:
    """Keep only the destinations a hard pin allows. Returns (allowed destinations, why nothing is left or None). Destinations are dicts with a "node" name. Pure."""
    if hard_names is None or not destinations:
        return destinations, None  # nothing was eligible to begin with: whatever blocked that (memory, CPU...) is the reason, not the pin
    allowed = [d for d in destinations if d["node"] in hard_names]
    if allowed:
        return allowed, None
    return [], f"hard pin: this guest may only run on {', '.join(sorted(hard_names))}, so it cannot be moved off {source_name}"


def load_pin_rules(db: Session) -> list:
    return db.query(PlacementPinRule).all()


def pin_info_for(db: Session, workload, rules=None) -> PinInfo:
    return compute_pin_info(rules if rules is not None else load_pin_rules(db), workload)


def node_names(db: Session, ids) -> list[str]:
    ids = list(ids)
    if not ids:
        return []
    by_id = {str(n.id): n.name for n in db.query(Node).filter(Node.id.in_(ids)).all()}
    return sorted(by_id.get(str(i), "an unknown host") for i in ids)


def pin_rule_hosts_text(host_ids, node_names: dict) -> str:
    names = sorted(node_names.get(str(h), "an unknown host") for h in host_ids)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1] if names else "no host"


def mirror_workload_page_pin(db: Session, workload) -> None:
    """workloads.preferred_node_id mirrors the workload-page pin rule (single host, soft) so the workload page and reports keep working."""
    rule = (
        db.query(PlacementPinRule)
        .filter(PlacementPinRule.workload_id == workload.id, PlacementPinRule.origin == "workload_page")
        .first()
    )
    ids = (rule.node_ids or []) if rule else []
    workload.preferred_node_id = uuid.UUID(str(ids[0])) if len(ids) == 1 else None
