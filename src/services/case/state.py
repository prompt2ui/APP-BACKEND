# Short Thai hints for operators reading logs (one block per step; no double blank lines).
_DEBUG_CASE_NODE_HINTS: dict[str, str] = {
    "CaseAgentEntryPoint.case_generate": "หลังดึงหน้าเว็บแล้ว — ส่ง context เข้า LLM เพื่อสร้างชุด testcase โดยตรง",
}


def debug_case_node_enter(name: str, detail: str = "") -> None:
    """Readable stdout block when a case-generation step starts (no extra empty lines)."""
    hint = _DEBUG_CASE_NODE_HINTS.get(name, "")
    sep = "─" * 76
    lines = [sep, f"[case-graph] {name}"]
    if detail:
        lines.append(f"  · {detail}")
    if hint:
        lines.append(f"  ⓘ {hint}")
    print("\n".join(lines), flush=True)
