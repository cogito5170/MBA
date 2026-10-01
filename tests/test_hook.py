"""mba-front 앞단 -- 그림자는 아무것도 막지 않는다 · 캐시는 읽기만 한 턴에서만 · on 은 켠 종류만 · 막는 쪽으로 안 틀린다.
진짜 claude 는 부르지 않는다(가짜 실행 파일이 인자 · 환경을 적는다)."""
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from mba.front import hook, ops
from tests.helpers import ROOT

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
SUBJECT = "fix: 경계 정리 7f3"


def git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, env={**os.environ, **GIT_ENV})


def repo(d: Path) -> Path:
    r = d / "repo"
    r.mkdir()
    git("init", "-q", "-b", "main", cwd=r)
    (r / "a.txt").write_text("a")
    git("add", "-A", cwd=r)
    git("commit", "-q", "-m", SUBJECT, cwd=r)
    return r


def fake_claude(d: Path, result: str) -> Path:
    log = d / "claude_calls.jsonl"
    p = d / "fake_claude"
    p.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import json, os, sys
        with open({str(log)!r}, "a") as f:
            f.write(json.dumps({{"argv": sys.argv[1:], "mode": os.environ.get("MBA_FRONT_MODE"),
                                "parent_sid": "CLAUDE_CODE_SESSION_ID" in os.environ}}) + "\\n")
        print(json.dumps({{"result": {result!r}, "is_error": False,
                          "usage": {{"input_tokens": 1000, "cache_creation_input_tokens": 0,
                                    "cache_read_input_tokens": 0, "output_tokens": 30}}}}))
    """))
    p.chmod(0o755)
    return p


def transcript(d: Path, since: float, text: str, tools=(), tokens=(5000, 7000)) -> Path:
    p = d / "t.jsonl"
    rows = []
    for i, tok in enumerate(tokens):
        ts = dt.datetime.fromtimestamp(since + 1 + i, dt.timezone.utc).isoformat().replace("+00:00", "Z")
        content = [{"type": "tool_use", "name": n, "input": inp} for n, inp in (tools if i == 0 else [])]
        if i == len(tokens) - 1:
            content.append({"type": "text", "text": text})
        rows.append({"type": "assistant", "timestamp": ts,
                     "message": {"id": f"m{i}", "content": content,
                                 "usage": {"input_tokens": 0, "cache_read_input_tokens": tok, "output_tokens": 0}}})
    old = dt.datetime.fromtimestamp(since - 100, dt.timezone.utc).isoformat().replace("+00:00", "Z")
    rows.insert(0, {"type": "assistant", "timestamp": old, "message": {"id": "old", "content": [],
                                                                       "usage": {"output_tokens": 999999}}})
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


class Base(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.env = dict(os.environ)
        os.environ["MBA_HOME"] = str(self.d / "home")
        for k in ("MBA_FRONT_MODE", "MBA_ENABLE", "MBA_SHADOW_COMPILE", "MBA_CLAUDE_BIN", "MBA_ALLOW_PUBLISH"):
            os.environ.pop(k, None)
        self.repo = repo(self.d)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)

    def ledger(self):
        p = self.d / "home" / "ledger.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.is_file() else []

    def turn(self, prompt, answer, sid="S", tools=(), change=False):
        """프롬프트 -> (Claude 가 답함) -> Stop. change=True 면 그 턴에 저장소를 바꾼다."""
        out = hook.on_prompt({"prompt": prompt, "session_id": sid, "cwd": str(self.repo)})
        since = json.loads((self.d / "home" / "pending" / f"{sid}.json").read_text())["t"]
        if change:
            (self.repo / "b.txt").write_text(str(time.time()))
        tr = transcript(self.d, since, answer, tools)
        hook.on_stop({"session_id": sid, "cwd": str(self.repo), "transcript_path": str(tr)})
        return out


class Shadow(Base):
    def setUp(self):
        super().setUp()
        os.environ["MBA_SHADOW_COMPILE"] = "0"

    def test_그림자는_아무것도_막지_않는다(self):
        for q in ("HEAD 커밋 제목이 뭐야?", "HEAD 커밋 제목이 뭐야?", "새 기능을 짜줘"):
            self.assertIsNone(self.turn(q, SUBJECT))
        paths = [r["path"] for r in self.ledger() if r["e"] == "prompt"]
        self.assertEqual(paths, ["pass", "cache", "pass"])                      # 두 번째는 캐시였을 것
        self.assertNotIn("HEAD 커밋", (self.d / "home" / "ledger.jsonl").read_text())   # 원장에 글은 없다

    def test_캐시는_읽기만_한_턴에서만_채운다(self):
        self.turn("파일 하나 만들어줘", "만들었다", change=True)
        self.turn("파일 하나 만들어줘", "만들었다")
        paths = [r["path"] for r in self.ledger() if r["e"] == "prompt"]
        self.assertEqual(paths, ["pass", "pass"])

    def test_상태가_바뀌면_캐시를_안_쓴다(self):
        self.turn("HEAD 커밋 제목이 뭐야?", SUBJECT)
        (self.repo / "c.txt").write_text("c")                                   # 턴 사이에 바뀜
        self.turn("HEAD 커밋 제목이 뭐야?", SUBJECT)
        self.assertEqual([r["path"] for r in self.ledger() if r["e"] == "prompt"], ["pass", "pass"])

    def test_탈출과_명령은_건드리지_않는다(self):
        for q in ("// HEAD 커밋 제목", "/clear", "   "):
            self.assertIsNone(hook.on_prompt({"prompt": q, "session_id": "S", "cwd": str(self.repo)}))
        self.assertEqual({r["path"] for r in self.ledger()}, {"escape"})

    def test_off_면_아무것도_적지_않는다(self):
        os.environ["MBA_FRONT_MODE"] = "off"
        self.assertIsNone(hook.on_prompt({"prompt": "HEAD 커밋 제목", "session_id": "S", "cwd": str(self.repo)}))
        self.assertEqual(self.ledger(), [])

    def test_git_이_아니면_캐시를_안_쓴다(self):
        nogit = self.d / "nogit"
        nogit.mkdir()
        for _ in range(2):
            hook.on_prompt({"prompt": "뭐야", "session_id": "N", "cwd": str(nogit)})
            hook.on_stop({"session_id": "N", "cwd": str(nogit), "transcript_path": None})
        self.assertEqual([r["path"] for r in self.ledger() if r["e"] == "prompt"], ["pass", "pass"])


class Compile(Base):
    def test_뒤에서_컴파일하고_QUERY_는_읽기만_실행한다(self):
        os.environ["MBA_CLAUDE_BIN"] = str(fake_claude(self.d, "OP=QUERY;WHAT=HEAD_SUBJECT"))
        os.environ["CLAUDE_CODE_SESSION_ID"] = "부모"
        out = hook.on_prompt({"prompt": "지금 HEAD 커밋 제목 알려줘", "session_id": "S", "cwd": str(self.repo)})
        self.assertIsNone(out)                                                  # 지연 0 -- 뒤에서 돈다
        rid = self.ledger()[-1]["rid"]
        comp = self.d / "home" / "pending" / f"{rid}.compile.json"
        for _ in range(100):
            if comp.is_file():
                break
            time.sleep(0.1)
        res = json.loads(comp.read_text())
        self.assertEqual((res["op"], res["answer"], res["tokens"]), ("QUERY", SUBJECT, 1030))
        call = json.loads((self.d / "claude_calls.jsonl").read_text().splitlines()[0])
        self.assertEqual(call["mode"], "off")                                   # 자식은 훅을 다시 안 부른다
        self.assertFalse(call["parent_sid"])                                    # 부모 세션 ID 를 물려주지 않는다
        a = call["argv"]
        self.assertEqual(a[a.index("--tools") + 1], "")                         # 도구 없음
        self.assertIn("--system-prompt", a)                                     # 작은 머리
        self.assertFalse((self.d / "home" / "pending" / f"{rid}.prompt.json").exists())   # 프롬프트 글은 지운다

    def test_WAIT_PUBLISH_는_그림자에서_실행하지_않는다(self):
        os.environ["MBA_CLAUDE_BIN"] = str(fake_claude(self.d, "OP=PUBLISH;MESSAGE=x;FILES=a.txt"))
        (self.d / "home" / "pending").mkdir(parents=True)
        (self.d / "home" / "pending" / "r1.prompt.json").write_text(json.dumps({"prompt": "밀어줘", "cwd": str(self.repo)}))
        res = hook.compile_bg("r1")
        self.assertEqual((res["op"], res["answer"]), ("PUBLISH", None))
        self.assertEqual(subprocess.run(["git", "-C", str(self.repo), "log", "--oneline"], capture_output=True,
                                        text=True).stdout.count("\n"), 1)        # 커밋도 안 했다

    def test_NONE_이나_엉뚱한_출력은_제안이_아니다(self):
        for i, out in enumerate(["OP=NONE", "안녕하세요 무엇을 도와드릴까요", "OP=QUERY;WHAT=ALL_FILES"]):
            os.environ["MBA_CLAUDE_BIN"] = str(fake_claude(self.d, out))
            (self.d / "home" / "pending").mkdir(parents=True, exist_ok=True)
            (self.d / "home" / "pending" / f"n{i}.prompt.json").write_text(json.dumps({"prompt": "x", "cwd": str(self.repo)}))
            self.assertNotIn(hook.compile_bg(f"n{i}")["op"], ("QUERY", "WAIT", "PUBLISH"))


class On(Base):
    def setUp(self):
        super().setUp()
        os.environ["MBA_SHADOW_COMPILE"] = "0"

    def test_켠_종류만_막는다(self):
        self.turn("HEAD 커밋 제목이 뭐야?", SUBJECT)
        os.environ["MBA_FRONT_MODE"] = "on"
        self.assertIsNone(hook.on_prompt({"prompt": "HEAD 커밋 제목이 뭐야?", "session_id": "S", "cwd": str(self.repo)}))
        os.environ["MBA_ENABLE"] = "cache"
        out = hook.on_prompt({"prompt": "HEAD 커밋 제목이 뭐야?", "session_id": "S", "cwd": str(self.repo)})
        self.assertEqual(out["decision"], "block")
        self.assertTrue(out["reason"].startswith(SUBJECT))
        self.assertIn("//", out["reason"])

    def test_정답_확률이_문턱을_못_넘으면_컴파일도_안_한다(self):
        os.environ.update(MBA_FRONT_MODE="on", MBA_ENABLE="QUERY",
                          MBA_CLAUDE_BIN=str(fake_claude(self.d, "OP=QUERY;WHAT=HEAD_SUBJECT")))
        self.assertIsNone(hook.on_prompt({"prompt": "HEAD 제목", "session_id": "S", "cwd": str(self.repo)}))
        self.assertEqual(self.ledger()[-1]["path"], "declined")                 # 사전 (1,1) -> p 0.5 <= 0.8
        self.assertFalse((self.d / "claude_calls.jsonl").exists())

    def test_확률이_넘으면_컴파일하고_켠_연산을_실행해_막는다(self):
        from mba.front.blackboard import Blackboard
        b = Blackboard(self.d / "home" / "blackboard.json")
        c = b.confidence()
        c.priors["compile"] = (9, 1)
        b.save_confidence(c)
        os.environ.update(MBA_FRONT_MODE="on", MBA_ENABLE="QUERY",
                          MBA_CLAUDE_BIN=str(fake_claude(self.d, "OP=QUERY;WHAT=HEAD_SUBJECT")))
        out = hook.on_prompt({"prompt": "HEAD 제목", "session_id": "S", "cwd": str(self.repo)})
        self.assertTrue(out["reason"].startswith(SUBJECT))
        self.assertEqual((self.ledger()[-1]["op"], self.ledger()[-1]["tokens"]), ("QUERY", 1030))

    def test_발행은_허락_목록_없이는_안_한다(self):
        with self.assertRaises(ops.OpError):
            ops.execute({"OP": "PUBLISH", "MESSAGE": "x", "FILES": "a.txt"}, self.repo)
        os.environ["MBA_ALLOW_PUBLISH"] = "/nowhere/*"
        self.assertFalse(ops.publish_allowed(self.repo))
        os.environ["MBA_ALLOW_PUBLISH"] = str(self.repo.resolve())
        self.assertTrue(ops.publish_allowed(self.repo))


class Report(Base):
    def setUp(self):
        super().setUp()
        os.environ["MBA_SHADOW_COMPILE"] = "0"

    def test_턴_토큰은_그_프롬프트_뒤만_같은_id_는_한_번(self):
        t = transcript(self.d, 1000.0, "답", tokens=(5000, 7000))
        r = hook.turn_from_transcript(t, 1000.0)
        self.assertEqual((r["tokens"], r["final"]), (12000, "답"))              # 'old'(999999)는 빠진다

    def test_일치와_켜기_후보(self):
        for i in range(11):
            self.turn("HEAD 커밋 제목이 뭐야?", SUBJECT)                        # 1번 = 채움, 2~11번 = 캐시 제안 10 개
        r = hook.report()
        c = r["classes"]["cache"]
        self.assertEqual((c["proposals"], c["agree"], c["wrong"]), (10, 10, 0))
        self.assertTrue(c["eligible"])
        self.assertEqual(c["saved_turn_tokens"], 10 * 12000)
        self.assertEqual(r["totals"]["estimated_net_saving_if_all_on"], 120000)

    def test_캐시가_틀리면_틀림으로_센다(self):
        self.turn("HEAD 커밋 제목이 뭐야?", SUBJECT)
        self.turn("HEAD 커밋 제목이 뭐야?", "전혀 다른 긴 대답입니다 이건 완전히 다른 내용이다")
        c = hook.report()["classes"]["cache"]
        self.assertEqual((c["agree"], c["wrong"], c["eligible"]), (0, 1, False))

    def test_WAIT_일치는_Claude_가_실제로_기다렸나(self):
        (self.d / "home" / "pending").mkdir(parents=True, exist_ok=True)
        hook._append({"e": "compile", "rid": "w1", "op": "WAIT", "tokens": 1500})
        hook._write(self.d / "home" / "pending" / "w1.stop.json", {"final": "x", "kinds": ["wait"], "tokens": 40000})
        hook._append({"e": "stop", "rid": "w1", "tokens": 40000})
        r = hook.report()
        self.assertEqual(r["classes"]["WAIT"]["agree"], 1)
        self.assertEqual(r["totals"]["estimated_net_saving_if_all_on"], 40000 - 1500)


class Install(Base):
    def test_한_번만_걸고_남의_훅은_지키고_되돌린다(self):
        p = self.d / "settings.json"
        orig = {"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other.sh"}]}]}}
        p.write_text(json.dumps(orig))
        self.assertTrue(hook.install(p)["changed"])
        self.assertFalse(hook.install(p)["changed"])
        d = json.loads(p.read_text())
        cmds = [h["command"] for ev in ("UserPromptSubmit", "Stop") for g in d["hooks"][ev] for h in g["hooks"]]
        self.assertEqual(sum("mba" in c for c in cmds), 2)
        self.assertIn("other.sh", cmds)
        self.assertEqual(json.loads(p.with_name("settings.json.bak-mba").read_text()), orig)
        hook.install(p, remove=True)
        self.assertEqual(json.loads(p.read_text()), orig)


class FailOpen(unittest.TestCase):
    def test_깨진_입력에도_막지_않는다(self):
        env = {**os.environ, "MBA_HOME": tempfile.mkdtemp(), "PYTHONPATH": str(ROOT)}
        for stdin in ("이건 JSON 이 아니다", "[1,2]", ""):
            for which in ("prompt", "stop"):
                r = subprocess.run([sys.executable, "-m", "mba.front.hook", which], input=stdin, capture_output=True,
                                   text=True, env=env, cwd=str(ROOT), timeout=30)
                self.assertEqual((r.returncode, r.stdout), (0, ""), (which, stdin, r.stderr))


if __name__ == "__main__":
    unittest.main()
