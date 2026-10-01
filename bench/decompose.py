"""V2 분해 -- 같은 과제에서 머리 몫 · 구조 몫(두 길)과 강한 기준선 대비 구조 몫. docs/PREREG_토큰차이_V2.md."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path


def load(d: Path):
    runs = [json.loads(x) for x in (d / "runs.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    cell = {}
    for r in runs:
        c = cell.setdefault((r["task"], r["arm"]), {"t": [], "ok": 0, "n": 0})
        c["t"].append(r["total"])
        c["ok"] += r["ok"]
        c["n"] += 1
    return cell


def cmp(cell, task, a, b):
    """a 가 b 보다 큰가(= b 쪽이 줄였나). 비 · 범위 겹침 · 성공 비교 · 판정."""
    A, B = cell.get((task, a)), cell.get((task, b))
    if not A or not B:
        return None
    ma, mb = sum(A["t"]) / len(A["t"]), sum(B["t"]) / len(B["t"])
    sep = min(A["t"]) > max(B["t"])
    succ = B["ok"] >= A["ok"]
    same = A["ok"] == B["ok"]
    verdict = ("섰다" if sep and same else "성공이 달라 주장 안 함" if not same else "차이 없음(범위 겹침)")
    return {"pair": f"{a}/{b}", "ratio": ma / mb if mb else math.inf, "log": math.log(ma / mb) if ma and mb else None,
            "a": f"{ma:,.0f} ({A['ok']}/{A['n']})", "b": f"{mb:,.0f} ({B['ok']}/{B['n']})", "verdict": verdict}


def main(d):
    cell = load(Path(d))
    out = []
    for task in sorted({t for t, _ in cell}):
        out.append(f"### {task}")
        rows = [("머리 몫 · 루프", "LOOP", "LOOPB"), ("머리 몫 · MBA", "MBA", "BARE"), ("머리 몫 · 사건", "EVENT", "EVENTB"),
                ("구조 몫 · 기본 머리 (루프→MBA)", "LOOP", "MBA"), ("구조 몫 · 작은 머리 (루프→MBA)", "LOOPB", "BARE"),
                ("강한 기준선 · 기본 머리 (사건→MBA)", "EVENT", "MBA"), ("강한 기준선 · 작은 머리 (사건→MBA)", "EVENTB", "BARE"),
                ("전체 (LOOP→BARE)", "LOOP", "BARE")]
        out.append("| 비교 | 쌍 | 큰 쪽 | 작은 쪽 | 비 | 판정 |")
        out.append("|---|---|---|---|---|---|")
        logs = {}
        for name, a, b in rows:
            c = cmp(cell, task, a, b)
            if c:
                logs[(a, b)] = c["log"]
                out.append(f"| {name} | {c['pair']} | {c['a']} | {c['b']} | {c['ratio']:.3f} | {c['verdict']} |")
        if all(k in logs and logs[k] is not None for k in [("LOOP", "LOOPB"), ("LOOPB", "BARE"), ("LOOP", "MBA"), ("MBA", "BARE")]):
            tot = logs[("LOOP", "LOOPB")] + logs[("LOOPB", "BARE")]
            if tot:
                out.append(f"\n전체 로그비 {tot:.3f} 의 몫 -- 길 1: 머리(루프) {logs[('LOOP','LOOPB')]/tot:.0%} + 구조(작은 머리) "
                           f"{logs[('LOOPB','BARE')]/tot:.0%} · 길 2: 구조(기본 머리) {logs[('LOOP','MBA')]/tot:.0%} + 머리(MBA) "
                           f"{logs[('MBA','BARE')]/tot:.0%}")
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    print(main(sys.argv[1]))
