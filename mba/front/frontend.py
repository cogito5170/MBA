"""MBA-frontend -- 잰 그대로의 MBA 앞단(`mba-front` 의 MBA-1 설정)을 한 줄로 켠다.

    mba-frontend install-hook [--settings 경로]     Claude Code 의 UserPromptSubmit · Stop 에 건다(mba-front 훅이 있으면 이것으로 바꾼다)
    mba-frontend uninstall-hook [--settings 경로]
    mba-frontend report [--text] · purge            mba-front 와 같다

설정은 비교(`docs/MBA-frontend.md`)에서 잰 MBA-1 과 같다:
    MBA_FRONT_MODE=on   MBA_ENABLE=cache,QUERY   컴파일 사전 (9, 1) -> p 0.9 > θ 0.8

- 환경에 이미 값이 있으면 그것을 쓴다(덮지 않는다). 그때는 잰 설정이 아니다.
- 사전은 블랙보드에 **아직 없을 때만** 넣는다 -- 쌓인 기록 · 사람이 정한 사전은 덮지 않는다.
- WAIT · PUBLISH 는 켜지 않는다 -- 비교에서 잰 종류가 아니다(PUBLISH 는 바깥 동작이기도 하다).
- 나머지(캐시 · 관문 · 컴파일 · 막는 쪽으로 안 틀리기)는 `mba.front.hook` 그대로다.
"""
from __future__ import annotations

import os
import shutil
import sys

from mba.front import hook

PRESET = {"MBA_FRONT_MODE": "on", "MBA_ENABLE": "cache,QUERY"}
COMPILE_PRIOR = (9, 1)


def apply_preset() -> dict:
    """잰 설정을 건다. -> 실제로 쓰게 된 값(환경이 이미 정한 것은 그대로)."""
    for k, v in PRESET.items():
        os.environ.setdefault(k, v)
    if os.environ.get("MBA_FRONT_MODE") == "on":
        b = hook._board()
        c = b.confidence()
        if "compile" not in c.priors:
            c.priors["compile"] = COMPILE_PRIOR
            b.save_confidence(c)
    return {k: os.environ.get(k) for k in PRESET}


def _command(which: str) -> str:
    exe = shutil.which("mba-frontend")
    if exe:
        return f'"{exe}" {which}'
    return f'PYTHONPATH="{hook.ROOT}" "{sys.executable}" -m mba.front.frontend {which}'


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else ""
    if cmd in ("install-hook", "uninstall-hook"):
        settings = argv[argv.index("--settings") + 1] if "--settings" in argv else None
        import json
        print(json.dumps(hook.install(settings, remove=cmd == "uninstall-hook", command=_command), ensure_ascii=False, indent=1))
        return 0
    if cmd in ("prompt", "stop"):
        try:                                                    # 막는 쪽으로 틀리지 않는다 -- 사전 쓰기가 터져도 훅은 돈다
            apply_preset()
        except Exception as e:                                  # noqa: BLE001
            print(f"mba-frontend: {type(e).__name__}: {e}", file=sys.stderr)
    return hook.main(argv)


if __name__ == "__main__":
    sys.exit(main())
