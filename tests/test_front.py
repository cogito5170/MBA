"""토큰 경계 바깥: 관문(정답 확률 · 최단 경로 · 같은 답 다른 꼴) · 의도 파서 · 블랙보드 · 재구성."""
import json
import tempfile
import unittest
from pathlib import Path

from mba.core.confidence import Confidence
from mba.front import blackboard, intent, llm, mission
from mba.front.gate import Declined, Gate
from tests.helpers import EXAMPLE

SRC = EXAMPLE.read_text(encoding="utf-8")


class Fake:
    def __init__(self, text="", tokens=500):
        self.text, self.tokens, self.calls = text, tokens, []

    def __call__(self, prompt, purpose):
        self.calls.append(purpose)
        return (self.text(prompt) if callable(self.text) else self.text), self.tokens


def trusting(**kw):
    """사전을 높게 둔 관문(시험용) -- p = 0.9 > θ 0.8."""
    return Gate(Confidence({"intent": (9, 1), "model": (9, 1), "reconfig": (9, 1)}), 0.8, **kw)


class Base(unittest.TestCase):
    def setUp(self):
        llm.reset()

    def tearDown(self):
        llm.set_backend(None)


class Trigger(Base):
    def test_정답_확률이_문턱을_못_넘으면_부르지_않는다(self):
        f = Fake("x")
        llm.set_backend(f)
        g = Gate(Confidence({"intent": (8, 2)}), 0.8)                # p = 0.8 == θ -> 아낀다
        with self.assertRaises(Declined):
            g.ask("intent:GOAL", "q", lambda t: t)
        self.assertEqual(f.calls, [])

    def test_틀린_답은_확률을_내린다(self):
        llm.set_backend(Fake("틀림"))
        g = Gate(Confidence({"intent": (9, 1)}), 0.8)
        before = g.conf.p("intent:GOAL")

        def bad(t):
            raise ValueError("검증 실패")
        with self.assertRaises(ValueError):
            g.ask("intent:GOAL", "q", bad)
        self.assertLess(g.conf.p("intent:GOAL"), before)
        for _ in range(3):
            try:
                g.ask("intent:GOAL", "q", bad)
            except (ValueError, Declined):
                pass
        with self.assertRaises(Declined):                            # 내려간 뒤에는 토큰을 안 쓴다
            g.ask("intent:GOAL", "q", bad)


class ShortestPath(Base):
    def test_같은_말의_다른_꼴은_한_열쇠(self):
        a = intent.canonical_question("저기 그거 좀 봐줘")
        self.assertEqual(a, intent.canonical_question("그거 좀 저기 봐줘!"))
        self.assertNotEqual(a, intent.canonical_question("저기 저거 좀 봐줘"))

    def test_결정적_파서가_되면_관문에_안_간다(self):
        f = Fake()
        llm.set_backend(f)
        m, canon, used = intent.compile_mission("연기 속에서 파란 물체를 찾아.", trusting())
        self.assertEqual(canon, "MISSION M01 { GOAL=SEARCH TARGET=BLUE_OBJECT ENV=SMOKE RISK=LOW }")
        self.assertEqual((used, f.calls), (False, []))

    def test_같은_질문은_두_번째부터_토큰_0(self):
        f = Fake("GOAL=SEARCH;TARGET=RED_OBJECT", 120)
        llm.set_backend(f)
        g = trusting()
        m1, _, u1 = intent.compile_mission("저기 그거 좀 봐줘", g)
        m2, _, u2 = intent.compile_mission("그거 좀 저기 봐줘", g)
        self.assertEqual((m1, u1, u2), (m2, True, False))
        self.assertEqual(f.calls, ["intent"])
        self.assertEqual(g.log[-1][2], "cache")

    def test_다른_꼴_같은_답은_동치류로_묶이고_그_뒤로_토큰_0(self):
        f = Fake("GOAL=SEARCH;TARGET=RED_OBJECT", 120)
        llm.set_backend(f)
        g = trusting()
        intent.compile_mission("저기 그거 좀 봐줘", g)
        intent.compile_mission("거기 그것 확인", g)                  # 다른 꼴 -- LLM 이 같은 답을 낸다
        self.assertEqual(len(f.calls), 2)
        cls = g.answers["classes"]
        self.assertEqual(list(cls), [intent.canonical_answer({"ENV": "CLEAR", "GOAL": "SEARCH", "RISK": "LOW",
                                                              "TARGET": "RED_OBJECT"})])
        self.assertEqual(len(next(iter(cls.values()))), 2)

    def test_한_번만_같은_답이면_동치로_읽지_않는다(self):
        f = Fake("GOAL=SEARCH;TARGET=RED_OBJECT", 120)
        llm.set_backend(f)
        g = trusting()
        intent.compile_mission("저기 그거 좀 봐줘", g)
        self.assertEqual(g.answers["classes"], {})

    def test_가장_짧게_답하라고_묻는다(self):
        seen = []
        llm.set_backend(Fake(lambda p: seen.append(p) or "GOAL=SEARCH;TARGET=OBJECT"))
        intent.compile_mission("저기 그거 좀 봐줘", trusting())
        self.assertIn("가장 짧게", seen[0])

    def test_관문이_없으면_부르지_않는다(self):
        with self.assertRaises(intent.IntentError):
            intent.compile_mission("저기 그거 좀 봐줘")
        self.assertEqual(llm.CALLS["n"], 0)


class Board(Base):
    def setUp(self):
        super().setUp()
        self.path = Path(tempfile.mkdtemp()) / "bb.json"

    def test_LLM_작성은_한_번_뒤로는_칸에서(self):
        f = Fake(SRC, 3000)
        llm.set_backend(f)
        bb = blackboard.Blackboard(self.path)
        g = trusting()
        m1 = bb.model("rover/smoke", g)
        m2 = bb.model("rover/smoke", g)
        m3 = blackboard.Blackboard(self.path).model("rover/smoke", g)
        self.assertEqual(f.calls, ["model"])
        self.assertEqual({m1.ir_hash, m2.ir_hash, m3.ir_hash}, {m1.ir_hash})
        self.assertEqual(blackboard.Blackboard(self.path).amortized_tokens(), 3000)

    def test_실패해도_다시_부르지_않는다(self):
        f = Fake("이건 DSL 이 아니다 {", 800)
        llm.set_backend(f)
        g = trusting()
        with self.assertRaises(blackboard.BlackboardError):
            blackboard.Blackboard(self.path).model("rover/smoke", g)
        with self.assertRaisesRegex(blackboard.BlackboardError, "이미 한 번"):
            blackboard.Blackboard(self.path).model("rover/smoke", g)
        self.assertEqual(f.calls, ["model"])
        self.assertEqual(json.loads(self.path.read_text())["models"]["rover/smoke"]["state"], "failed")

    def test_확률이_낮으면_기회를_쓰지_않는다(self):
        f = Fake(SRC)
        llm.set_backend(f)
        bb = blackboard.Blackboard(self.path)
        with self.assertRaises(Declined):
            bb.model("rover/smoke", Gate(threshold=0.8))            # 사전 0.5
        self.assertNotIn("rover/smoke", json.loads(self.path.read_text() if self.path.exists() else '{"models":{}}')["models"])
        bb.model("rover/smoke", trusting())                          # 확률이 오르면 그때 한 번
        self.assertEqual(f.calls, ["model"])

    def test_작성_중_터져도_시도는_남는다(self):
        def boom(prompt, purpose):
            raise KeyboardInterrupt
        llm.set_backend(boom)
        with self.assertRaises(KeyboardInterrupt):
            blackboard.Blackboard(self.path).model("k", trusting())
        llm.set_backend(Fake(SRC))
        with self.assertRaises(blackboard.BlackboardError):
            blackboard.Blackboard(self.path).model("k", trusting())

    def test_확률_기록과_답은_블랙보드에_남아_이어진다(self):
        llm.set_backend(Fake("GOAL=SEARCH;TARGET=OBJECT"))
        bb = blackboard.Blackboard(self.path)
        g = Gate.on(bb)
        g.conf.priors["intent"] = (9, 1)
        intent.compile_mission("저기 그거 좀 봐줘", g)
        g2 = Gate.on(blackboard.Blackboard(self.path))
        self.assertEqual(g2.conf.counts, {"intent:GOAL,TARGET": [1, 0]})
        self.assertEqual(g2.lookup(intent.canonical_question("그거 저기 좀 봐줘"))[1], "cache")

    def test_사람이_넣은_칸은_토큰_0(self):
        bb = blackboard.Blackboard(self.path)
        bb.put("rover/smoke", SRC)
        self.assertEqual(bb.model("rover/smoke", Gate()).order[-1], "B40")
        self.assertEqual(llm.CALLS["n"], 0)


class Reconfig(Base):
    def test_재구성도_관문을_지난다(self):
        f = Fake("GOAL=SEARCH;TARGET=OBJECT")
        llm.set_backend(f)
        r = mission.reconfigure({"GOAL": "SEARCH"}, ["C01"], Gate(threshold=0.8), allow=True, used=0)
        self.assertFalse(r["spent"])
        r = mission.reconfigure({"GOAL": "SEARCH"}, ["C01"], trusting(), allow=True, used=0)
        self.assertTrue(r["spent"])
        r = mission.reconfigure({"GOAL": "SEARCH"}, ["C01"], trusting(), allow=True, used=1)
        self.assertFalse(r["spent"])
        self.assertEqual(f.calls, ["reconfig"])


if __name__ == "__main__":
    unittest.main()
