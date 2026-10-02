# MBA-frontend — 잰 그대로의 MBA 앞단

`mba-front` 의 여러 설정 가운데 **비교에서 잰 것(MBA-1) 하나**를 이름 붙여 한 줄로 켠다. 새 판정 논리는 없다 — `mba.front.hook` 그대로다.

```bash
pip install -e .                       # 또는 pip install git+https://github.com/cogito5170/MBA
mba-frontend install-hook              # ~/.claude/settings.json 에 UserPromptSubmit · Stop (mba-front 훅이 있으면 이것으로 바꾼다)
mba-frontend uninstall-hook
mba-frontend report --text             # 원장(해시 · 길이 · 토큰만)
```

| 설정 | 값 | 왜 |
|---|---|---|
| `MBA_FRONT_MODE` | `on` | 실제로 막는다(기본 `mba-front` 는 `shadow` — 아무것도 안 막는다) |
| `MBA_ENABLE` | `cache,QUERY` | 비교에서 잰 종류. WAIT · PUBLISH 는 켜지 않는다(PUBLISH 는 바깥 동작) |
| 컴파일 사전 | `(9, 1)` → p 0.9 > θ 0.8 | **이게 없으면 `on` 이어도 컴파일을 안 한다**(새 블랙보드의 사전 (1,1) = 0.5). 그러면 비교의 MBA-0 (0.771 · 틀림 1)이 된다 |

환경에 값이 이미 있으면 그것을 쓰고, 블랙보드에 사전이 이미 있으면 덮지 않는다 — 그때는 잰 설정이 아니다.

## 잰 것 — 같은 프롬프트 24 개(22 번 뺌, 23 개)

원자료: `bench/results/cmp-20261002T020134/`(첫 비교) · 재비교는 `cogito5170/frontend` 브랜치 `claude/frontend-start` 의
`docs/PREREG_앞단_재비교.md` · `docs/data/`(같은 하니스 `bench/compare_walp.py` · 같은 기준선, MBA 는 네 번 새로 컴파일 — 네 번 모두 같은 수).

| 앞단 | 정확도 | 오답률 | 틀린 가로챔 | 토큰 절감률 | 지연 합 |
|---|---|---|---|---|---|
| 기준선 `claude -p` | 23/23 | 0% | 0 | 0% | 122 s |
| walp-front (`bb0a754`) | 20/23 | 13.0% | 3 | 34.2% | 92 s |
| walp-front 고친 것 (`claude/fixed-head`) | 21/23 | 8.7% | 2 | 31.4% | 91 s |
| **MBA-frontend (= MBA-1)** | **23/23** | **0%** | **0** | **49.1%** | ~160 s |

실제 `claude -p` 로 한 번 확인(2026-10-02): 훅을 건 저장소에서 "HEAD 커밋 제목 알려줘" → 컴파일 1,214 토큰, QUERY 실행, Claude 토큰 0, 답 맞음.

## 알고 쓸 것 — 이 수가 기대는 범위

- 프롬프트 24 개(잡담 7 · 저장소 질문 6 · 반복 6 — 실제 사용 비율이 아니다) · haiku-4.5 하나 · 기준선 n=1
- **느리다**: 가로채지 못한 프롬프트마다 컴파일 4~5 초가 앞에 붙는다(지연 합 ~160 s 대 기준선 122 s)
- **잡담은 못 거른다**(1/7) — 잡담이 많은 쓰임에서는 절감률이 뒤집힐 수 있다. 이 24 개로는 모른다
- 가로채지 못한 프롬프트에도 컴파일 ~1.2k 토큰이 든다(절감률에 넣었다)
- 틀렸다고 느끼면 앞에 `//` 를 붙여 다시 보내면 Claude 로 간다
