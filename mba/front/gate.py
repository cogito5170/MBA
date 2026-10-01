"""LLM 관문 -- 의도 파서 · 모형 작성 · 임무 재구성이 LLM 을 부르는 **유일한 문**.

정답까지 가장 짧은 길(사용자 결정 2026-10-01) -- 싼 것부터, LLM 은 마지막:

    0. 같은 질문(정준형)의 답이 있다                       -> 그 답 (토큰 0)
    1. 같은 답으로 확인된 동치류에 이 질문이 들어 있다       -> 그 답 (토큰 0)
    2. LLM 이 이 자리에서 맞힐 확률 p 가 문턱 θ 를 넘는다    -> 부른다. 출력을 결정적으로 검증하고 정준형으로 남긴다
       p <= θ (같아도)                                     -> 부르지 않는다. Declined -> 사람에게

같은 답이 다른 식으로 나오는 것을 잡는다: 질문도 답도 **정준형** 으로 바꿔 비교한다. 서로 다른 질문 꼴이
같은 정준 답으로 검증되면 한 동치류로 묶는다. 동치류의 답을 다시 쓰는 것은 서로 다른 질문 **agree_min 개 이상**
(기본 2)이 같은 답으로 검증된 뒤에만 -- 하나뿐이면 우연을 동치로 읽는다(WALP 답 캐시가 헷갈리는 짝에
남의 답을 6/43 낸 것과 같은 병).
"""
from __future__ import annotations

from mba.core.boundary import should_call_llm
from mba.core.confidence import Confidence
from mba.front import llm


class Declined(RuntimeError):
    """LLM 이 맞힐 확률이 문턱을 못 넘었다 -- 토큰을 쓰지 않았다."""


class Gate:
    def __init__(self, conf: "Confidence | None" = None, threshold: float = 0.8, store=None,
                 answers: "dict | None" = None, agree_min: int = 2):
        self.conf = conf or Confidence()
        self.threshold = threshold
        self.store = store                          # Blackboard 면 확률 기록 · 답을 거기 남긴다
        self.answers = answers or {"by_q": {}, "by_a": {}, "classes": {}}
        self.agree_min = agree_min
        self.log = []

    @classmethod
    def on(cls, board, threshold: float = 0.8, agree_min: int = 2) -> "Gate":
        return cls(board.confidence(), threshold, board, board.answers(), agree_min)

    def lookup(self, qkey: str):
        """토큰 0 으로 답할 수 있으면 (정준 답, 길) 아니면 None."""
        a = self.answers["by_q"].get(qkey)
        if a is not None:
            return a, "cache"
        for ans, members in self.answers["classes"].items():
            if qkey in members:
                return ans, "class"
        return None

    def ask(self, key: str, prompt: str, validate, qkey: "str | None" = None, canon=None, decode=None):
        """validate(text) -> 값(틀리면 예외). canon(값) -> 정준 답 문자열, decode(정준 답) -> 값.
        qkey 를 주면 0 · 1 단계(캐시 · 동치류)를 먼저 본다."""
        if qkey is not None and canon is not None:
            hit = self.lookup(qkey)
            if hit:
                self.log.append((key, None, hit[1]))
                return decode(hit[0]) if decode else hit[0]
        p = self.conf.p(key)
        if not should_call_llm(p, self.threshold):
            self.log.append((key, round(p, 4), "declined"))
            raise Declined(f"{key}: LLM 정답 확률 {p:.3f} <= 문턱 {self.threshold} -- 부르지 않는다")
        text = llm.call(prompt + "\n설명 없이 답만, 가장 짧게.", key.split(":", 1)[0])
        try:
            value = validate(text)
        except Exception:
            self._record(key, False, p)
            raise
        self._record(key, True, p)
        if qkey is not None and canon is not None:
            self._remember(qkey, canon(value))
        return value

    def _remember(self, qkey: str, ans: str):
        self.answers["by_q"][qkey] = ans
        members = self.answers["by_a"].setdefault(ans, [])
        if qkey not in members:
            members.append(qkey)
        if len(members) >= self.agree_min:                      # 다른 꼴 · 같은 답 -- 동치류 확인
            self.answers["classes"][ans] = sorted(members)
        if self.store is not None:
            self.store.save_answers(self.answers)

    def _record(self, key, ok, p):
        self.conf.record(key, ok)
        self.log.append((key, round(p, 4), "ok" if ok else "wrong"))
        if self.store is not None:
            self.store.save_confidence(self.conf)
