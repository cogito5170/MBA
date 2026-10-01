"""guard 평가 -- 비트 · 모드 · 임무 원자의 논리곱/합/부정. 산술 없음, 루프 없음."""
from __future__ import annotations


def holds(g, bits, modes, mission) -> bool:
    k = g[0]
    if k == "true":
        return True
    if k == "bit":
        return g[1] in bits
    if k == "not":
        return not holds(g[1], bits, modes, mission)
    if k == "and":
        return all(holds(x, bits, modes, mission) for x in g[1])
    if k == "or":
        return any(holds(x, bits, modes, mission) for x in g[1])
    if k == "mode":
        return modes.get(g[1], "NOMINAL") == g[2]
    if k == "mission":
        return mission.get(g[1]) == g[2]
    raise ValueError(f"모르는 guard: {g!r}")
