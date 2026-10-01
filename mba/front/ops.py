"""닫힌 연산 집합 -- 컴파일된 한 줄(OP=...)을 토큰 0 으로 실행한다.

    OP=QUERY;WHAT=HEAD_SUBJECT          읽기만 -- 현재 HEAD 커밋 제목
    OP=WAIT;PID=<n>;LOG=<path>          PID 로 끝날 때까지 기다리고 로그 끝 줄
    OP=PUBLISH;MESSAGE=<m>;FILES=<a,b>  커밋 -> 검사 -> 밀기. **바깥 동작** -- 허락 목록(MBA_ALLOW_PUBLISH)이 있어야 한다
    OP=NONE                             이 집합으로는 못 한다 -> Claude 로 그대로

연산이 늘수록 앞단이 가로챌 수 있는 프롬프트가 는다. 측정에서 잰 것은 이 넷뿐이다.
"""
from __future__ import annotations

import fnmatch
import hashlib
import os
import subprocess
import time
from pathlib import Path

OUTWARD = {"PUBLISH"}
NEED = {"WAIT": ("PID", "LOG"), "PUBLISH": ("MESSAGE", "FILES"), "QUERY": ("WHAT",), "NONE": ()}


class OpError(ValueError):
    pass


def parse_op(text: str) -> dict:
    line = next((x.strip().strip("`") for x in (text or "").splitlines() if x.strip().strip("`").startswith("OP=")), None)
    if not line:
        raise OpError(f"OP 줄이 없다: {(text or '')[:120]!r}")
    kv = {k.strip(): v.strip() for k, v in (p.split("=", 1) for p in line.split(";") if "=" in p)}
    op = kv.get("OP")
    if op not in NEED or any(k not in kv for k in NEED[op]):
        raise OpError(f"연산 형식이 아니다: {line!r}")
    if op == "QUERY" and kv["WHAT"] != "HEAD_SUBJECT":
        raise OpError(f"모르는 QUERY: {kv['WHAT']}")
    if op == "WAIT" and not kv["PID"].isdigit():
        raise OpError(f"PID 가 정수가 아니다: {kv['PID']}")
    return kv


def _git(cwd, *a) -> "str | None":
    try:
        r = subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def state_hash(cwd) -> "str | None":
    """저장소 상태(HEAD · 브랜치 · 원격 추적 · 작업 트리 변경)의 해시. git 이 아니면 None -- 그때는 캐시를 쓰지 않는다."""
    head = _git(cwd, "rev-parse", "HEAD")
    if head is None:
        return None
    parts = [head, _git(cwd, "rev-parse", "--abbrev-ref", "HEAD") or "",
             _git(cwd, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}") or "",
             _git(cwd, "status", "--porcelain") or ""]
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()[:16]


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0] != "Z"
    except OSError:
        return True


def publish_allowed(cwd) -> bool:
    pats = [p for p in os.environ.get("MBA_ALLOW_PUBLISH", "").split(":") if p]
    real = str(Path(cwd).resolve())
    return any(fnmatch.fnmatch(real, p) for p in pats)


def execute(op: dict, cwd, timeout: float = 600) -> str:
    kind = op["OP"]
    if kind == "QUERY":
        s = _git(cwd, "log", "-1", "--format=%s")
        if s is None:
            raise OpError("git 저장소가 아니다")
        return s.strip()
    if kind == "WAIT":
        pid, t0 = int(op["PID"]), time.time()
        while _alive(pid) and time.time() - t0 < timeout:
            time.sleep(0.5)
        if _alive(pid):
            raise OpError(f"시간 초과: PID {pid} 가 아직 돈다")
        log = Path(op["LOG"])
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines() if log.is_file() else []
        return lines[-1] if lines else ""
    if kind == "PUBLISH":
        if not publish_allowed(cwd):
            raise OpError("발행은 허락 목록(MBA_ALLOW_PUBLISH)에 든 저장소에서만")
        try:
            from walp import execpolicy                                  # 있으면 검증된 발행기를 쓴다
        except ImportError as e:
            raise OpError("발행기(walp.execpolicy)가 없다") from e
        files = [f.strip() for f in op["FILES"].split(",") if f.strip()]
        out = execpolicy.publish(cwd, op["MESSAGE"].strip().strip("'\""), files=files, push=True)
        if not out["ok"]:
            raise OpError(f"발행 멈춤: {out['stopped_at']}")
        return "발행했다: " + ", ".join(out["done"])
    raise OpError(f"실행할 수 없는 연산: {kind}")
