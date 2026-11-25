from __future__ import annotations

from typing import Any


def validate_dispatch_plan(
    plan: list[dict[str, Any]], valid_agent_ids: set[str]
) -> tuple[bool, str | None]:
    if not plan:
        return False, "Dispatch plan must contain at least one task"
    ids: set[str] = set()
    for item in plan:
        tid = str(item.get("id") or "").strip()
        aid = str(item.get("agentId") or "").strip()
        task = str(item.get("task") or "").strip()
        if not tid or not aid or not task:
            return False, "Each task requires id, agentId, and task"
        if tid in ids:
            return False, f"Duplicate task id: {tid}"
        if valid_agent_ids and aid not in valid_agent_ids:
            return False, f"Unknown agentId in plan: {aid}"
        ids.add(tid)

    for item in plan:
        for dep in item.get("dependsOn") or []:
            if dep not in ids:
                return False, f"Task {item['id']} depends on unknown task {dep}"
            if dep == item["id"]:
                return False, f"Task {item['id']} cannot depend on itself"

    # Check DAG acyclicity via Kahn's algorithm
    waves = compile_dispatch_waves(plan)
    if sum(len(w) for w in waves) != len(plan):
        return False, "Dispatch plan contains a dependency cycle"
    return True, None


def compile_dispatch_waves(plan: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    by_id = {item["id"]: item for item in plan}
    in_deg = {item["id"]: len(item.get("dependsOn") or []) for item in plan}
    children: dict[str, list[str]] = {item["id"]: [] for item in plan}
    for item in plan:
        for dep in item.get("dependsOn") or []:
            if dep in children:
                children[dep].append(item["id"])

    waves: list[list[dict[str, Any]]] = []
    ready = [tid for tid, deg in in_deg.items() if deg == 0]
    while ready:
        wave = [by_id[tid] for tid in ready]
        waves.append(wave)
        next_ready: list[str] = []
        for tid in ready:
            for ch in children[tid]:
                in_deg[ch] -= 1
                if in_deg[ch] == 0:
                    next_ready.append(ch)
        ready = next_ready
    return waves
