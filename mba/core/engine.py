"""코어 한 주기: 관측 -> 상태 비트 -> (바뀐 곳만) 진단 -> 파생 비트 -> 사건 -> 중재(토큰 경계) -> 안전 -> 행동.

LLM 을 부르지 않는다(T0). 같은 IR + 같은 관측 열 -> 같은 행동 열(난수 · 벽시계 없음).
"""
from __future__ import annotations

import hashlib

from mba.core import boundary
from mba.core.diagnosis import diagnose
from mba.core.guard import holds

_CMP = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b,
        ">=": lambda a, b: a >= b, "==": lambda a, b: a == b, "!=": lambda a, b: a != b}


class Engine:
    def __init__(self, model, mission: dict):
        self.m = model
        self.mission = dict(mission)
        self.tick = 0
        self.prev_obs = set()
        self.prev_bits = set()
        self.run_len = {}
        self.modes = {c: "NOMINAL" for c in model.components}
        self.cache = {}                         # [4] 믿음 캐시: (원뿔, 원뿔 비트) -> 진단 결과
        self.trace = []
        self.raw = {}
        self.diag_runs = 0
        self.examined_components = []           # 시험용: 진단이 실제로 읽은 부품

    # [8] -> [3]
    def _observe(self, raw: dict) -> set:
        bits = set()
        for sid, chan, cmp, val, ticks in self.m.observe:
            v = raw.get(chan)
            ok = v is not None and _CMP[cmp](v, val)
            self.run_len[sid] = self.run_len.get(sid, 0) + 1 if ok else 0
            if self.run_len[sid] >= ticks:
                bits.add(sid)
        for sid, k, v in self.m.priors:
            if self.mission.get(k) == v:
                bits.add(sid)
        return bits

    # [5] 바뀐 관측에 걸린 부품과, 그것에 기대는 부품의 원뿔만
    def _diagnose(self, obs: set, changed: set) -> dict:
        roots = {c.cid for c in self.m.components.values() if changed & set(c.observed_by)}
        if not roots:
            return {}
        roots |= {c for c in self.m.components if roots & set(self.m.cone(c))}
        cone = sorted({x for r in roots for x in self.m.cone(r)})
        cone_bits = {s for x in cone for s in self.m.components[x].observed_by}
        key = (tuple(cone), tuple(sorted(obs & cone_bits)))
        hit = key in self.cache
        if not hit:
            self.diag_runs += 1
            self.examined_components.append(tuple(cone))
            self.cache[key] = diagnose(self.m, cone, obs)
        r = self.cache[key]
        if r["unknown"]:
            for c in sorted(roots):
                self.modes[c] = "UNKNOWN"
        else:
            self.modes.update(r["modes"])
        return {"cone": cone, "cache": "hit" if hit else "miss", "k": r["k"], "unknown": r["unknown"],
                "ambiguous": len(r["ambiguous"]), "examined": r["examined"]}

    def tau(self, action: str, bits: set) -> int:
        for a, t, g in self.m.costs:
            if a == action and holds(g, bits, self.modes, self.mission):
                if isinstance(t, tuple):                                  # COST ... TOKENS CHANNEL x.y
                    return int(self.raw.get(t[1]) or 0)
                return self.m.budget["RECONFIG"] if t == "RECONFIG" else t
        return 0

    def cycle(self, raw: dict) -> dict:
        self.tick += 1
        self.raw = raw
        obs = self._observe(raw)
        changed = obs ^ self.prev_obs
        diag = self._diagnose(obs, changed)
        bits = set(obs)
        for sid, g in self.m.derive:                                  # 선언 순서 -- 앞의 파생을 뒤가 쓸 수 있다
            if holds(g, bits, self.modes, self.mission):
                bits.add(sid)
        events = set()
        for eid, edge, sid in self.m.events:
            if (edge == "RISE" and sid in bits and sid not in self.prev_bits) or \
               (edge == "FALL" and sid not in bits and sid in self.prev_bits):
                events.add(eid)
        proposals = []
        for i, bid in enumerate(self.m.order):                        # [6] 층마다 하나
            b = self.m.behaviors[bid]
            if not holds(b.enabled, bits, self.modes, self.mission):
                continue
            for r in b.rules:
                if (not r.on or events & set(r.on)) and holds(r.guard, bits, self.modes, self.mission):
                    if r.action:
                        proposals.append((i, r.rid, r.action))
                    break
        ranked = boundary.order(proposals, lambda a: self.tau(a, bits))
        chosen, vetoed = None, []
        for i, rid, act in ranked:                                    # 안전층은 토큰 경계 밖 -- 그대로 지난다
            why = next((reason for acts, g, reason in self.m.vetoes
                        if act in acts and holds(g, bits, self.modes, self.mission)), None)
            if why:
                vetoed.append((act, why))
                continue
            chosen = (self.m.order[i], rid, act)
            break
        if chosen is None:
            g, act = next((g, a) for g, a in self.m.fallbacks if holds(g, bits, self.modes, self.mission))
            chosen = ("SAFETY", "FALLBACK", act)
        self.prev_obs, self.prev_bits = obs, bits
        rec = {"tick": self.tick, "action": chosen[2], "behavior": chosen[0], "rule": chosen[1],
               "tau": self.tau(chosen[2], bits), "proposals": [(self.m.order[i], r, a) for i, r, a in ranked],
               "vetoed": vetoed, "events": sorted(events), "bits": sorted(bits), "diag": diag,
               "modes": {c: m for c, m in sorted(self.modes.items()) if m != "NOMINAL"}, "ir": self.m.ir_hash}
        self.trace.append(rec)
        return rec

    def action_hash(self) -> str:
        return hashlib.sha256("|".join(r["action"] for r in self.trace).encode()).hexdigest()[:16]
