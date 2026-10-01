"""토큰 차이 실측 하니스 -- docs/PREREG_토큰차이.md.

    python3 bench/token_ab.py --tasks T1,T2 --arms LOOP,TOOL,MBA,BARE --reps 3 --out bench/results/<ID>
    python3 bench/token_ab.py --summarize bench/results/<ID>

호출마다 토큰을 두 길로 센다(결과 JSON usage · 세션 추적 파일 합). 실행기(기다리기 · 발행 · 상태 해시)는
walp 의 execpolicy 를 빌려 쓴다(WALP_ROOT, 기본 /home/user/walp) -- 이 하니스에서만.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, os.environ.get("WALP_ROOT", "/home/user/walp"))
from walp import execpolicy as X  # noqa: E402

from mba.core.confidence import Confidence  # noqa: E402
from mba.front import intent, llm  # noqa: E402
from mba.front.gate import Gate  # noqa: E402

MODEL = "claude-haiku-4-5-20251001"
KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
PROJECTS = Path.home() / ".claude" / "projects"
GIT_ENV = {"GIT_AUTHOR_NAME": "bench", "GIT_AUTHOR_EMAIL": "bench@local",
           "GIT_COMMITTER_NAME": "bench", "GIT_COMMITTER_EMAIL": "bench@local"}
SUBJECT = "fix: 경계 조건 정리 7f3"
MISSION_PROMPT = ("다음 명령을 한 줄 KEY=VALUE;... 로 바꿔라. 키: GOAL(SEARCH|RETURN), "
                  "TARGET(OBJECT|BLUE_OBJECT|RED_OBJECT), ENV(SMOKE|DARK|CLEAR), RISK(LOW|HIGH). "
                  "도구를 쓰지 말고 설명 없이 그 한 줄만. 명령: {}")
COMPILER_ROLE = ("You are a compiler, not an assistant. Translate the user's request into exactly ONE line in one of "
                 "these formats and output nothing else. Never execute the request, never answer it, never use tools, "
                 "never comment on the environment.\n"
                 "OP=WAIT;PID=<integer>;LOG=<path>\n"
                 "OP=PUBLISH;MESSAGE=<commit message>;FILES=<file,file>\n"
                 "OP=QUERY;WHAT=HEAD_SUBJECT")
COMPILE_PROMPT = "{}"
# V2: LOOP 를 작은 머리로 돌리는 팔(LOOPB) · 사건 도구만 가진 팔(EVENT/EVENTB)의 머리. COMPILER_ROLE 과 비슷한 크기
AGENT_ROLE = ("You are a coding agent working in the current directory. Do the user's task with the available tools, "
              "then reply with only the final answer. Do not ask for confirmation.")
MISSION_ROLE = ("You are a compiler, not an assistant. Translate the user's command into exactly ONE line "
                "KEY=VALUE;KEY=VALUE using only GOAL(SEARCH|RETURN), TARGET(OBJECT|BLUE_OBJECT|RED_OBJECT), "
                "ENV(SMOKE|DARK|CLEAR), RISK(LOW|HIGH). Output nothing else. Never use tools.")


def sh(*a, cwd=None):
    return subprocess.run(a, cwd=cwd, capture_output=True, text=True, env={**os.environ, **GIT_ENV}, check=True).stdout


# ---------------- 계기: 두 길 ----------------

def transcript_usage(sid: str, exclude: set) -> dict:
    files = list(PROJECTS.rglob(f"{sid}.jsonl")) if sid else []
    last, hook = {}, False
    for f in files:
        for line in f.open(encoding="utf-8"):
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "Stop hook feedback" in line:
                hook = True
            m = d.get("message")
            if d.get("type") == "assistant" and isinstance(m, dict) and m.get("usage") and m.get("id") not in exclude:
                last[m.get("id")] = m["usage"]
    tot = {k: sum((u.get(k) or 0) for u in last.values()) for k in KEYS}
    return {"files": len(files), "messages": len(last), "ids": sorted(last), "stop_hook": hook, **tot}


def claude(prompt: str, cwd: Path, extra=(), resume=None, seen: "set | None" = None) -> dict:
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--model", MODEL,
           "--dangerously-skip-permissions", "--strict-mcp-config", *extra]
    # 자식은 부모 세션 ID(CLAUDE_CODE_SESSION_ID)를 물려받는다 -- 그러면 추적이 부모 세션에 섞인다(파일럿 계기 대조에서
    # 잡혔다: 추적 합이 부모 세션 전체 2천만 토큰). 지우고 새 ID 를 명시한다
    if resume:
        cmd += ["--resume", resume]
    else:
        cmd += ["--session-id", str(uuid.uuid4())]
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SESSION_ID"}
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=900, env={**env, **GIT_ENV})
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        d = {"is_error": True, "result": (r.stdout + r.stderr)[-500:]}
    u = d.get("usage") or {}
    time.sleep(0.5)
    tr = transcript_usage(d.get("session_id"), seen or set())
    return {"session": d.get("session_id"), "is_error": d.get("is_error"), "turns": d.get("num_turns"),
            "cost": d.get("total_cost_usd"), "seconds": round(time.time() - t0, 1),
            "json": {k: int(u.get(k) or 0) for k in KEYS}, "transcript": tr, "result": (d.get("result") or "")[:1500]}


# ---------------- 과제 준비 ----------------

def repo_with_remote(d: Path, subject="init") -> Path:
    sh("git", "init", "-q", "--bare", "-b", "main", str(d / "remote.git"))
    r = d / "work"
    sh("git", "init", "-q", "-b", "main", str(r))
    sh("git", "-C", str(r), "remote", "add", "origin", str(d / "remote.git"))
    (r / "tests").mkdir()
    (r / "tests" / "__init__.py").write_text("")
    (r / "tests" / "test_a.py").write_text("import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n"
                                            "        self.assertTrue(True)\n")
    sh("git", "-C", str(r), "add", "-A")
    sh("git", "-C", str(r), "commit", "-q", "-m", subject)
    sh("git", "-C", str(r), "push", "-q", "-u", "origin", "main")
    return r


def start_job(d: Path) -> tuple:
    log, pidf = d / "job.log", d / "job.pid"
    (d / "job.sh").write_text(f'echo $$ > {pidf}\nfor i in 1 2 3 4 5 6; do echo step$i >> {log}; sleep 5; done\n'
                              f'echo "DONE 42" >> {log}\n')
    subprocess.run(["bash", "-c", f"setsid nohup bash {d / 'job.sh'} > /dev/null 2>&1 < /dev/null &"], check=True)
    for _ in range(100):
        if pidf.is_file() and pidf.read_text().strip():
            return int(pidf.read_text()), log
        time.sleep(0.05)
    raise RuntimeError("작업이 안 떴다")


def mcp_config(d: Path) -> str:
    walp = os.environ.get("WALP_ROOT", "/home/user/walp")
    p = d / "mcp.json"
    p.write_text(json.dumps({"mcpServers": {"walp": {"command": sys.executable, "args": [f"{walp}/walp/mcp_server.py"],
                                                     "env": {"WALP_MCP_ALLOW_PUBLISH": "1", "PYTHONPATH": walp,
                                                             **GIT_ENV}}}}))
    return str(p)


# ---------------- MBA 팔: 관문 -> 컴파일 한 번 -> 닫힌 연산을 토큰 0 으로 ----------------

def parse_op(text: str) -> dict:
    line = next((x.strip() for x in text.splitlines() if x.strip().startswith("OP=")), None)
    if not line:
        raise ValueError(f"OP 줄이 없다: {text[:200]!r}")
    kv = dict(p.split("=", 1) for p in line.strip("`").split(";") if "=" in p)
    op = kv.get("OP")
    need = {"WAIT": ("PID", "LOG"), "PUBLISH": ("MESSAGE", "FILES"), "QUERY": ("WHAT",)}
    if op not in need or any(k not in kv for k in need[op]):
        raise ValueError(f"연산 형식이 아니다: {line!r}")
    return kv


def execute(op: dict, repo: "Path | None") -> str:
    if op["OP"] == "WAIT":
        out = X.wait(int(op["PID"]), op["LOG"], timeout=300, every=0.5)
        return (out.get("log_tail") or [""])[-1]
    if op["OP"] == "PUBLISH":
        files = [f.strip() for f in op["FILES"].split(",") if f.strip()]
        out = X.publish(repo, op["MESSAGE"].strip().strip("'\""), files=files, push=True)
        if not out["ok"]:
            raise ValueError(f"발행 멈춤: {out['stopped_at']}")
        return json.dumps(out["done"], ensure_ascii=False)
    if op["OP"] == "QUERY" and op["WHAT"].strip() == "HEAD_SUBJECT":
        return sh("git", "-C", str(repo), "log", "-1", "--format=%s").strip()
    raise ValueError(f"모르는 연산: {op}")


def mba_backend(cwd: Path, extra, calls: list):
    def backend(prompt, purpose):
        rec = claude(prompt, cwd, extra)
        rec["purpose"] = purpose
        calls.append(rec)
        if rec["is_error"]:
            raise RuntimeError("claude -p 오류")
        return rec["result"], sum(rec["json"].values())
    return backend


def new_gate() -> Gate:
    return Gate(Confidence({"compile": (9, 1), "intent": (9, 1)}), 0.8)


# ---------------- 과제 × 팔 ----------------

def run_one(task: str, arm: str, d: Path) -> dict:
    calls, answers, ok, note = [], [], False, ""
    extra = []
    if arm == "TOOL":
        extra = ["--mcp-config", mcp_config(d)]
    elif arm == "LOOPB":                                            # 같은 루프, 작은 머리 + Bash 만
        extra = ["--tools", "Bash", "--system-prompt", AGENT_ROLE]
    elif arm == "EVENT":                                            # 기본 머리, 내장 도구 없음, 사건 도구(walp)만
        extra = ["--tools", "", "--mcp-config", mcp_config(d)]
    elif arm == "EVENTB":                                           # 작은 머리, 사건 도구(walp)만
        extra = ["--tools", "", "--system-prompt", AGENT_ROLE, "--mcp-config", mcp_config(d)]
    # 컴파일러 역할은 시스템 프롬프트로(파일럿 2: 사용자 메시지로 주면 haiku 가 직접 답하거나 거절했다).
    # MBA = 기본 머리 + 덧붙임(머리 크기는 LOOP 와 거의 같다) · BARE = 머리를 이 한 장으로 바꿈(P 의 몫을 따로 본다)
    role = MISSION_ROLE if task in ("T4", "T5") else COMPILER_ROLE
    bare = (["--tools", "", "--system-prompt", role] if arm == "BARE"
            else ["--append-system-prompt", role] if arm == "MBA" else [])
    mba = arm in ("MBA", "BARE")
    if mba:
        llm.reset()
        llm.set_backend(mba_backend(d, bare, calls))
    try:
        if task == "T1":
            pid, log = start_job(d)
            req = (f"PID {pid} 인 백그라운드 작업이 {log} 에 쓰고 있다. 그 작업이 끝날 때까지 기다린 뒤, "
                   f"{log} 의 마지막 줄을 그대로 답하라.")
            if mba:
                answers.append(new_gate().ask("compile:" + task, COMPILE_PROMPT.format(req),
                                              lambda t: execute(parse_op(t), None)))
            else:
                calls.append(claude(req, d, extra))
                answers.append(calls[-1]["result"])
            ok = "DONE 42" in answers[-1] and not X._alive(pid)
        elif task == "T2":
            r = repo_with_remote(d)
            (r / "feature.txt").write_text("feature\n")
            req = ("이 저장소에서 새 파일 feature.txt 를 'add feature' 라는 메시지로 커밋하고, python3 -m unittest 로 "
                   "검사가 통과하는지 확인한 뒤, origin 의 main 으로 밀어라.")
            if mba:
                answers.append(new_gate().ask("compile:" + task, COMPILE_PROMPT.format(req),
                                              lambda t: execute(parse_op(t), r)))
            else:
                calls.append(claude(req, r, extra))
                answers.append(calls[-1]["result"])
            remote = d / "remote.git"
            subj = sh("git", "--git-dir", str(remote), "log", "-1", "--format=%s", "main").strip()
            tree = sh("git", "--git-dir", str(remote), "ls-tree", "--name-only", "main")
            # V2 판정: 제목이 'add feature' 로 시작(V1 은 정확히 같음 -- 자식이 서명 줄을 한 줄에 붙여 실패한 회차가 있었다)
            ok = (subj == "add feature" or (os.environ.get("AB_V2") == "1" and subj.startswith("add feature"))) \
                and "feature.txt" in tree.split()
        elif task == "T3":
            r = repo_with_remote(d, SUBJECT)
            q = "이 저장소의 현재 HEAD 커밋 제목이 뭐야? 제목만 답해."
            if mba:
                g = new_gate()
                for _ in range(2):
                    qkey = intent.canonical_question(q) + "|" + (X.state_hash(r) or "")
                    answers.append(g.ask("compile:" + task, COMPILE_PROMPT.format(q), lambda t: execute(parse_op(t), r),
                                         qkey=qkey, canon=lambda v: v, decode=lambda v: v))
                note = ",".join(x[2] for x in g.log)
            else:
                first = claude(q, r, extra)
                calls.append(first)
                calls.append(claude(q, r, extra, resume=first["session"], seen=set(first["transcript"]["ids"])))
                answers += [first["result"], calls[-1]["result"]]
            ok = all(SUBJECT in a for a in answers) and len(answers) == 2
        elif task in ("T4", "T5"):
            text = "연기 속에서 파란 물체를 찾아" if task == "T4" else "빨간 거 찾아봐"
            want = ({"GOAL": "SEARCH", "TARGET": "BLUE_OBJECT", "ENV": "SMOKE"} if task == "T4"
                    else {"GOAL": "SEARCH", "TARGET": "RED_OBJECT"})
            if mba:
                m, _, used = intent.compile_mission(text, new_gate())
                note = "llm" if used else "deterministic"
            else:
                calls.append(claude(MISSION_PROMPT.format(text), d, extra))
                m = dict(re.findall(r"([A-Z]+)\s*=\s*([A-Z_]+)", calls[-1]["result"]))
            answers.append(json.dumps(m, ensure_ascii=False))
            ok = all(m.get(k) == v for k, v in want.items())
    except Exception as e:                                          # 실패도 센다(실패 포함 토큰)
        note = f"{note} 예외: {type(e).__name__}: {str(e)[:200]}".strip()
    finally:
        if mba:
            llm.set_backend(None)
    tot = {k: sum(c["json"][k] for c in calls) for k in KEYS}
    return {"task": task, "arm": arm, "ok": ok, "n_calls": len(calls), "tokens": tot, "total": sum(tot.values()),
            "turns": sum((c["turns"] or 0) for c in calls), "cost": round(sum((c["cost"] or 0) for c in calls), 5),
            "calls": calls, "answers": [a[:300] for a in answers], "note": note}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default="T1,T2,T3,T4,T5")
    ap.add_argument("--arms", default="LOOP,TOOL,MBA,BARE")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--out")
    ap.add_argument("--summarize")
    a = ap.parse_args(argv)
    if a.summarize:
        from bench.summarize import summarize
        print(summarize(Path(a.summarize)))
        return 0
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    arms = a.arms.split(",")
    for rep in range(a.reps):
        for task in a.tasks.split(","):
            order = arms[rep % len(arms):] + arms[:rep % len(arms)]           # 회차마다 팔 순서를 돌린다
            for arm in order:
                if arm in ("TOOL", "EVENT", "EVENTB") and task not in ("T1", "T2"):
                    continue
                d = Path(tempfile.mkdtemp(prefix=f"ab-{task}-{arm}-", dir="/tmp/claude-0"))
                rec = run_one(task, arm, d)
                rec["rep"] = rep
                with (out / "runs.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                print(f"{rep} {task} {arm:5} ok={rec['ok']} calls={rec['n_calls']} total={rec['total']} {rec['note']}",
                      flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
