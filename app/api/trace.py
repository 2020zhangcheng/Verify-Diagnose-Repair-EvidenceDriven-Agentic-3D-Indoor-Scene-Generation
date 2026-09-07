"""Build trace from stored IDs, never generated prose."""
def build_trace(rows):
    nodes, edges = {}, []

    def node(ident, kind, version=None):
        nodes[ident] = {"id": ident, "kind": kind, "entity_id": ident, "version": version}
        return ident

    def edge(a, b, relation):
        edges.append({"from": a, "to": b, "relation": relation})

    def belief_id(ref):
        return f"{ref['belief_id']}@{ref['version']}"

    def evidence(origin, refs):
        for ref in refs:
            edge(origin, ref["observation_id"], "supported_by")

    for row in rows:
        p, ident = row.payload, row.entity_id
        if row.kind == "SceneBelief":
            node(ident, "belief", p["ref"]["version"])
            for claim in p["claims"]:
                cid = node(f"{ident}:claim:{claim['claim_id']}", "claim", p["ref"]["version"])
                edge(cid, ident, "belongs_to")
                evidence(cid, claim["evidence"])
        elif row.kind == "Observation":
            node(ident, "observation")
            node(p["camera_view_id"], "camera_view")
            edge(ident, p["camera_view_id"], "captured_from")
        elif row.kind == "CandidateLayout":
            node(ident, "layout")
            bid = belief_id(p["belief_ref"])
            edge(ident, bid, "planned_from")
            for assumption in p["required_assumptions"]:
                aid = node(assumption["assumption_id"], "assumption")
                edge(ident, aid, "requires")
                edge(aid, f"{bid}:claim:{assumption['claim_id']}", "depends_on")
        elif row.kind == "Decision":
            node(ident, "decision")
            edge(ident, belief_id(p["belief_ref"]), "based_on")
            if p["layout_id"]:
                edge(ident, p["layout_id"], "selects")
            for vid in p["verification_ids"]:
                edge(ident, vid, "verified_by")
        elif row.kind == "VerificationResult":
            node(ident, "verification")
            edge(ident, p["layout_id"], "checks")
            edge(ident, belief_id(p["belief_ref"]), "uses")
            evidence(ident, p["evidence"])
        elif row.kind == "ActionResult":
            node(ident, "action")
            edge(ident, p["decision_id"], "decided_by")
            edge(ident, p["layout_id"], "executes")
    missing = {e[k] for e in edges for k in ("from", "to") if e[k] not in nodes}
    if missing:
        raise ValueError("trace_integrity_error: " + ",".join(sorted(missing)))
    return {"nodes": list(nodes.values()), "edges": edges}
