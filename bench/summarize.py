"""runs.jsonl -> 계기 대조(0단계) + 과제 × 팔 표. 계기가 서지 않으면 비교 표를 내지 않는다."""
from __future__ import annotations

import json
from pathlib import Path

KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
SHORT = {"input_tokens": "입력", "cache_creation_input_tokens": "캐시만듦", "cache_read_input_tokens": "캐시읽음",
         "output_tokens": "출력"}


def summarize(d: Path, tol: float = 0.05) -> str:
    runs = [json.loads(x) for x in (d / "runs.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    out = ["## 0단계 -- 계기 대조 (결과 JSON usage vs 세션 추적 합)", ""]
    worst, bad, hooks, n = 0.0, [], 0, 0
    for r in runs:
        for c in r["calls"]:
            n += 1
            a = sum(c["json"].values())
            b = sum(c["transcript"][k] for k in KEYS)
            rel = abs(a - b) / a if a else (0.0 if b == 0 else 1.0)
            worst = max(worst, rel)
            hooks += bool(c["transcript"].get("stop_hook"))
            if rel > tol:
                bad.append(f"{r['task']} {r['arm']} rep{r['rep']} 세션 {c['session']}: json {a} vs 추적 {b} ({rel:.1%})")
    out += [f"호출 {n} 개, 최대 차 {worst:.2%}, 5% 넘은 호출 {len(bad)}, stop 훅 흔적 {hooks}", ""]
    out += [f"- {x}" for x in bad[:20]]
    if bad:
        out += ["", "**계기가 서지 않았다 -- 비교를 보고하지 않는다.**"]
        return "\n".join(out)
    out += ["", "## 과제 × 팔 (과제당, 실패 포함)", "",
            "| 과제 | 팔 | 성공 | 호출 | 토큰 평균 | 범위 | " + " | ".join(SHORT[k] for k in KEYS) + " | 턴 | $ |",
            "|---|---|---|---|---|---|" + "---|" * len(KEYS) + "---|---|"]
    cell = {}
    for task in sorted({r["task"] for r in runs}):
        for arm in ("LOOP", "TOOL", "MBA", "BARE"):
            rs = [r for r in runs if r["task"] == task and r["arm"] == arm]
            if not rs:
                continue
            tot = [r["total"] for r in rs]
            mean = sum(tot) / len(tot)
            cell[(task, arm)] = mean
            by = [round(sum(r["tokens"][k] for r in rs) / len(rs)) for k in KEYS]
            out.append(f"| {task} | {arm} | {sum(r['ok'] for r in rs)}/{len(rs)} | "
                       f"{sum(r['n_calls'] for r in rs) / len(rs):.1f} | {mean:,.0f} | {min(tot):,}~{max(tot):,} | "
                       + " | ".join(f"{x:,}" for x in by)
                       + f" | {sum(r['turns'] for r in rs) / len(rs):.1f} | {sum(r['cost'] for r in rs) / len(rs):.4f} |")
    out += ["", "## 비 (평균 토큰)", ""]
    for task in sorted({t for t, _ in cell}):
        base = cell.get((task, "LOOP"))
        parts = []
        for arm in ("TOOL", "MBA", "BARE"):
            if (task, arm) in cell and base:
                parts.append(f"{arm}/LOOP = {cell[(task, arm)] / base:.3f}")
        out.append(f"- {task}: " + (" · ".join(parts) or "-"))
    return "\n".join(out)
