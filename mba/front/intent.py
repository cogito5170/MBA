"""[1] 의도 파서 -- 자연어를 **한 번만** 기호 임무로 컴파일한다.

순서(토큰 절약 우선): 결정적 어휘 파서가 먼저다. 필수 칸(GOAL · TARGET)을 다 채우면 토큰 0 으로 끝난다.
못 채웠을 때만 LLM 을 부르고, 그 출력은 'KEY=VALUE;...' 로 **제약** 된다 -- 어휘 밖의 값(새 기호)은 거부한다.
"""
from __future__ import annotations

import re


VOCAB = {
    "GOAL": {"SEARCH": ["찾아", "찾기", "찾아라", "search", "find"], "RETURN": ["돌아와", "귀환", "return"]},
    "COLOR": {"BLUE": ["파란", "파랑", "blue"], "RED": ["빨간", "빨강", "red"]},
    "OBJECT": {"OBJECT": ["물체", "object", "물건"]},
    "ENV": {"SMOKE": ["연기", "smoke"], "DARK": ["어둠", "어두운", "dark"]},
    "RISK": {"LOW": ["조심", "안전하게", "low risk"], "HIGH": ["빨리", "급히", "high risk"]},
}
ALLOWED = {"GOAL": {"SEARCH", "RETURN"}, "TARGET": {"OBJECT", "BLUE_OBJECT", "RED_OBJECT"},
           "ENV": {"SMOKE", "DARK", "CLEAR"}, "RISK": {"LOW", "HIGH"}}
REQUIRED = ("GOAL", "TARGET")


class IntentError(ValueError):
    """사람에게 되물을 일 -- 코어로 넘기지 않는다."""


def _find(text: str, slot: str):
    t = text.lower()
    hits = [k for k, words in VOCAB[slot].items() if any(w in t for w in words)]
    return hits[0] if len(hits) == 1 else None


def deterministic(text: str) -> dict:
    out = {"ENV": "CLEAR", "RISK": "LOW"}
    for slot in ("GOAL", "ENV", "RISK"):
        v = _find(text, slot)
        if v:
            out[slot] = v
    obj, color = _find(text, "OBJECT"), _find(text, "COLOR")
    if obj:
        out["TARGET"] = f"{color}_{obj}" if color else obj
    return out


_PARTICLE = re.compile(r"(에서|으로|에게|까지|부터|을|를|이|가|은|는|의|로|에|와|과|도|만)$")
_ENDING = re.compile(r"(아라|어라|아줘|어줘|해줘|아|어|해|요|라)$")


def canonical_question(text: str) -> str:
    """질문의 정준형 -- 어순 · 조사 · 어미 · 문장부호 · 대소문자를 지운 낱말 묶음(정렬). 같은 말의 다른 꼴을 한 열쇠로."""
    words = []
    for w in re.findall(r"[0-9A-Za-z가-힣]+", text.lower()):
        w = _PARTICLE.sub("", w)
        w = _ENDING.sub("", w) if len(w) > 1 else w
        if w:
            words.append(w)
    return " ".join(sorted(set(words)))


def canonical_answer(m: dict) -> str:
    return ";".join(f"{k}={m[k]}" for k in sorted(m))


def decode_answer(s: str) -> dict:
    return dict(kv.split("=", 1) for kv in s.split(";") if kv)


def _validate(m: dict) -> dict:
    for k in REQUIRED:
        if k not in m:
            raise IntentError(f"{k} 가 비었다")
    for k, v in m.items():
        if k not in ALLOWED or v not in ALLOWED[k]:
            raise IntentError(f"어휘 밖: {k}={v}")
    return m


def compile_mission(text: str, gate=None) -> tuple:
    """-> (임무 dict, 정준 DSL 문자열, LLM 을 썼나). 결정적 파서가 다 채우면 관문에 가지도 않는다.
    못 채우면 관문(mba.front.gate)에 묻는다: 그 빈칸 꼴에서 LLM 이 맞힐 확률이 문턱을 넘을 때만 부른다."""
    m = deterministic(text)
    used = False
    missing = [k for k in REQUIRED if k not in m]
    if missing:
        if gate is None:
            raise IntentError(f"{','.join(missing)} 가 비었다 -- 관문이 없어 LLM 을 부르지 않는다")
        prompt = ("다음 명령을 KEY=VALUE;... 한 줄로만 바꿔라. 허용 값: "
                  + "; ".join(f"{k}={'|'.join(sorted(v))}" for k, v in ALLOWED.items()) + f"\n명령: {text}")
        base = dict(m)
        before = len(gate.log)
        m = gate.ask("intent:" + ",".join(missing), prompt,
                     lambda raw: _validate({**base, **dict(re.findall(r"([A-Z]+)\s*=\s*([A-Z_]+)", raw))}),
                     qkey=canonical_question(text), canon=canonical_answer, decode=decode_answer)
        used = gate.log[before:] != [] and gate.log[-1][2] in ("ok", "wrong")
    m = _validate(m)
    canon = "MISSION M01 { " + " ".join(f"{k}={m[k]}" for k in ("GOAL", "TARGET", "ENV", "RISK")) + " }"
    return m, canon, used
