"""MBA-frontend -- 잰 설정(MBA-1: on · cache,QUERY · 사전 (9,1))을 그대로 켜는가. 진짜 claude 는 부르지 않는다."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

from mba.front import frontend, hook
from mba.front.blackboard import Blackboard
from tests.helpers import ROOT
from tests.test_hook import SUBJECT, Base, fake_claude


class Preset(Base):
    def board(self):
        return Blackboard(self.d / "home" / "blackboard.json")

    def test_잰_설정을_건다(self):
        self.assertEqual(frontend.apply_preset(), {"MBA_FRONT_MODE": "on", "MBA_ENABLE": "cache,QUERY"})
        self.assertEqual(tuple(self.board().confidence().priors["compile"]), (9, 1))

    def test_사람이_정한_값과_사전은_덮지_않는다(self):
        b = self.board(); c = b.confidence(); c.priors["compile"] = (1, 1); b.save_confidence(c)
        os.environ["MBA_ENABLE"] = "cache"
        self.assertEqual(frontend.apply_preset()["MBA_ENABLE"], "cache")
        self.assertEqual(tuple(self.board().confidence().priors["compile"]), (1, 1))

    def test_off_면_사전도_안_쓴다(self):
        os.environ["MBA_FRONT_MODE"] = "off"
        frontend.apply_preset()
        self.assertFalse((self.d / "home" / "blackboard.json").exists())

    def test_사전이_없으면_mba_front_on_은_컴파일을_못_한다_frontend_는_한다(self):
        # 이 차이가 MBA-frontend 를 따로 두는 까닭이다: 비교의 MBA-0(사전 1,1 · 0.771)과 MBA-1(9,1 · 0.509)
        os.environ.update(MBA_FRONT_MODE="on", MBA_ENABLE="cache,QUERY",
                          MBA_CLAUDE_BIN=str(fake_claude(self.d, "OP=QUERY;WHAT=HEAD_SUBJECT")))
        self.assertIsNone(hook.on_prompt({"prompt": "HEAD 커밋 제목 알려줘", "session_id": "S", "cwd": str(self.repo)}))
        self.assertEqual(self.ledger()[-1]["path"], "declined")
        frontend.apply_preset()
        out = hook.on_prompt({"prompt": "HEAD 커밋 제목 알려줘", "session_id": "S", "cwd": str(self.repo)})
        self.assertTrue(out["reason"].startswith(SUBJECT))

    def test_WAIT_PUBLISH_는_켜지_않는다(self):
        frontend.apply_preset()
        os.environ["MBA_CLAUDE_BIN"] = str(fake_claude(self.d, "OP=WAIT;PID=1;LOG=/tmp/x.log"))
        self.assertIsNone(hook.on_prompt({"prompt": "1 번 끝나면 /tmp/x.log 마지막 줄", "session_id": "S", "cwd": str(self.repo)}))
        self.assertEqual(self.ledger()[-1]["op"], "WAIT")


class Install(Base):
    def test_mba_front_훅을_바꾸고_하나만_남긴다(self):
        p = self.d / "settings.json"
        hook.install(p)                                             # 먼저 mba-front
        self.assertTrue(hook.install(p, command=frontend._command)["changed"])
        self.assertFalse(hook.install(p, command=frontend._command)["changed"])
        d = json.loads(p.read_text())
        cmds = [h["command"] for ev in ("UserPromptSubmit", "Stop") for g in d["hooks"][ev] for h in g["hooks"]]
        self.assertEqual(len(cmds), 2)
        self.assertTrue(all("frontend" in c for c in cmds), cmds)
        hook.install(p, remove=True)
        self.assertNotIn("hooks", json.loads(p.read_text()))

    def test_명령줄_설치와_깨진_입력에도_막지_않는다(self):
        env = {**os.environ, "MBA_HOME": tempfile.mkdtemp(), "PYTHONPATH": str(ROOT)}
        p = self.d / "s.json"
        r = subprocess.run([sys.executable, "-m", "mba.front.frontend", "install-hook", "--settings", str(p)],
                           capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(json.loads(r.stdout)["changed"])
        for which in ("prompt", "stop"):
            r = subprocess.run([sys.executable, "-m", "mba.front.frontend", which], input="JSON 아님", capture_output=True,
                               text=True, env=env, cwd=str(ROOT), timeout=30)
            self.assertEqual((r.returncode, r.stdout), (0, ""), (which, r.stderr))


if __name__ == "__main__":
    unittest.main()
