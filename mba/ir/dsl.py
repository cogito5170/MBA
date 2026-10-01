"""`.mba` DSL -> Model. docs/설계.md §2.4 의 부분집합 + 토큰 경계 선언(COST · BUDGET).

들어온 것: SYMBOL · COMPONENT(MODES · DEPENDS ON · OBSERVED_BY · PREDICT) · OBSERVE · PRIOR · EVENT · DERIVE ·
BEHAVIOR/RULE · ARBITRATION · SAFETY(VETO · FALLBACK) · COST · BUDGET.
아직 안 들어온 것: TRANSITION · FRAGMENT · MISSION 블록(임무는 mba.front.intent 가 만든 dict 로 받는다).

컴파일러가 거부하는 것: 기호 타입 · 미선언 기호 · 맨 아래 층의 전체성 · 한 층 안의 규칙 겹침 ·
VETO 될 수 있는 마지막 FALLBACK.
"""
from __future__ import annotations

import hashlib
import itertools
import re
from dataclasses import dataclass, field

SID = re.compile(r"^[SEAFCBRGM]\d{2,3}$")
_TOK = re.compile(r"""
    (?P<ws>\s+) | (?P<com>\#[^\n]*) | (?P<str>"[^"]*") |
    (?P<num>-?\d+(?:\.\d+)?) | (?P<id>[A-Za-z_][A-Za-z0-9_]*) |
    (?P<op>==|!=|<=|>=|->|[{}();,=<>!&|.:])
""", re.X)


class DslError(ValueError):
    pass


@dataclass
class Component:
    cid: str
    name: str
    modes: dict                      # mode -> prior rank (NOMINAL · F## · UNKNOWN)
    depends: list = field(default_factory=list)
    observed_by: list = field(default_factory=list)
    predict: dict = field(default_factory=dict)     # mode -> guard


@dataclass
class Rule:
    rid: str
    on: tuple
    guard: tuple
    action: "str | None"             # None = PASS


@dataclass
class Behavior:
    bid: str
    name: str
    priority: int
    enabled: tuple
    rules: list


@dataclass
class Model:
    symbols: dict = field(default_factory=dict)      # sid -> name
    components: dict = field(default_factory=dict)
    observe: list = field(default_factory=list)      # (sid, channel, cmp, value, ticks)
    priors: list = field(default_factory=list)       # (sid, key, value)
    events: list = field(default_factory=list)       # (eid, edge, sid)
    derive: list = field(default_factory=list)       # (sid, guard)
    behaviors: dict = field(default_factory=dict)
    order: list = field(default_factory=list)        # 중재 순서(왼쪽이 이긴다)
    vetoes: list = field(default_factory=list)       # (actions, guard, reason)
    fallbacks: list = field(default_factory=list)    # (guard, action)
    costs: list = field(default_factory=list)        # (action, tokens|"RECONFIG"|("CHANNEL", ch), guard)
    budget: dict = field(default_factory=lambda: {"RECONFIG": 0, "CANDIDATES": 4096})
    warnings: list = field(default_factory=list)
    ir_hash: str = ""

    def cone(self, cid: str) -> list:
        """cid 와 그것이 (전이적으로) 기대는 부품들. 정렬된 목록."""
        seen, stack = set(), [cid]
        while stack:
            c = stack.pop()
            if c not in seen:
                seen.add(c)
                stack.extend(self.components[c].depends)
        return sorted(seen)


def _tokens(text: str) -> list:
    out, pos = [], 0
    while pos < len(text):
        m = _TOK.match(text, pos)
        if not m:
            raise DslError(f"읽을 수 없는 글자: {text[pos:pos + 20]!r}")
        pos = m.end()
        if m.lastgroup in ("ws", "com"):
            continue
        out.append(m.group())
    return out


class _P:
    def __init__(self, text: str):
        self.t = _tokens(text)
        self.i = 0

    def peek(self, k=0):
        return self.t[self.i + k] if self.i + k < len(self.t) else None

    def next(self):
        if self.i >= len(self.t):
            raise DslError("파일이 중간에 끝났다")
        self.i += 1
        return self.t[self.i - 1]

    def eat(self, want):
        got = self.next()
        if got != want:
            raise DslError(f"{want!r} 가 와야 하는데 {got!r}")
        return got

    def sid(self, prefix=None):
        s = self.next()
        if not SID.match(s) or (prefix and s[0] not in prefix):
            raise DslError(f"기호 ID 가 와야 한다({prefix or 'S/E/A/F/C/B/R'}): {s!r}")
        return s

    def sids(self, prefix):
        out = [self.sid(prefix)]
        while self.peek() == ",":
            self.next()
            out.append(self.sid(prefix))
        return out

    # guard = conj { "|" conj } ; conj = lit { "&" lit }
    def guard(self):
        xs = [self.conj()]
        while self.peek() == "|":
            self.next()
            xs.append(self.conj())
        return xs[0] if len(xs) == 1 else ("or", tuple(xs))

    def conj(self):
        xs = [self.lit()]
        while self.peek() == "&":
            self.next()
            xs.append(self.lit())
        return xs[0] if len(xs) == 1 else ("and", tuple(xs))

    def lit(self):
        t = self.peek()
        if t == "!":
            self.next()
            return ("not", self.lit())
        if t == "(":
            self.next()
            g = self.guard()
            self.eat(")")
            return g
        if t == "TRUE":
            self.next()
            return ("true",)
        if t == "MODE":
            self.next()
            self.eat("(")
            c = self.sid("C")
            self.eat(")")
            self.eat("==")
            return ("mode", c, self.mode_ref())
        if t == "MISSION":
            self.next()
            self.eat(".")
            k = self.next()
            self.eat("==")
            return ("mission", k, self.next())
        return ("bit", self.sid("S"))

    def mode_ref(self):
        m = self.next()
        if m in ("NOMINAL", "UNKNOWN") or (SID.match(m) and m[0] == "F"):
            return m
        raise DslError(f"모드가 와야 한다(NOMINAL · UNKNOWN · F##): {m!r}")


def _name(p: _P):
    n = p.next()
    if not re.match(r"^[A-Za-z_]\w*$", n):
        raise DslError(f"이름이 와야 한다: {n!r}")
    return n


def parse(text: str) -> Model:
    p, m = _P(text), Model()

    def declare(sid, name):
        if sid in m.symbols and m.symbols[sid] != name:
            raise DslError(f"{sid} 를 두 이름으로 선언했다: {m.symbols[sid]} / {name}")
        m.symbols[sid] = name

    while p.peek() is not None:
        kw = p.next()
        if kw == "SYMBOL":
            sid, name = p.sid(), _name(p)
            if p.peek() and p.peek().startswith('"'):
                p.next()
            declare(sid, name)
            p.eat(";")
        elif kw == "COMPONENT":
            cid, name = p.sid("C"), _name(p)
            declare(cid, name)
            c = Component(cid, name, {})
            p.eat("{")
            while p.peek() != "}":
                item = p.next()
                if item == "MODES":
                    while True:
                        mode = p.mode_ref()
                        rank = 0
                        if p.peek() == "PRIOR":
                            p.next()
                            rank = int(p.next())
                        c.modes[mode] = rank
                        if p.peek() != ",":
                            break
                        p.next()
                elif item == "DEPENDS":
                    p.eat("ON")
                    c.depends = p.sids("C")
                elif item == "OBSERVED_BY":
                    c.observed_by = p.sids("S")
                elif item == "PREDICT":
                    mode = p.mode_ref()
                    p.eat(":")
                    c.predict[mode] = p.guard()
                else:
                    raise DslError(f"COMPONENT 안에서 모르는 항목: {item!r}")
                p.eat(";")
            p.eat("}")
            c.modes.setdefault("NOMINAL", 0)
            m.components[cid] = c
        elif kw == "OBSERVE":
            sid, name = p.sid("S"), _name(p)
            declare(sid, name)
            p.eat("=")
            chan = p.next() + p.eat(".") + p.next()
            cmp = p.next()
            if cmp not in ("<", "<=", ">", ">=", "==", "!="):
                raise DslError(f"비교가 와야 한다: {cmp!r}")
            val = float(p.next())
            ticks = 1
            if p.peek() == "FOR":
                p.next()
                ticks = int(p.next())
                p.eat("TICKS")
            m.observe.append((sid, chan, cmp, val, ticks))
            p.eat(";")
        elif kw == "PRIOR":
            sid, name = p.sid("S"), _name(p)
            declare(sid, name)
            p.eat("=")
            p.eat("MISSION")
            p.eat(".")
            k = p.next()
            p.eat("==")
            m.priors.append((sid, k, p.next()))
            p.eat(";")
        elif kw == "EVENT":
            eid, name = p.sid("E"), _name(p)
            declare(eid, name)
            p.eat("=")
            edge = p.next()
            if edge not in ("RISE", "FALL"):
                raise DslError(f"RISE · FALL 만: {edge!r}")
            m.events.append((eid, edge, p.sid("S")))
            p.eat(";")
        elif kw == "DERIVE":
            sid, name = p.sid("S"), _name(p)
            declare(sid, name)
            p.eat("=")
            m.derive.append((sid, p.guard()))
            p.eat(";")
        elif kw == "BEHAVIOR":
            bid, name = p.sid("B"), _name(p)
            declare(bid, name)
            p.eat("PRIORITY")
            prio = int(p.next())
            enabled = ("true",)
            if p.peek() == "ENABLED_BY":
                p.next()
                enabled = p.guard()
            rules = []
            p.eat("{")
            while p.peek() != "}":
                p.eat("RULE")
                rid = p.sid("R")
                on = ()
                if p.peek() == "ON":
                    p.next()
                    on = [p.sid("E")]
                    while p.peek() == "|":
                        p.next()
                        on.append(p.sid("E"))
                    on = tuple(on)
                p.eat("WHEN")
                g = p.guard()
                p.eat("DO")
                act = p.next()
                if act == "PASS":
                    act = None
                elif not (SID.match(act) and act[0] == "A"):
                    raise DslError(f"DO 뒤에는 A## 또는 PASS: {rid} {act!r}")
                rules.append(Rule(rid, on, g, act))
                p.eat(";")
            p.eat("}")
            m.behaviors[bid] = Behavior(bid, name, prio, enabled, rules)
        elif kw == "ARBITRATION":
            m.order = [p.sid("B")]
            while p.peek() == ">":
                p.next()
                m.order.append(p.sid("B"))
            p.eat(";")
        elif kw == "SAFETY":
            p.eat("{")
            while p.peek() != "}":
                item = p.next()
                if item == "VETO":
                    acts = p.sids("A")
                    p.eat("WHEN")
                    g = p.guard()
                    p.eat("REASON")
                    m.vetoes.append((tuple(acts), g, _name(p)))
                elif item == "FALLBACK":
                    p.eat("WHEN")
                    g = p.guard()
                    p.eat("DO")
                    m.fallbacks.append((g, p.sid("A")))
                else:
                    raise DslError(f"SAFETY 안에서 모르는 항목: {item!r}")
                p.eat(";")
            p.eat("}")
        elif kw == "COST":
            a = p.sid("A")
            p.eat("TOKENS")
            v = p.next()
            if v == "CHANNEL":                              # 그 주기의 센서 값(예: ctx.tokens)
                tokens = ("CHANNEL", p.next() + p.eat(".") + p.next())
            else:
                tokens = "RECONFIG" if v == "RECONFIG" else int(v)
            g = ("true",)
            if p.peek() == "WHEN":
                p.next()
                g = p.guard()
            m.costs.append((a, tokens, g))
            p.eat(";")
        elif kw == "BUDGET":
            k = p.next()
            if k not in m.budget:
                raise DslError(f"모르는 예산: {k!r}")
            p.next()                                    # TOKENS | CANDIDATES (단위 이름)
            m.budget[k] = int(p.next())
            p.eat(";")
        else:
            raise DslError(f"모르는 선언: {kw!r}")
    _check(m)
    m.ir_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return m


# ---------------- 정적 검사 ----------------

def _atoms(g, out):
    k = g[0]
    if k in ("and", "or"):
        for x in g[1]:
            _atoms(x, out)
    elif k == "not":
        _atoms(g[1], out)
    elif k != "true":
        out.add(g)
    return out


def _check(m: Model):
    known = set(m.symbols)

    def need(sid, what):
        if sid not in known:
            raise DslError(f"{what}: 선언 안 된 기호 {sid}")

    def need_guard(g, what):
        for a in _atoms(g, set()):
            if a[0] == "bit":
                need(a[1], what)
            elif a[0] == "mode":
                need(a[1], what)
                if a[1] in m.components and a[2] not in m.components[a[1]].modes and a[2] != "UNKNOWN":
                    raise DslError(f"{what}: {a[1]} 에 없는 모드 {a[2]}")

    for c in m.components.values():
        for d in c.depends:
            if d not in m.components:
                raise DslError(f"{c.cid} DEPENDS ON 선언 안 된 부품 {d}")
        for s in c.observed_by:
            need(s, c.cid)
        cone = set(m.cone(c.cid))
        cone_bits = {s for x in cone for s in m.components[x].observed_by}
        for mode, g in c.predict.items():
            need_guard(g, f"{c.cid} PREDICT")
            for a in _atoms(g, set()):
                if a[0] == "bit" and a[1] not in cone_bits:
                    raise DslError(f"{c.cid} PREDICT {mode} 이 원뿔 밖 비트 {a[1]} 를 본다")
    for e, _, s in m.events:
        need(s, e)
    for s, g in m.derive:
        need_guard(g, f"DERIVE {s}")
    acts = {s for s in known if s[0] == "A"}
    for b in m.behaviors.values():
        need_guard(b.enabled, b.bid)
        for r in b.rules:
            need_guard(r.guard, r.rid)
            for e in r.on:
                need(e, r.rid)
            if r.action and r.action not in acts:
                raise DslError(f"{r.rid}: 선언 안 된 행동 {r.action}")
        _overlap(m, b)
    if not m.order:
        raise DslError("ARBITRATION 이 없다")
    for bid in m.order:
        if bid not in m.behaviors:
            raise DslError(f"ARBITRATION 에 선언 안 된 층 {bid}")
    bottom = m.behaviors[m.order[-1]]
    if bottom.enabled != ("true",) or not any(r.guard == ("true",) and not r.on and r.action for r in bottom.rules):
        raise DslError(f"전체성: 맨 아래 층 {bottom.bid} 에 'WHEN TRUE DO A##' 규칙이 없다")
    for acts_, g, _ in m.vetoes:
        need_guard(g, "VETO")
        for a in acts_:
            need(a, "VETO")
    if not m.fallbacks or m.fallbacks[-1][0] != ("true",):
        raise DslError("SAFETY 의 마지막 FALLBACK 은 'WHEN TRUE' 여야 한다")
    last = m.fallbacks[-1][1]
    if any(last in a for a, _, _ in m.vetoes):
        raise DslError(f"마지막 FALLBACK {last} 가 VETO 대상이다 -- 안전 대체가 막히면 갈 곳이 없다")
    for a, t, g in m.costs:
        need(a, "COST")
        need_guard(g, "COST")
        if t == "RECONFIG" and m.budget["RECONFIG"] <= 0:
            raise DslError("COST 가 RECONFIG 를 쓰는데 BUDGET RECONFIG 가 없다")


def _overlap(m: Model, b: Behavior, limit: int = 16):
    """한 층 안에서 두 규칙이 동시에 참일 수 있으면 오류(먼저 쓴 것이 이긴다는 암묵 규칙을 두지 않는다).
    원자를 펼쳐 진리표로 본다. MODE · MISSION 원자는 같은 열쇠끼리 배타(한 부품은 한 모드)."""
    from mba.core.guard import holds
    rules = b.rules
    if len(rules) < 2:
        return
    atoms = set()
    for r in rules:
        _atoms(r.guard, atoms)
    bits = sorted(a[1] for a in atoms if a[0] == "bit")
    keyed = {}
    for a in atoms:
        if a[0] in ("mode", "mission"):
            keyed.setdefault((a[0], a[1]), set()).add(a[2])
    events = sorted({e for r in rules for e in r.on})
    choices = [[v for v in sorted(vs)] + ["__other__"] for vs in (keyed[k] for k in sorted(keyed))]
    space = 2 ** (len(bits) + len(events)) * max(1, len(list(itertools.product(*choices))) if choices else 1)
    if space > 2 ** limit:
        m.warnings.append(f"{b.bid}: 겹침 검사를 건너뛰었다(경우 {space})")
        return
    keys = sorted(keyed)
    for bv in itertools.product((0, 1), repeat=len(bits)):
        on_bits = {s for s, v in zip(bits, bv) if v}
        for ev in itertools.product((0, 1), repeat=len(events)):
            on_ev = {e for e, v in zip(events, ev) if v}
            for kv in (itertools.product(*choices) if choices else [()]):
                modes = {k[1]: v for k, v in zip(keys, kv) if k[0] == "mode"}
                mission = {k[1]: v for k, v in zip(keys, kv) if k[0] == "mission"}
                hit = [r.rid for r in rules
                       if (not r.on or on_ev & set(r.on)) and holds(r.guard, on_bits, modes, mission)]
                if len(hit) > 1:
                    raise DslError(f"{b.bid}: 규칙 {hit} 가 같은 상태에서 함께 참이다 "
                                   f"(비트 {sorted(on_bits)}, 사건 {sorted(on_ev)}, {modes or ''}{mission or ''})")


def load(path) -> Model:
    with open(path, encoding="utf-8") as f:
        return parse(f.read())
