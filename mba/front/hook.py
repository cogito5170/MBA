"""mba-front -- Claude Code 의 모든 프롬프트 앞에 서는 MBA 앞단.

    UserPromptSubmit 훅:  0 탈출(//, /명령)  1 같은 질문 + 같은 저장소 상태 -> 캐시   2 작은 머리 컴파일 -> 닫힌 연산
    Stop 훅:              그 턴이 실제로 쓴 토큰 · 상태가 바뀌었나 · 무슨 도구를 썼나를 재고, 읽기만 한 턴의 답을 캐시에 넣는다

모드(MBA_FRONT_MODE):
    shadow (기본)  아무것도 막지 않는다. 가로챘다면 무엇을 냈을지 원장에 적는다. 컴파일은 **뒤에서**(지연 0) 돈다
    on            MBA_ENABLE 에 든 종류(cache · QUERY · WAIT · PUBLISH)만 실제로 막고 답한다. 컴파일은 정답 확률 관문을 지난다
    off           아무것도 안 한다(자식 claude 의 되부름 방지에도 쓴다)

막는 쪽으로 틀리지 않는다: 어떤 예외든 아무것도 출력하지 않고 0 으로 끝난다 -> 프롬프트는 그대로 Claude 에 간다.

저장(MBA_HOME, 기본 ~/.mba -- 이 기계 밖으로 안 나간다):
    ledger.jsonl     해시 · 길이 · 경로 · 토큰 수만(프롬프트 글은 안 적는다)
    blackboard.json  캐시된 답(글) · 정답 확률 기록
    pending/         턴을 맞대기 위한 임시 파일(최종 답 글 포함). `mba-front purge` 로 지운다
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from mba.core.boundary import should_call_llm
from mba.front import compiler, ops
from mba.front.blackboard import Blackboard
from mba.front.gate import Gate
from mba.front.intent import canonical_question

ROOT = Path(__file__).resolve().parents[2]
BYPASS = "//"
CLASSES = ("cache", "QUERY", "WAIT", "PUBLISH")
ELIGIBLE_MIN, ELIGIBLE_RATE = 10, 0.9          # 켜기 후보: 제안 >= 10 · 일치 >= 90% (walp 사전등록과 같은 문턱)
CACHE_SIM = 0.6                                 # 캐시 일치: 읽기만 한 턴 + 글 유사도 >= 0.6 (잠정)


def home() -> Path:
    return Path(os.environ.get("MBA_HOME") or Path.home() / ".mba")


def mode() -> str:
    m = os.environ.get("MBA_FRONT_MODE", "shadow")
    return m if m in ("shadow", "on", "off") else "shadow"


def enabled() -> set:
    return {x.strip() for x in os.environ.get("MBA_ENABLE", "").split(",") if x.strip()}


def _h(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def _append(rec: dict) -> None:
    p = home() / "ledger.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _write(p: Path, d: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _read(p: Path) -> "dict | None":
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _pending(name: str) -> Path:
    return home() / "pending" / name


def _board() -> Blackboard:
    return Blackboard(home() / "blackboard.json")


def _block(answer: str, path: str) -> dict:
    return {"decision": "block",
            "reason": f"{answer}\n(MBA 앞단이 답했다 -- {path}, Claude 에 보내지 않음. 틀렸으면 앞에 // 를 붙여 다시 보내라)"}


# ---------------- UserPromptSubmit ----------------

def on_prompt(ev: dict) -> "dict | None":
    if mode() == "off":
        return None
    prompt = str(ev.get("prompt") or "")
    sid = str(ev.get("session_id") or "")[:36]
    cwd = ev.get("cwd") or os.getcwd()
    rid = uuid.uuid4().hex[:12]
    p = prompt.strip()
    rec = {"t": round(time.time(), 3), "e": "prompt", "rid": rid, "s": sid, "len": len(p), "mode": mode()}
    if not p or p.startswith(BYPASS) or p.startswith("/"):
        _append({**rec, "path": "escape"})
        return None
    st = ops.state_hash(cwd)
    key = f"{canonical_question(p)}|{st}" if st else None
    rec["q"] = _h(canonical_question(p))
    _write(_pending(f"{sid or 'nosession'}.json"), {"rid": rid, "t": rec["t"], "state": st, "key": key, "cwd": str(cwd)})
    board = _board()
    cached = board.answers()["by_q"].get(key) if key else None
    if cached is not None:
        _write(_pending(f"{rid}.cache.json"), {"answer": cached})
        _append({**rec, "path": "cache"})
        if mode() == "on" and "cache" in enabled():
            return _block(cached, "같은 질문 · 같은 저장소 상태의 캐시, 토큰 0")
        return None
    if mode() == "shadow":
        if os.environ.get("MBA_SHADOW_COMPILE", "1") == "1":
            _write(_pending(f"{rid}.prompt.json"), {"prompt": p, "cwd": str(cwd)})
            _spawn_compile(rid)
            _append({**rec, "path": "compile_bg"})
        else:
            _append({**rec, "path": "pass"})
        return None
    # on: 관문 -> 동기 컴파일 -> 켜진 종류만 실행해 막는다
    gate = Gate.on(board, threshold=float(os.environ.get("MBA_THETA", "0.8")))
    pc = gate.conf.p("compile:any")
    if not should_call_llm(pc, gate.threshold):
        _append({**rec, "path": "declined", "p": round(pc, 3)})
        return None
    c = compiler.compile_request(p, cwd)
    out = {**rec, "path": "compile", "tokens": c["tokens"], "p": round(pc, 3)}
    try:
        op = ops.parse_op(c["text"]) if c["ok"] else None
    except ops.OpError:
        op = None
    out["op"] = op["OP"] if op else "ERR"
    if not op or op["OP"] == "NONE" or op["OP"] not in enabled():
        _append(out)
        return None
    try:
        answer = ops.execute(op, cwd)
    except ops.OpError as e:
        _append({**out, "exec": f"실패: {str(e)[:80]}"})
        return None
    _append({**out, "exec": "ok"})
    return _block(answer, f"{op['OP']} 컴파일 {c['tokens']} 토큰")


def _spawn_compile(rid: str) -> None:
    cmd = [sys.executable, "-m", "mba.front.hook", "compile-bg", rid]
    env = {**os.environ, "PYTHONPATH": str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""), "MBA_FRONT_MODE": "off"}
    env["MBA_HOME"] = str(home())
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, env=env, cwd=str(ROOT))


def compile_bg(rid: str) -> dict:
    """그림자: 뒤에서 컴파일하고 무엇을 냈을지 적는다. 바깥 · 기다리는 연산은 **실행하지 않는다**(QUERY 는 읽기만이라 실행)."""
    src = _read(_pending(f"{rid}.prompt.json")) or {}
    try:
        _pending(f"{rid}.prompt.json").unlink()
    except OSError:
        pass
    c = compiler.compile_request(src.get("prompt", ""), src.get("cwd"))
    res = {"rid": rid, "tokens": c["tokens"], "ok": c["ok"], "op": "ERR", "answer": None}
    try:
        op = ops.parse_op(c["text"]) if c["ok"] else None
        if op:
            res["op"] = op["OP"]
            if op["OP"] == "QUERY":
                res["answer"] = ops.execute(op, src.get("cwd"))
            elif op["OP"] in ("WAIT", "PUBLISH"):
                res["args"] = {k: v for k, v in op.items() if k != "OP"}
    except ops.OpError as e:
        res["error"] = str(e)[:120]
    _write(_pending(f"{rid}.compile.json"), res)
    _append({"t": round(time.time(), 3), "e": "compile", "rid": rid, "op": res["op"], "tokens": res["tokens"],
             "ok": res["ok"]})
    return res


# ---------------- Stop ----------------

_WAIT = re.compile(r"\bsleep\s+\d|kill\s+-0|\bwait\s+\$?\d|pgrep|while\s+.*ps\s+-p")
_PUSH = re.compile(r"\bgit\s+push\b")
KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def turn_from_transcript(path, since: float) -> dict:
    """그 프롬프트 뒤의 본 세션 턴: 토큰(같은 message id 는 마지막 줄 하나) · 도구 종류 · 마지막 답 글."""
    import datetime as dt
    usage, kinds, final = {}, set(), ""
    if not path or not Path(path).is_file():
        return {"tokens": None, "kinds": [], "final": ""}
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    for line in lines:
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict) or d.get("type") != "assistant" or d.get("isSidechain") or not d.get("timestamp"):
            continue
        try:
            ts = dt.datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        if ts < since:
            continue
        m = d.get("message") or {}
        if m.get("usage"):
            usage[m.get("id")] = m["usage"]
        for c in m.get("content") or []:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "text" and c.get("text", "").strip():
                final = c["text"]
            if c.get("type") == "tool_use":
                name, inp = c.get("name", ""), c.get("input") or {}
                cmd = str(inp.get("command", "")) if name == "Bash" else ""
                if name.endswith("walp_wait") or _WAIT.search(cmd):
                    kinds.add("wait")
                if name.endswith("walp_publish") or _PUSH.search(cmd):
                    kinds.add("publish")
    tokens = sum(sum(int(u.get(k) or 0) for k in KEYS) for u in usage.values())
    return {"tokens": tokens, "kinds": sorted(kinds), "final": final}


def on_stop(ev: dict) -> None:
    if mode() == "off":
        return
    sid = str(ev.get("session_id") or "")[:36]
    info = _read(_pending(f"{sid or 'nosession'}.json"))
    if not info:
        return
    turn = turn_from_transcript(ev.get("transcript_path"), info["t"])
    after = ops.state_hash(ev.get("cwd") or info.get("cwd"))
    read_only = info.get("state") is not None and after == info["state"]
    _write(_pending(f"{info['rid']}.stop.json"), {"final": turn["final"], "kinds": turn["kinds"],
                                                  "tokens": turn["tokens"], "read_only": read_only})
    if read_only and info.get("key") and turn["final"].strip():         # 같은 질문 · 같은 상태 -> 같은 답 (캐시를 채운다)
        board = _board()
        a = board.answers()
        a["by_q"][info["key"]] = turn["final"]
        board.save_answers(a)
    _append({"t": round(time.time(), 3), "e": "stop", "rid": info["rid"], "s": sid, "tokens": turn["tokens"],
             "read_only": read_only, "kinds": turn["kinds"], "final_len": len(turn["final"])})


# ---------------- 보고 ----------------

def report() -> dict:
    """종류마다: 제안 · 일치 · 틀림 · 일치율 · 켰다면 아꼈을 토큰(일치한 턴의 토큰 - 모든 컴파일 토큰) · 켜기 후보인가."""
    rows = []
    p = home() / "ledger.jsonl"
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    by = {}
    for r in rows:
        if isinstance(r, dict) and r.get("rid"):
            by.setdefault(r["rid"], {}).setdefault(r.get("e"), r)
            if r.get("e") == "stop":
                by[r["rid"]]["stop"] = r                                       # Stop 이 여러 번이면 마지막
    res = {c: {"proposals": 0, "agree": 0, "wrong": 0, "pending": 0, "saved_turn_tokens": 0} for c in CLASSES}
    totals = {"prompts": 0, "escape": 0, "compile_tokens": 0, "compiles": 0, "none_or_err": 0, "turn_tokens": 0}
    for rid, ev in by.items():
        pr, cp, sp = ev.get("prompt"), ev.get("compile"), ev.get("stop")
        if pr:
            totals["prompts"] += 1
            totals["escape"] += pr.get("path") == "escape"
        if sp and sp.get("tokens"):
            totals["turn_tokens"] += sp["tokens"]
        if cp:
            totals["compiles"] += 1
            totals["compile_tokens"] += cp.get("tokens") or 0
        if pr and pr.get("path") == "compile":
            totals["compiles"] += 1
            totals["compile_tokens"] += pr.get("tokens") or 0
        cls = None
        if pr and pr.get("path") == "cache":
            cls = "cache"
        elif cp and cp.get("op") in ("QUERY", "WAIT", "PUBLISH"):
            cls = cp["op"]
        elif pr and pr.get("path") == "compile" and pr.get("op") in ("QUERY", "WAIT", "PUBLISH"):
            cls = pr["op"]                                             # on 모드(막았으면 Claude 답이 없어 판정 보류)
        elif cp:
            totals["none_or_err"] += 1
        if not cls:
            continue
        res[cls]["proposals"] += 1
        st = _read(_pending(f"{rid}.stop.json"))
        if not sp or not st:
            res[cls]["pending"] += 1
            continue
        ok = _agrees(cls, rid, st)
        res[cls]["agree" if ok else "wrong"] += 1
        if ok:
            res[cls]["saved_turn_tokens"] += st.get("tokens") or 0
    for c, v in res.items():
        decided = v["agree"] + v["wrong"]
        v["agree_rate"] = round(v["agree"] / decided, 3) if decided else None
        v["eligible"] = bool(decided >= ELIGIBLE_MIN and v["agree"] / decided >= ELIGIBLE_RATE) if decided else False
    saved = sum(v["saved_turn_tokens"] for v in res.values())
    totals["estimated_net_saving_if_all_on"] = saved - totals["compile_tokens"]
    return {"mode": mode(), "classes": res, "totals": totals,
            "rule": f"켜기 후보 = 제안(판정된 것) >= {ELIGIBLE_MIN} 그리고 일치율 >= {ELIGIBLE_RATE:.0%}"}


def report_text(r: dict) -> str:
    t = r["totals"]
    out = [f"# mba-front 보고 (모드 {r['mode']})", "",
           f"프롬프트 {t['prompts']} · 탈출 {t['escape']} · 컴파일 {t['compiles']}회 {t['compile_tokens']:,} 토큰 "
           f"(NONE/실패 {t['none_or_err']}) · 본 세션 턴 토큰 합 {t['turn_tokens']:,}", "",
           "| 종류 | 제안 | 일치 | 틀림 | 판정 대기 | 일치율 | 켰다면 아꼈을 턴 토큰 | 켜기 후보 |",
           "|---|---|---|---|---|---|---|---|"]
    for c, v in r["classes"].items():
        rate = "-" if v["agree_rate"] is None else f"{v['agree_rate']:.0%}"
        out.append(f"| {c} | {v['proposals']} | {v['agree']} | {v['wrong']} | {v['pending']} | {rate} | "
                   f"{v['saved_turn_tokens']:,} | {'예' if v['eligible'] else '아니오'} |")
    out += ["", f"모두 켰다면 순절약(아꼈을 턴 토큰 - 모든 컴파일 토큰): {t['estimated_net_saving_if_all_on']:,}",
            f"규칙: {r['rule']}. 켜는 법: MBA_FRONT_MODE=on MBA_ENABLE=<후보인 종류만, 쉼표로>"]
    return "\n".join(out)


def _agrees(cls: str, rid: str, st: dict) -> bool:
    final = st.get("final") or ""
    if cls == "cache":
        c = _read(_pending(f"{rid}.cache.json")) or {}
        sim = difflib.SequenceMatcher(None, c.get("answer") or "", final).ratio()
        return bool(st.get("read_only")) and sim >= CACHE_SIM
    cp = _read(_pending(f"{rid}.compile.json")) or {}
    if cls == "QUERY":
        return bool(cp.get("answer")) and cp["answer"] in final
    if cls == "WAIT":
        return "wait" in (st.get("kinds") or [])
    if cls == "PUBLISH":
        return "publish" in (st.get("kinds") or [])
    return False


# ---------------- 설치 ----------------

_OURS = re.compile(r'(mba-front"?|mba\.front\.hook"?)\s+(prompt|stop)\b')


def _command(which: str) -> str:
    exe = shutil.which("mba-front")
    if exe:
        return f'"{exe}" {which}'
    return f'PYTHONPATH="{ROOT}" "{sys.executable}" -m mba.front.hook {which}'


def install(settings=None, remove: bool = False) -> dict:
    """UserPromptSubmit · Stop 에 우리 훅을 **제자리에 하나만**. 남의 훅은 그대로, 바꾸기 전 것은 .bak-mba."""
    p = Path(settings or Path.home() / ".claude" / "settings.json").expanduser()
    text = p.read_text(encoding="utf-8") if p.is_file() else ""
    d = json.loads(text) if text.strip() else {}
    before = json.dumps(d, sort_keys=True)
    hooks = d.setdefault("hooks", {})
    for ev, which, timeout in (("UserPromptSubmit", "prompt", 30), ("Stop", "stop", 30)):
        groups = hooks.setdefault(ev, [])
        entry = {"type": "command", "command": _command(which), "timeout": timeout}
        placed = False
        for g in groups:
            keep = []
            for h in g.get("hooks", []):
                if not _OURS.search(h.get("command", "")):
                    keep.append(h)
                elif not remove and not placed and g.get("matcher") is None:
                    keep.append(dict(entry))
                    placed = True
            g["hooks"] = keep
        groups[:] = [g for g in groups if g.get("hooks")]
        if not remove and not placed:
            groups.append({"hooks": [entry]})
        if not groups:
            del hooks[ev]
    if not hooks:
        del d["hooks"]
    changed = json.dumps(d, sort_keys=True) != before
    if changed:
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.is_file():
            p.with_name(p.name + ".bak-mba").write_text(text, encoding="utf-8")
        p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"settings": str(p), "changed": changed, "installed": not remove}


def purge() -> dict:
    n = 0
    d = home() / "pending"
    if d.is_dir():
        for f in d.iterdir():
            f.unlink()
            n += 1
    return {"removed_pending": n}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mba-front")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prompt")
    sub.add_parser("stop")
    a = sub.add_parser("compile-bg")
    a.add_argument("rid")
    for name in ("install-hook", "uninstall-hook"):
        a = sub.add_parser(name)
        a.add_argument("--settings")
    a = sub.add_parser("report")
    a.add_argument("--text", action="store_true", help="사람이 읽는 표")
    sub.add_parser("purge")
    args = ap.parse_args(argv)
    if args.cmd in ("prompt", "stop"):
        try:                                                    # 막는 쪽으로 틀리지 않는다
            ev = json.loads(sys.stdin.read() or "{}")
            out = on_prompt(ev) if args.cmd == "prompt" else on_stop(ev)
            if out:
                print(json.dumps(out, ensure_ascii=False))
        except Exception as e:                                  # noqa: BLE001
            print(f"mba-front: {type(e).__name__}: {e}", file=sys.stderr)
        return 0
    if args.cmd == "compile-bg":
        compile_bg(args.rid)
        return 0
    if args.cmd in ("install-hook", "uninstall-hook"):
        out = install(args.settings, remove=args.cmd == "uninstall-hook")
    elif args.cmd == "report":
        out = report()
        if args.text:
            print(report_text(out))
            return 0
    else:
        out = purge()
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
