"""모형 작성자 = 블랙보드(사용자 결정 2026-10-01): LLM 이 모형(.mba)을 쓰는 것은 **열쇠마다 한 번뿐** 이다.

- 블랙보드는 파일 하나(JSON)다. 열쇠(예: 플랫폼 · 영역 이름)마다 칸 하나.
- 칸이 차 있으면 거기서 읽는다(토큰 0). 비어 있으면 작성자를 **한 번** 부르고, 부르기 **전에** '시도함' 을 적는다
  -- 그래서 작성 중 터져도 다시 부르지 않는다.
- 쓴 것은 컴파일러를 지나야 칸에 들어간다. 못 지나면 실패로 적고, 다시 부르지 않는다 -> 사람에게.
- 쓴 토큰은 칸에 남는다(절약 측정에 상각할 몫).
- 부르기 전에 관문(mba.front.gate)을 지난다: LLM 이 이 모형을 맞게 쓸 확률이 문턱을 못 넘으면 부르지 않고,
  시도로도 적지 않는다(한 번의 기회를 쓰지 않은 것).
- LLM 정답 확률의 기록(mba.core.confidence)도 여기 남는다.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from mba.core.confidence import Confidence
from mba.front import llm
from mba.ir import dsl


class BlackboardError(RuntimeError):
    pass


class Blackboard:
    def __init__(self, path):
        self.path = Path(path)
        self.d = json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else {"models": {}}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.d, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def put(self, key: str, text: str, author: str = "human") -> dsl.Model:
        """사람이 쓴 모형을 칸에 넣는다(LLM 안 씀). 이미 찬 칸은 덮지 않는다."""
        if key in self.d["models"]:
            raise BlackboardError(f"{key}: 이미 찬 칸이다 -- 덮어쓰지 않는다")
        model = dsl.parse(text)
        self.d["models"][key] = {"state": "ok", "author": author, "text": text, "tokens": 0,
                                 "sha": hashlib.sha256(text.encode()).hexdigest()[:16]}
        self._save()
        return model

    def confidence(self) -> Confidence:
        return Confidence.from_dict(self.d.get("confidence"))

    def answers(self) -> dict:
        return self.d.get("answers") or {"by_q": {}, "by_a": {}, "classes": {}}

    def save_answers(self, answers: dict) -> None:
        self.d["answers"] = answers
        self._save()

    def save_confidence(self, conf: Confidence) -> None:
        self.d["confidence"] = conf.to_dict()
        self._save()

    def model(self, key: str, gate, brief: str = "") -> dsl.Model:
        slot = self.d["models"].get(key)
        if slot and slot["state"] == "ok":
            return dsl.parse(slot["text"])
        if slot:
            raise BlackboardError(f"{key}: LLM 작성은 이미 한 번 썼다({slot['state']}) -- 사람에게 넘긴다")
        from mba.core.boundary import should_call_llm
        p = gate.conf.p("model:" + key)
        if not should_call_llm(p, gate.threshold):              # 기회를 쓰지 않는다 -- 시도로 적지 않는다
            from mba.front.gate import Declined
            raise Declined(f"model:{key}: LLM 정답 확률 {p:.3f} <= 문턱 {gate.threshold} -- 사람이 모형을 넣어야 한다")
        self.d["models"][key] = {"state": "attempted", "author": "llm"}
        self._save()                                            # 부르기 전에 적는다
        before = llm.CALLS["tokens"]
        try:
            text, model = gate.ask("model:" + key, f"MBA DSL 로 모형을 써라. 열쇠: {key}\n{brief}",
                                   lambda t: (t, dsl.parse(t)))
        except Exception as e:
            self.d["models"][key].update(state="failed", error=str(e)[:300], tokens=llm.CALLS["tokens"] - before)
            self._save()
            raise BlackboardError(f"{key}: 모형 작성 실패 -- 다시 부르지 않는다: {e}") from e
        self.d["models"][key].update(state="ok", text=text, tokens=llm.CALLS["tokens"] - before,
                                     sha=hashlib.sha256(text.encode()).hexdigest()[:16])
        self._save()
        return model

    def amortized_tokens(self) -> int:
        return sum(s.get("tokens", 0) for s in self.d["models"].values())
