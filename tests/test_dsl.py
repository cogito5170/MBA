"""컴파일러가 거부해야 하는 것을 실제로 거부하는가."""
import unittest

from mba.ir import dsl
from tests.helpers import EXAMPLE

SRC = EXAMPLE.read_text(encoding="utf-8")


class Reject(unittest.TestCase):
    def bad(self, old, new, msg):
        self.assertIn(old, SRC)
        with self.assertRaisesRegex(dsl.DslError, msg):
            dsl.parse(SRC.replace(old, new, 1))

    def test_실례는_통과한다(self):
        m = dsl.parse(SRC)
        self.assertEqual(m.order, ["B10", "B20", "B25", "B30", "B40"])
        self.assertEqual(m.warnings, [])

    def test_맨_아래_층_전체성(self):
        self.bad("RULE R40 WHEN TRUE                         DO A09;",
                 "RULE R40 WHEN S15                          DO A09;", "전체성")

    def test_한_층_안의_겹침(self):
        self.bad("RULE R22 WHEN !S42 & S41 & !S16 & S23      DO A06;",
                 "RULE R22 WHEN S41 & !S16 & S23             DO A06;", "함께 참")

    def test_기호_타입(self):
        self.bad("RULE R10 WHEN S20  DO A01;", "RULE R10 WHEN S20  DO S20;", "A## 또는 PASS")

    def test_미선언_기호(self):
        self.bad("RULE R10 WHEN S20  DO A01;", "RULE R10 WHEN S99  DO A01;", "선언 안 된")

    def test_원뿔_밖_예측(self):
        self.bad("PREDICT F04: S22;", "PREDICT F04: S14;", "원뿔 밖")

    def test_마지막_안전_대체는_막힐_수_없다(self):
        self.bad("VETO A13           WHEN !S42 REASON NO_EVIDENCE;",
                 "VETO A13, A00      WHEN !S42 REASON NO_EVIDENCE;", "VETO 대상")

    def test_재구성_예산_없이_RECONFIG(self):
        self.bad("BUDGET RECONFIG TOKENS 2000;", "", "BUDGET RECONFIG")


if __name__ == "__main__":
    unittest.main()


class AgentModel(unittest.TestCase):
    def test_에이전트_토큰_모형이_컴파일되고_τ_를_센서에서_읽는다(self):
        from mba.core.engine import Engine
        from tests.helpers import ROOT
        m = dsl.load(ROOT / "examples" / "agent_tokens.mba")
        e = Engine(m, {})
        base = {"repo.state_known": 1, "repo.state_same": 1, "prompt.repeat": 0, "tool.dup": 0,
                "tool.poll_streak": 0, "proc.alive": 0, "repo.commit_unpublished": 0,
                "llm.p_correct": 0.9, "policy.publish_allowed": 0, "ctx.tokens": 419000}
        r = e.cycle(base)
        self.assertEqual((r["action"], r["tau"]), ("A00", 419000))           # 보통 턴 = 그 맥락
        r = e.cycle({**base, "prompt.repeat": 1})
        self.assertEqual((r["action"], r["tau"]), ("A01", 0))                # 같은 말 · 같은 상태 -> 재사용
        r = e.cycle({**base, "llm.p_correct": 0.8})
        self.assertEqual(r["action"], "A05")                                 # p == θ -> 부르지 않고 사람에게
