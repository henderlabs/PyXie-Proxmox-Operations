"""Review the affinity rules and host pins against where guests are right now (Balance Load does this on every check and preview).

A rule that is not met names the guests that would have to move to meet it (the `violators`); the planner is told to consider those
guests even when the cluster itself is balanced. Pure functions over loaded rows, plus one loader, so the logic is testable."""

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from .models import Node, PlacementAffinityRule, PlacementPinRule, Workload
from .pins import pin_rule_hosts_text, rule_covers


@dataclass
class RuleReview:
    affinity_rules: int = 0
    pin_rules: int = 0
    issues: list = field(default_factory=list)  # one plain sentence per rule that is not met
    violators: dict = field(default_factory=dict)  # workload id -> [why it should move]

    @property
    def ok(self) -> bool:
        return not self.issues

    def summary(self) -> dict:
        return {"affinity_rules": self.affinity_rules, "pin_rules": self.pin_rules, "issues": self.issues[:12], "guests_to_move": len(self.violators)}

    def sentence(self) -> str:
        n = f"{self.affinity_rules} affinity rule{'s' if self.affinity_rules != 1 else ''} and {self.pin_rules} pin{'s' if self.pin_rules != 1 else ''}"
        if self.ok:
            return f"Reviewed {n}: all met."
        return f"Reviewed {n}: {len(self.issues)} not met ({'; '.join(self.issues[:3])}{'; …' if len(self.issues) > 3 else ''})."


def _affinity_members(rule, workloads):
    if rule.scope_type == "tag_group":
        return [w for w in workloads if rule.tag and rule.tag in (w.tags or [])]
    ids = {str(i) for i in (rule.workload_ids or [])}
    return [w for w in workloads if str(w.id) in ids]


def review_rules_pure(affinity_rules, pin_rules, workloads, node_names: dict) -> RuleReview:
    """workloads: running VMs only. node_names: node id (str) -> name."""
    rv = RuleReview(affinity_rules=len(affinity_rules), pin_rules=len(pin_rules))
    movable = [w for w in workloads if not getattr(w, "do_not_move", False)]
    name = lambda w: w.name or f"VM {w.vmid}"  # noqa: E731
    where = lambda w: node_names.get(str(w.node_id), "an unknown host")  # noqa: E731

    def flag(w, why):
        rv.violators.setdefault(str(w.id), []).append(why)

    for r in affinity_rules:
        members = _affinity_members(r, workloads)
        label = r.description or (f'tag "{r.tag}"' if r.scope_type == "tag_group" else "a guest pair")
        if r.rule_type == "keep_apart":
            by_node: dict = {}
            for m in members:
                by_node.setdefault(str(m.node_id), []).append(m)
            for nid, group in by_node.items():
                if len(group) < 2:
                    continue
                rv.issues.append(f"keep apart ({label}): {', '.join(name(g) for g in group)} share {node_names.get(nid, 'a host')}")
                keep = max(group, key=lambda g: g.memory_bytes or 0)  # the biggest stays; the cheaper ones move
                for g in group:
                    if g is not keep and g in movable:
                        flag(g, f"keep apart ({label})")
        elif r.rule_type == "keep_together" and len(members) > 1:
            by_node = {}
            for m in members:
                by_node.setdefault(str(m.node_id), []).append(m)
            if len(by_node) > 1:
                home = max(by_node, key=lambda k: (len(by_node[k]), sum((g.memory_bytes or 0) for g in by_node[k])))
                rv.issues.append(f"keep together ({label}): {', '.join(f'{name(m)} on {where(m)}' for m in members[:4])}")
                for nid, group in by_node.items():
                    if nid != home:
                        for g in group:
                            if g in movable:
                                flag(g, f"keep together ({label})")
    for p in pin_rules:
        members = [w for w in workloads if rule_covers(p, w)]
        hosts = {str(n) for n in (p.node_ids or [])}
        off = [m for m in members if str(m.node_id) not in hosts]
        if off:
            rv.issues.append(f"{'hard ' if p.strict else ''}pin: {', '.join(f'{name(m)} is on {where(m)}' for m in off[:3])}, pinned to {pin_rule_hosts_text(hosts, node_names)}")
            for m in off:
                if m in movable:
                    flag(m, f"{'hard ' if p.strict else ''}pin to {pin_rule_hosts_text(hosts, node_names)}")
    return rv


def review_rules(db: Session, cluster_id=None) -> RuleReview:
    q = db.query(Workload).filter(Workload.is_missing.is_(False), Workload.status == "running", Workload.type == "vm")
    if cluster_id is not None:
        q = q.filter(Workload.cluster_id == cluster_id)
    workloads = q.all()
    nodes = {str(n.id): n.name for n in db.query(Node).all()}
    return review_rules_pure(db.query(PlacementAffinityRule).all(), db.query(PlacementPinRule).all(), workloads, nodes)
