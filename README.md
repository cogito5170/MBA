# MBA

Model-Based Autonomy tool. 목적은 **LLM 토큰을 최소로 쓰는 것** 하나다(`docs/재설계_토큰최소.md`).
LLM 은 자연어를 기호로 바꾸는 **작은 머리 컴파일러**로만 쓰고, 나머지는 토큰 0 으로 돈다.

## 잰 것 (`docs/PREREG_토큰차이*.md`, claude -p 120 호출, haiku-4.5, 계기 대조 최대 차 0.00%)

- 지금의 에이전트 루프 대비 MBA + 도구 없는 작은 머리: 과제당 **17~95배** 적다. 성공은 30/30
- 그 감소의 **62~100% 는 고정 머리**(호출마다 다시 실리는 시스템 프롬프트 · 도구 정의)에서 왔다. 구조 몫은 0~38% 이고,
  규칙대로 선 구조 이득은 **같은 질문 반복**(캐시, 4.0배) 하나다
- 사건 도구(walp_wait · walp_publish)를 가진 에이전트 대비 구조 이득은 보이지 않았다
- 과제 다섯 · 3 회 · haiku 하나. 과제는 MBA 의 닫힌 연산 안에서 골랐다 -- 일반 에이전트 대비 주장이 아니다

## 결정: 앞단은 MBA 를 쓴다 (`docs/결정_앞단_선택.md`)

walp-front 와 같은 프롬프트 24 개로 비교한 결과(`docs/PREREG_비교_walp.md`) MBA 가 우세했다 -- **LLM 토큰 0.510 대 0.658,
틀린 가로챔 0 대 3**(walp-front 는 섞인 말 · 저장소 질문에서 사람의 요청을 잃었다). 그래서 Claude 앞단으로 **MBA 를 쓴다.**
단 잡담은 walp-front 가 낫고(7/7 토큰 0), MBA 는 컴파일 때문에 더 느리다. 근거의 범위는 결정 문서에 적었다.

## MBA-frontend — 잰 설정 그대로 한 줄로 (`docs/MBA-frontend.md`)

```bash
mba-frontend install-hook      # mba-front 를 on · cache,QUERY · 컴파일 사전 (9,1) 로 -- 비교의 MBA-1
```

같은 24 프롬프트에서 **정확도 23/23 · 틀린 가로챔 0 · 토큰 절감 49.1%**(walp-front 고친 것 21/23 · 31.4%). 대신 느리고(컴파일 4~5 초) 잡담은 못 거른다.

## mba-front — Claude Code 의 모든 프롬프트 앞에

```
프롬프트 ─▶ 0 '//' · '/명령'          → 건드리지 않음
          1 같은 질문 + 같은 저장소 상태 → 캐시된 답 (토큰 0)
          2 작은 머리 컴파일(~1.5k)      → 닫힌 연산 QUERY · WAIT · PUBLISH 실행
          3 그 밖 · 실패                 → 그대로 Claude 로
Stop    ─▶ 그 턴의 실제 토큰 · 상태가 바뀌었나 · 무슨 도구를 썼나 → 원장. 읽기만 한 턴의 답만 캐시에
```

### 설치 (그림자 모드 — 아무것도 막지 않는다)

```bash
pip install git+https://github.com/cogito5170/MBA          # 또는 저장소에서 pip install -e .
mba-front install-hook                                      # ~/.claude/settings.json 의 UserPromptSubmit · Stop 에 하나씩(.bak-mba 백업)
mba-front uninstall-hook                                    # 떼면 원래 설정 그대로
```

그림자 모드에서는 프롬프트마다 **뒤에서** 컴파일 호출 한 번(`claude -p --tools "" --system-prompt <한 장>`, haiku, ~1.5k 토큰)이
돈다. 지연은 0 이고, 그 토큰은 측정 비용이다. 끄려면 `MBA_SHADOW_COMPILE=0`(그러면 캐시 경로만 잰다).

### 보고 · 켜기

```bash
mba-front report --text      # 종류별: 제안 · 일치 · 틀림 · 일치율 · 켰다면 아꼈을 토큰 · 켜기 후보
```

**켜기 후보 = 판정된 제안 ≥ 10 그리고 일치율 ≥ 90%.** 후보인 종류만 켠다:

```bash
export MBA_FRONT_MODE=on MBA_ENABLE=cache            # 예: 캐시만
export MBA_ALLOW_PUBLISH="/home/me/work/*"            # PUBLISH 를 켤 때만 -- 허락한 저장소에서만 민다
```

on 모드에서 컴파일은 **정답 확률 관문**을 지난다: 그 자리에서 컴파일이 맞을 확률 p 가 문턱(`MBA_THETA`, 기본 0.8)을 **넘을 때만**
부른다(같으면 안 부른다). 사전은 (1,1) 이라 기록이 쌓이기 전에는 부르지 않는다. 막은 답이 틀렸으면 **앞에 `//` 를 붙여 다시 보내면**
그대로 Claude 에 간다.

| 일치 판정(잠정) | |
|---|---|
| cache | 그 턴이 저장소를 안 바꿨고, 캐시 답과 Claude 의 답이 유사도 ≥ 0.6 |
| QUERY | MBA 가 낸 HEAD 제목이 Claude 의 답에 들어 있다 |
| WAIT | Claude 가 그 턴에 실제로 기다렸다(sleep · kill -0 · walp_wait …) |
| PUBLISH | Claude 가 그 턴에 실제로 밀었다(git push · walp_publish) |

### 저장과 안전

- `~/.mba/`(`MBA_HOME`) — 이 기계 밖으로 안 나간다. `ledger.jsonl` 에는 해시 · 길이 · 토큰 수만(프롬프트 글 없음).
  `blackboard.json` 에 캐시된 답(글) · 정답 확률 기록, `pending/` 에 턴 대조용 임시 파일(최종 답 글 포함) — `mba-front purge` 로 지운다
- **막는 쪽으로 틀리지 않는다**: 훅에서 어떤 예외가 나도 아무것도 출력하지 않고 0 으로 끝난다 → 프롬프트는 그대로 Claude 에
- 컴파일 자식은 부모 세션 ID 를 받지 않고(`CLAUDE_CODE_SESSION_ID` 를 지움) 훅을 다시 부르지 않는다(`MBA_FRONT_MODE=off`)
- 그림자에서는 WAIT · PUBLISH 를 **실행하지 않는다**(QUERY 는 읽기만이라 실행해 답을 적는다)

## 검사

```bash
python3 -m unittest discover -s tests -t .
```

핵심 규칙은 망가뜨린 변형에서 검사가 빨개지는 것을 확인했다(토큰 경계 · 관문 동률 · 동치류 최소 · 그림자 무차단 · 캐시는 읽기만 한
턴에서 · 그림자에서 바깥 연산 안 함 · 자식 위생 · 켜기 문턱).
