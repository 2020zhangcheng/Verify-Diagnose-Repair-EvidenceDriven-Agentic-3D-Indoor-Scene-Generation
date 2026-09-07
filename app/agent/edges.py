def need_more_evidence(state):
    if state["critical_unknown_ids"]:
        if state["budget"]["remaining_views"] < 1 or state["budget"]["remaining_travel_m"] < 1:
            return "blocked"
        return "generate_candidate_views"
    return "verify_layout"


def verified(state):
    return "execute" if state["decision"]["kind"] == "execute" else "blocked"
