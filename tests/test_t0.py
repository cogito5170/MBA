"""T0 -- 코어의 어떤 경로도 LLM · 망에 닿지 않는다. 세 겹: 정적(import) · 실행 계수 · 망 차단.
각 겹이 **망가진 코드에서 빨개지는 것** 도 여기서 본다(거짓 초록 방지)."""
import ast
import socket
import tempfile
import unittest
from pathlib import Path

from mba.core.engine import Engine
from mba.front import llm
from tests.helpers import MISSION, ROOT, model, scenario

ALLOWED = {"__future__", "re", "itertools", "hashlib", "dataclasses", "typing", "collections", "math"}
FORBIDDEN_CALLS = {"__import__", "eval", "exec", "compile", "open"}


def violations(path: Path) -> list:
    out = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        mods = []
        if isinstance(n, ast.Import):
            mods = [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            mods = [n.module or ""]
        for mod in mods:
            top = mod.split(".")[0]
            ok = top in ALLOWED or mod.startswith(("mba.core", "mba.ir")) or mod in ("mba.core", "mba.ir")
            if not ok:
                out.append(f"{path.name}:{n.lineno} import {mod}")
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in FORBIDDEN_CALLS:
            out.append(f"{path.name}:{n.lineno} {n.func.id}()")
    return out


def core_files():
    return sorted((ROOT / "mba" / "core").glob("*.py"))


class Static(unittest.TestCase):
    def test_코어는_허용된_모듈만_부른다(self):
        self.assertTrue(core_files())
        bad = [v for f in core_files() for v in violations(f)]
        self.assertEqual(bad, [])

    def test_IR_도_망을_모른다(self):
        # ir 은 load() 에서 open 을 쓴다 -- 파일 읽기는 허용, 망 모듈은 안 된다
        bad = [v for f in sorted((ROOT / "mba" / "ir").glob("*.py")) for v in violations(f) if "open()" not in v]
        self.assertEqual(bad, [])

    def test_정적_겹은_주입한_위반을_잡는다(self):
        d = Path(tempfile.mkdtemp())
        for i, src in enumerate(["import urllib.request\n", "from mba.front import llm\n",
                                 "import socket\n", "x = __import__('http')\n", "import anthropic\n"]):
            p = d / f"bad{i}.py"
            p.write_text(src)
            self.assertTrue(violations(p), src)


class Dynamic(unittest.TestCase):
    def setUp(self):
        llm.reset()
        self.calls = []
        llm.set_backend(lambda prompt, purpose: (self.calls.append(purpose) or "x", 100))
        self.real = socket.socket
        self.tried = []
        outer = self

        class Blocked(socket.socket):
            def __init__(self, *a, **k):
                outer.tried.append(a)
                raise OSError("망 차단(T0 시험)")
        socket.socket = Blocked

    def tearDown(self):
        socket.socket = self.real
        llm.set_backend(None)

    def test_주기마다_LLM_호출_0_망_시도_0(self):
        e = Engine(model(), MISSION)
        for _ in range(30):
            for raw in scenario():
                before = llm.CALLS["n"]
                e.cycle(raw)
                self.assertEqual(llm.CALLS["n"] - before, 0)
        self.assertEqual((self.calls, self.tried), ([], []))

    def test_계수_겹은_주입한_호출을_잡는다(self):
        # 코어가 llm.call 을 부르게 망가뜨린 변형 -- 같은 단언이 빨개져야 한다
        e = Engine(model(), MISSION)
        orig = e.cycle

        def broken(raw):
            llm.call("코어가 부르면 안 된다", "core")
            return orig(raw)
        e.cycle = broken
        before = llm.CALLS["n"]
        e.cycle(scenario()[0])
        self.assertNotEqual(llm.CALLS["n"] - before, 0)

    def test_망_겹은_주입한_접속을_잡는다(self):
        with self.assertRaises(OSError):
            socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.assertEqual(len(self.tried), 1)


if __name__ == "__main__":
    unittest.main()
