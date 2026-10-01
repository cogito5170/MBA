"""토큰 경계의 결정 규칙 -- 사용자 결정(2026-10-01): **그 경계에서는 무조건 토큰 절약이 우선이다.**

1. 중재: 같은 주기에 나온 제안들 가운데 **그 행동이 부를 LLM 토큰(τ)이 작은 것** 이 이긴다. τ 가 같을 때만
   선언된 우선순위를 쓴다. τ 는 컴파일된 COST 표에서 읽는다(상태에 따라 달라질 수 있다 -- COST ... WHEN guard).
   예: 확정된 물체를 앞에 두고 배터리가 문턱에 닿았다. RETURN 은 확정을 버리고 임무를 미완으로 남긴다 ->
   새 임무 컴파일(τ = RECONFIG). DECLARE 는 τ = 0 -> 선언이 이긴다.
2. LLM 을 부를지(방아쇠): 그 자리에서 **LLM 이 정답을 낼 확률** p(mba.core.confidence)가 문턱 θ 를 **넘을 때만**
   부른다. p 가 θ 에 못 미치면 부르지 않는다 -- 맞히지 못할 호출은 토큰만 쓴다. p == θ 이면 아낀다(사용자 결정).
3. 안전층(VETO · FALLBACK)은 이 경계 밖이다 -- τ 로 고른 행동도 VETO 를 그대로 지난다.
"""
from __future__ import annotations


def should_call_llm(p_correct: float, threshold: float) -> bool:
    """LLM 이 맞힐 확률이 문턱을 **넘어야만** 부른다. 정확히 같으면 토큰을 아낀다(사용자 결정)."""
    return p_correct > threshold


def order(proposals: list, tau) -> list:
    """proposals: [(layer_index, rule_id, action)] -> τ 오름차순, 같으면 층 순서(선언된 우선순위)."""
    return sorted(proposals, key=lambda p: (tau(p[2]), p[0], p[1]))
