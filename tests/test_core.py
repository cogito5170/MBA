"""코어: 토큰 경계 중재(D2) · 증분 진단(원뿔) · 다중 고장(D4) · 믿음 캐시 · 결정성."""
import unittest

from mba.core import boundary
from mba.core.diagnosis import diagnose
from mba.core.engine import Engine
from tests.helpers import MISSION, model, obs, scenario


def run(seq, mission=MISSION):
    e = Engine(model(), mission)
    return e, [e.cycle(r) for r in seq]


class TokenBoundary(unittest.TestCase):
    def test_확정_물체_앞에서_배터리_문턱이면_돌아서지_않고_선언한다(self):
        e, recs = run(scenario())
        last = recs[-1]
        self.assertIn("S42", last["bits"])
        self.assertIn("S20", last["bits"])
        self.assertEqual(last["action"], "A13")                       # DECLARE
        self.assertIn(("B10", "R10", "A01"), last["proposals"])       # RETURN 도 나왔지만
        self.assertEqual(e.tau("A01", set(last["bits"])), 2000)       # 확정을 버리면 재구성 토큰
        self.assertEqual(last["tau"], 0)

    def test_확정이_없으면_우선순위대로_귀환한다(self):
        e, recs = run([obs(battery__margin_to_home=-1.0)])
        self.assertEqual(recs[-1]["action"], "A01")
        self.assertEqual(recs[-1]["tau"], 0)

    def test_같은_τ_면_선언된_우선순위(self):
        ps = [(3, "R30", "A04"), (1, "R22", "A06"), (4, "R40", "A09")]
        self.assertEqual([p[2] for p in boundary.order(ps, lambda a: 0)], ["A06", "A04", "A09"])
        self.assertEqual(boundary.order(ps, lambda a: 5 if a == "A06" else 0)[0][2], "A04")

    def test_정답_확률이_문턱과_정확히_같으면_부르지_않는다(self):
        self.assertFalse(boundary.should_call_llm(0.7, 0.7))
        self.assertTrue(boundary.should_call_llm(0.70001, 0.7))
        self.assertFalse(boundary.should_call_llm(0.6, 0.7))

    def test_안전층은_토큰_경계_밖이다(self):
        # 확정 + 배터리 문턱 + 바로 앞 위험: τ 로 고른 선언은 VETO 대상이 아니라 나가고,
        # 확정이 없으면 귀환(A01)도 HAZARD 로 막혀 FALLBACK(A00) 으로 간다
        _, recs = run([obs(battery__margin_to_home=-1.0, radar__obstacle_m=0.2)])
        self.assertEqual(recs[-1]["action"], "A00")
        self.assertIn(("A01", "HAZARD"), recs[-1]["vetoed"])


class Diagnosis(unittest.TestCase):
    def test_CAMERA_BAD_는_카메라_원뿔만_깨운다(self):
        e, recs = run(scenario()[:6])
        r = next(x for x in recs if "E07" in x["events"])
        self.assertEqual(r["diag"]["cone"], ["C01", "C05", "C06", "C07", "C08"])
        self.assertNotIn("C02", r["diag"]["cone"])
        self.assertEqual(r["modes"], {"C08": "F04"})                # 연기(환경), 렌즈는 버금 후보
        self.assertEqual(r["diag"]["ambiguous"], 1)
        self.assertEqual(r["action"], "A12")                         # 판별 관측

    def test_사건이_없으면_진단이_돌지_않는다(self):
        e, _ = run([obs()] * 10)
        self.assertEqual(e.diag_runs, 1)                             # 첫 주기의 초기 진단 한 번뿐

    def test_같은_원뿔_같은_관측이면_캐시에서(self):
        low = obs(camera__contrast=0.1)
        e, recs = run([obs()] + [low] * 3 + [obs()] + [low] * 3)
        diags = [r["diag"] for r in recs if r["diag"]]
        self.assertIn("hit", [d["cache"] for d in diags])

    def test_두_고장도_코어_안에서_푼다(self):
        m = model()
        cone = m.cone("C01")
        r = diagnose(m, cone, {"S11", "S12", "S13"})                 # 버스 저전압 + 데이터 오류 동시
        self.assertFalse(r["unknown"])
        self.assertEqual(r["k"], 2)
        self.assertEqual({c: v for c, v in r["modes"].items() if v != "NOMINAL"}, {"C05": "F05", "C06": "F02"})

    def test_예산을_넘으면_UNKNOWN(self):
        m = model()
        m.budget["CANDIDATES"] = 3
        r = diagnose(m, m.cone("C01"), {"S11", "S12", "S13"})
        self.assertTrue(r["unknown"])
        self.assertLessEqual(r["examined"], 3)


class Determinism(unittest.TestCase):
    def test_같은_입력이면_같은_행동_열(self):
        a, _ = run(scenario() * 3)
        b, _ = run(scenario() * 3)
        self.assertEqual(a.action_hash(), b.action_hash())
        self.assertEqual([r["action"] for r in a.trace][:9],
                         ["A09", "A09", "A09", "A09", "A12", "A04", "A06", "A06", "A13"])


if __name__ == "__main__":
    unittest.main()
