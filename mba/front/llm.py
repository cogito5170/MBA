"""LLM 이 불리는 **유일한** 자리. 부를 때마다 계수기가 오른다 -- T0 시험이 코어 주기 전후로 이 계수기를 잰다.

백엔드는 일부러 비어 있다(망 코드가 이 저장소에 없다). 쓰는 쪽이 set_backend 로 꽂는다:
    backend(prompt: str, purpose: str) -> (text: str, tokens: int)
"""
from __future__ import annotations

CALLS = {"n": 0, "tokens": 0, "by_purpose": {}}
_backend = None


class NoBackend(RuntimeError):
    pass


def set_backend(fn) -> None:
    global _backend
    _backend = fn


def call(prompt: str, purpose: str) -> str:
    if _backend is None:
        raise NoBackend(f"LLM 백엔드가 없다({purpose}) -- 토큰을 쓰지 않고 멈춘다")
    text, tokens = _backend(prompt, purpose)
    CALLS["n"] += 1
    CALLS["tokens"] += int(tokens)
    CALLS["by_purpose"][purpose] = CALLS["by_purpose"].get(purpose, 0) + 1
    return text


def reset() -> None:
    CALLS.update(n=0, tokens=0, by_purpose={})
