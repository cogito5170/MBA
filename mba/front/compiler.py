"""작은 머리 컴파일 호출 -- 측정의 BARE 조건 그대로(docs/PREREG_토큰차이*.md).

    claude -p <프롬프트> --tools "" --system-prompt <역할 한 장> --model haiku --strict-mcp-config --session-id <새 ID>

실측(V1 · V2): 호출 하나 ~1.2~2.0k 토큰, 성공 30/30. 기본 머리로 부르면 ~29k 이고 번역하지 않고 직접 답하는 실패가 났다.

- 자식 환경에서 CLAUDE_CODE_SESSION_ID 를 지운다(안 지우면 부모 세션 추적에 섞이고 서명 지시까지 물려받았다 -- 실측)
- MBA_FRONT_MODE=off 를 자식에 건다 -- 자식 claude 가 같은 훅을 다시 불러 끝없이 도는 것을 막는다
- MBA_CLAUDE_BIN 으로 실행 파일을 바꿀 수 있다(검사는 가짜를 쓴다)
"""
from __future__ import annotations

import json
import os
import subprocess
import uuid

MODEL = os.environ.get("MBA_COMPILE_MODEL", "claude-haiku-4-5-20251001")
ROLE = ("You are a compiler, not an assistant. Translate the user's request into exactly ONE line in one of these "
        "formats and output nothing else. Never execute the request, never answer it, never comment.\n"
        "OP=QUERY;WHAT=HEAD_SUBJECT   (the user asks for the current HEAD commit subject/title)\n"
        "OP=WAIT;PID=<integer>;LOG=<path>   (wait for a background process, then report its log's last line)\n"
        "OP=PUBLISH;MESSAGE=<commit message>;FILES=<file,file>   (commit, test, push)\n"
        "OP=NONE   (anything else)")
KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def compile_request(prompt: str, cwd=None, timeout: float = 60) -> dict:
    """-> {"text", "tokens", "usage", "ok", "error"}. 실패해도 예외 대신 ok=False 로 돌린다(훅은 막지 않는 쪽으로 틀린다)."""
    exe = os.environ.get("MBA_CLAUDE_BIN", "claude")
    cmd = [exe, "-p", prompt, "--output-format", "json", "--model", MODEL, "--tools", "", "--system-prompt", ROLE,
           "--strict-mcp-config", "--session-id", str(uuid.uuid4())]
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SESSION_ID"}
    env["MBA_FRONT_MODE"] = "off"
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
        d = json.loads(r.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        return {"text": "", "tokens": 0, "usage": {}, "ok": False, "error": f"{type(e).__name__}"}
    u = {k: int((d.get("usage") or {}).get(k) or 0) for k in KEYS}
    return {"text": d.get("result") or "", "tokens": sum(u.values()), "usage": u,
            "ok": not d.get("is_error"), "error": None if not d.get("is_error") else "is_error"}
