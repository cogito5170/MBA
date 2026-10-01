"""LLM 이 이 자리에서 정답을 낼 확률 -- 토큰 없이 추정한다(사용자 결정 2026-10-01).

토큰 경계에서 불확실한 것은 센서가 아니라 **LLM 이 맞힐지** 다. 그래서 LLM 호출의 방아쇠는 그 확률 하나다.

- 자리(key)는 기호 서명이다: 목적 + 무엇이 비었나(예: "intent:GOAL,TARGET", "model:rover/smoke").
- 정답 = LLM 출력이 **결정적 검증** 을 지났다(어휘 · 문법 · 컴파일러). 사람의 판정이나 LLM 자신의 말은 쓰지 않는다.
- 추정: 베타 사후 평균 (s + a) / (s + f + a + b). 사전 (a, b) 는 목적마다 둔다 -- 기본 (1, 1) = 0.5.
- 기록은 블랙보드(mba.front.blackboard)에 남아 다음 프로세스가 이어 쓴다.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Confidence:
    priors: dict = field(default_factory=dict)          # 목적 -> (a, b)
    counts: dict = field(default_factory=dict)          # key -> [성공, 실패]

    def p(self, key: str) -> float:
        a, b = self.priors.get(key.split(":", 1)[0], (1.0, 1.0))
        s, f = self.counts.get(key, (0, 0))
        return (s + a) / (s + f + a + b)

    def record(self, key: str, ok: bool) -> None:
        c = self.counts.setdefault(key, [0, 0])
        c[0 if ok else 1] += 1

    def to_dict(self) -> dict:
        return {"priors": {k: list(v) for k, v in self.priors.items()},
                "counts": {k: list(v) for k, v in self.counts.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> "Confidence":
        return cls({k: tuple(v) for k, v in (d or {}).get("priors", {}).items()},
                   {k: list(v) for k, v in (d or {}).get("counts", {}).items()})
