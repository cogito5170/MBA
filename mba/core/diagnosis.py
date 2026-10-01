"""증분 모델 기반 진단 -- 바뀐 관측에 걸린 부품의 **원뿔만** 깨운다(docs/설계.md §2.5(c) · §3.4).

일관성 기반: 후보(부품 -> 모드)가 원뿔의 모든 PREDICT 와 지금 관측에 모순되지 않으면 산다.
한 부품의 NOMINAL 예측은 **그 부품이 기대는 부품이 모두 NOMINAL 일 때만** 묻는다(기대는 부품의 고장이 이탈을
설명할 수 있다).

다중 고장(사용자 결정 2026-10-01, "무조건 토큰 절약"): 단일 고장으로 설명이 안 되면 UNKNOWN 으로 넘기지 않고
**코어 안에서** k = 2, 3, ... 으로 넓힌다. CPU 는 쓰지만 토큰은 0 이다. UNKNOWN(그 뒤의 임무 재구성 = 토큰)은
후보 예산(BUDGET CANDIDATES)을 다 쓴 뒤에만 간다.
"""
from __future__ import annotations

import itertools

from mba.core.guard import holds


def _consistent(model, cone, assign, bits) -> bool:
    for cid in cone:
        c = model.components[cid]
        mode = assign.get(cid, "NOMINAL")
        if mode == "NOMINAL" and any(assign.get(d, "NOMINAL") != "NOMINAL" for d in model.cone(cid) if d != cid):
            continue
        g = c.predict.get(mode)
        if g is not None and not holds(g, bits, {}, {}):
            return False
    return True


def diagnose(model, cone: list, bits: set) -> dict:
    """-> {"modes": {cid: mode}, "k": 고장 수, "ambiguous": [다른 최소 후보], "examined": 본 후보 수,
           "unknown": bool}. 결정적: 후보는 (고장 수, 사전 순위 합, 부품 ID) 순."""
    budget = model.budget["CANDIDATES"]
    faults = {cid: sorted((m for m in model.components[cid].modes if m not in ("NOMINAL", "UNKNOWN")),
                          key=lambda m: (model.components[cid].modes[m], m)) for cid in cone}
    examined = 0
    for k in range(0, len(cone) + 1):
        found = []
        for comps in itertools.combinations(cone, k):
            if any(not faults[c] for c in comps):
                continue
            for modes in itertools.product(*(faults[c] for c in comps)):
                examined += 1
                if examined > budget:
                    return {"modes": {}, "k": None, "ambiguous": [], "examined": examined - 1, "unknown": True}
                assign = dict(zip(comps, modes))
                if _consistent(model, cone, assign, bits):
                    rank = sum(model.components[c].modes[m] for c, m in assign.items())
                    found.append((rank, tuple(sorted(assign.items())), assign))
        if found:
            found.sort(key=lambda x: (x[0], x[1]))
            best = found[0][2]
            return {"modes": {c: best.get(c, "NOMINAL") for c in cone}, "k": k,
                    "ambiguous": [dict(f[1]) for f in found[1:]], "examined": examined, "unknown": False}
    return {"modes": {}, "k": None, "ambiguous": [], "examined": examined, "unknown": True}
