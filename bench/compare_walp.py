"""MBA 앞단 대 walp-front 앞단 -- docs/PREREG_비교_walp.md.

    WALP_ROOT=/home/user/walp python3 bench/compare_walp.py --out bench/results/<ID>
    python3 bench/compare_walp.py --out bench/results/<ID> --reuse-baseline     # 기준선을 다시 안 돌림

기준선(claude -p 24 회)을 한 번 돌리고, 앞단 팔은 판정만 새로 한다. 통과 몫은 기준선의 것을 그대로 쓴다.
MBA-1 의 컴파일은 진짜 호출이다(같은 프롬프트의 컴파일 결과는 이 실행 안에서 재사용 -- STACK 이 같은 것을 다시 부르지 않게).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, os.environ.get("WALP_ROOT", "/home/user/walp"))

from mba.core.boundary import should_call_llm  # noqa: E402
from mba.core.confidence import Confidence  # noqa: E402
from mba.front import compiler, ops  # noqa: E402
from mba.front.intent import canonical_question  # noqa: E402
from walp.llmfront import REPLY, SmallTalk  # noqa: E402

MODEL = "claude-haiku-4-5-20251001"
KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
GIT_ENV = {"GIT_AUTHOR_NAME": "bench", "GIT_AUTHOR_EMAIL": "bench@local",
           "GIT_COMMITTER_NAME": "bench", "GIT_COMMITTER_EMAIL": "bench@local"}
SUBJECT = "fix: 경계 조건 정리 7f3"
HEAD_Q = "이 저장소의 현재 HEAD 커밋 제목이 뭐야? 제목만 답해."
UNCOMMITTED_Q = "지금 작업 트리에 커밋 안 된 파일이 뭐가 있어?"
HTTP_Q = "HTTP 404 는 무슨 뜻이야? 한 문장으로."

has = lambda *xs: (lambda a: any(x in a for x in xs))
ANY = lambda a: True
LOST = lambda a: False                                          # 상태를 바꾸는 일 -- 가로채면 일을 잃는다
http_ok = lambda a: "404" in a and any(x in a for x in ("찾을 수 없", "Not Found", "not found"))

PROMPTS = [
    (1, "안녕하세요", "잡담", ANY), (2, "고마워요", "잡담", ANY), (3, "잘 가", "잡담", ANY),
    (4, "넌 누구야?", "잡담", ANY), (5, "뭘 할 수 있어?", "잡담", ANY), (6, "고마워!", "잡담", ANY),
    (7, "고마워~ 근데 현재 HEAD 커밋 제목 알려줘", "섞임", has(SUBJECT)),
    (8, "안녕! 17 곱하기 23은?", "섞임", has("391")),
    (9, HEAD_Q, "저장소 질문", has(SUBJECT)), (10, HEAD_Q, "저장소 질문", has(SUBJECT)),
    (11, "현재 HEAD 커밋 제목 알려줘", "저장소 질문", has(SUBJECT)),
    (12, "HEAD 커밋의 제목만 말해줘", "저장소 질문", has(SUBJECT)),
    (13, "17 곱하기 23은?", "일반", has("391")), (14, "17 곱하기 23은?", "일반", has("391")),
    (15, "파이썬에서 리스트를 뒤집는 한 줄 코드는?", "일반", has("[::-1]", "reverse")),
    (16, HTTP_Q, "일반", http_ok),
    (17, "NOTES.md 파일을 만들고 'hello' 한 줄을 써줘.", "상태 바꿈", LOST),
    (18, HEAD_Q, "저장소 질문", has(SUBJECT)),
    (19, UNCOMMITTED_Q, "일반", has("NOTES.md")), (20, UNCOMMITTED_Q, "일반", has("NOTES.md")),
    (21, "NOTES.md 를 지워줘.", "상태 바꿈", LOST),
    (22, UNCOMMITTED_Q, "일반", lambda a: "NOTES.md" not in a),
    (23, "고마워요", "잡담", ANY), (24, HTTP_Q, "일반", http_ok),
]


def make_repo(d: Path) -> Path:
    r = d / "repo"
    r.mkdir(parents=True)
    env = {**os.environ, **GIT_ENV}
    for a in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", SUBJECT]):
        if a[0] == "add":
            (r / "a.txt").write_text("a\n")
        subprocess.run(["git", *a], cwd=r, check=True, capture_output=True, env=env)
    return r


def claude(prompt: str, cwd: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SESSION_ID"}
    env.update(GIT_ENV, MBA_FRONT_MODE="off")
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--model", MODEL, "--dangerously-skip-permissions",
           "--strict-mcp-config", "--session-id", str(uuid.uuid4())]
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=600, env=env)
    lat = time.time() - t0
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        d = {"is_error": True, "result": (r.stdout + r.stderr)[-300:]}
    u = d.get("usage") or {}
    return {"answer": d.get("result") or "", "tokens": sum(int(u.get(k) or 0) for k in KEYS),
            "usage": {k: int(u.get(k) or 0) for k in KEYS}, "latency": round(lat, 2), "is_error": bool(d.get("is_error")),
            "turns": d.get("num_turns")}


def baseline(out: Path) -> list:
    d = Path(tempfile.mkdtemp(prefix="cmp-", dir="/tmp/claude-0"))
    repo = make_repo(d)
    rows = []
    for i, text, cls, _ in PROMPTS:
        before = ops.state_hash(repo)
        c = claude(text, repo)
        after = ops.state_hash(repo)
        rows.append({"i": i, "text": text, "cls": cls, "before": before, "after": after, **c})
        print(f"BASE {i:2} {cls:6} tok={c['tokens']:>6} {c['latency']:>5}s err={c['is_error']} {c['answer'][:50]!r}",
              flush=True)
        if c["is_error"]:                                    # 무효 실행 1 의 교훈: 오류를 보고도 끝까지 돌지 않는다
            raise SystemExit(f"기준선 {i} 번이 오류로 끝났다 -- 실행을 멈춘다: {c['answer'][:120]!r}")
    (out / "baseline.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    (out / "repo_path").write_text(str(repo))
    return rows


class MBAFront:
    """mba-front on 모드의 판정을 그대로: 캐시 -> 관문 -> 작은 머리 컴파일 -> 켠 종류(cache, QUERY)만 막는다."""

    def __init__(self, prior, repo: Path, compiles: dict, theta=0.8):
        self.conf = Confidence({"compile": prior})
        self.theta, self.repo, self.compiles, self.cache = theta, repo, compiles, {}

    def route(self, row) -> dict:
        key = f"{canonical_question(row['text'].strip())}|{row['before']}" if row["before"] else None
        t0 = time.time()
        if key and key in self.cache:
            return {"by": "cache", "answer": self.cache[key], "tokens": 0, "latency": time.time() - t0}
        if not should_call_llm(self.conf.p("compile:any"), self.theta):
            return {"by": None, "compile_tokens": 0, "compile_latency": time.time() - t0}
        text = row["text"]
        if text not in self.compiles:
            t1 = time.time()
            c = compiler.compile_request(text, self.repo)
            c["latency"] = time.time() - t1
            self.compiles[text] = c
        c = self.compiles[text]
        try:
            op = ops.parse_op(c["text"]) if c["ok"] else None
        except ops.OpError:
            op = None
        if op and op["OP"] == "QUERY":
            return {"by": "QUERY", "answer": ops.execute(op, self.repo), "tokens": c["tokens"],
                    "latency": c["latency"], "op": "QUERY"}
        return {"by": None, "compile_tokens": c["tokens"], "compile_latency": c["latency"],
                "op": op["OP"] if op else "ERR"}

    def after_claude(self, row):
        """훅의 Stop 과 같다: 저장소를 안 바꾼 턴의 답만 캐시에."""
        if row["before"] and row["before"] == row["after"] and row["answer"].strip():
            self.cache[f"{canonical_question(row['text'].strip())}|{row['before']}"] = row["answer"]


def run_arm(name: str, base: list, repo: Path, compiles: dict) -> list:
    st = SmallTalk() if name in ("WALP", "STACK") else None
    mba = (MBAFront((1, 1), repo, compiles) if name == "MBA-0" else
           MBAFront((9, 1), repo, compiles) if name in ("MBA-1", "STACK") else None)
    out = []
    for row, (_, _, _, check) in zip(base, PROMPTS):
        res = None
        if st is not None:
            t0 = time.time()
            act = st.act(row["text"].strip())
            if act:
                res = {"by": "WALP:" + act, "answer": REPLY[act], "tokens": 0, "latency": time.time() - t0}
        extra_tok = extra_lat = 0.0
        if res is None and mba is not None:
            r = mba.route(row)
            if r["by"]:
                res = r
            else:
                extra_tok, extra_lat = r.get("compile_tokens", 0), r.get("compile_latency", 0)
        if res is None:                                                   # Claude 로 -- 기준선의 것
            res = {"by": None, "answer": row["answer"], "tokens": row["tokens"] + extra_tok,
                   "latency": row["latency"] + extra_lat, "compile_tokens": extra_tok}
            if mba is not None:
                mba.after_claude(row)
        res["i"], res["cls"] = row["i"], row["cls"]
        res["correct"] = bool(check(res["answer"])) if res["by"] else None
        out.append(res)
    return out


def summarize(arms: dict, base: list) -> str:
    bad = [r["i"] for r, p in zip(base, PROMPTS) if r["is_error"] or (p[3] not in (ANY, LOST) and not p[3](r["answer"]))]
    lines = [f"기준선에서 오류이거나 Claude 도 판정을 못 넘은 프롬프트: {bad or '없음'}", "",
             "| 팔 | LLM 토큰 합 | 기준선 대비 | 가로챔 | 그중 틀림 | 컴파일 토큰 | 지연 합(s) | 지연 중앙(s) |",
             "|---|---|---|---|---|---|---|---|"]
    base_tok = sum(r["tokens"] for r in base)
    for name, rows in arms.items():
        tok = sum(r["tokens"] for r in rows)
        inter = [r for r in rows if r["by"]]
        wrong = [r for r in inter if not r["correct"]]
        comp = sum(r.get("compile_tokens", 0) for r in rows) + sum(r["tokens"] for r in inter)
        lat = [r["latency"] for r in rows]
        lines.append(f"| {name} | {tok:,} | {tok / base_tok:.3f} | {len(inter)} | {len(wrong)} | {int(comp):,} | "
                     f"{sum(lat):.1f} | {statistics.median(lat):.2f} |")
    lines += ["", "### 가로챈 프롬프트 (번호:경로, ✗ = 틀림)", ""]
    for name, rows in arms.items():
        lines.append(f"- **{name}**: " + (", ".join(f"{r['i']}:{r['by']}{'' if r['correct'] else '✗'}"
                                                    for r in rows if r["by"]) or "없음"))
    lines += ["", "### 부류별 가로챔 (가로챔/그 부류 수)", "",
              "| 팔 | " + " | ".join(dict.fromkeys(p[2] for p in PROMPTS)) + " |",
              "|---|" + "---|" * len(dict.fromkeys(p[2] for p in PROMPTS))]
    for name, rows in arms.items():
        cells = []
        for c in dict.fromkeys(p[2] for p in PROMPTS):
            rs = [r for r in rows if r["cls"] == c]
            cells.append(f"{sum(1 for r in rs if r['by'])}/{len(rs)}")
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--reuse-baseline", action="store_true")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.reuse_baseline:
        base = [json.loads(x) for x in (out / "baseline.jsonl").read_text().splitlines() if x.strip()]
    else:
        base = baseline(out)
    repo = Path((out / "repo_path").read_text().strip())
    compiles = {}
    arms = {"BASE": [{"by": None, "answer": r["answer"], "tokens": r["tokens"], "latency": r["latency"],
                      "i": r["i"], "cls": r["cls"], "correct": None} for r in base]}
    for name in ("WALP", "MBA-0", "MBA-1", "STACK"):
        arms[name] = run_arm(name, base, repo, compiles)
        print(f"{name} 끝", flush=True)
    (out / "arms.json").write_text(json.dumps({"arms": arms, "compiles": {k: {kk: vv for kk, vv in v.items()}
                                                                         for k, v in compiles.items()}},
                                              ensure_ascii=False, indent=1))
    s = summarize(arms, base)
    (out / "summary.md").write_text(s + "\n")
    print(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
