"""임무 관리자 -- 코어가 멈춘 뒤에만 토큰을 쓸 수 있는 자리(임무 재구성). LLM 은 관문(gate)으로만."""
from __future__ import annotations

from mba.front.gate import Declined


def reconfigure(mission: dict, failed: list, gate, allow: bool, used: int, limit: int = 1, validate=None) -> dict:
    """-> {"spent": bool, "reason": ..., "value": 검증된 새 임무 또는 None}."""
    if not allow:
        return {"spent": False, "reason": "재구성 허락 없음 -- 사람에게 보고", "value": None}
    if used >= limit:
        return {"spent": False, "reason": f"재구성 예산 {limit}회를 다 썼다", "value": None}
    key = "reconfig:" + ",".join(sorted(failed))
    try:
        v = gate.ask(key, f"임무 {mission} 를 실패 항목 {failed} 없이 다시 컴파일하라", validate or (lambda t: t))
    except Declined as e:
        return {"spent": False, "reason": str(e), "value": None}
    return {"spent": True, "reason": "LLM 정답 확률이 문턱을 넘었다", "value": v}
