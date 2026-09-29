# company-os-agent-ui

[`company-os-cli`](https://github.com/as950118/ai-company)로 만든 Company OS 인스턴스를 브라우저에서 다루기 위한 로컬 웹 제어판과, 실제 PM → Architect → Backend 에이전트가 동작하는 라이브 파일럿을 제공합니다. `company-os-cli`가 하는 일(스캐폴딩)과는 분리된 별도 패키지이며, 이 패키지는 `company-os-cli`를 의존성으로 사용합니다.

## 설치

```bash
pip install company-os-agent-ui
```

`company-os-cli`가 자동으로 함께 설치됩니다. 이 도구는 `company-os init`으로 만든 인스턴스 디렉터리를 대상으로 동작하므로, 다룰 인스턴스가 없다면 먼저 [`company-os-cli`](https://github.com/as950118/ai-company)로 하나 만들어두세요.

```bash
pip install company-os-cli
company-os init --name "Acme Agent Co" --product "Acme Task Hub" --out ./my-company-os
```

## 빠른 시작

```bash
company-os-agent-ui
# → http://127.0.0.1:8765/ 를 기본 브라우저로 자동 오픈
```

- 기본적으로 **localhost(127.0.0.1)에만 바인딩**됩니다 — 로컬 파일시스템에 쓰는 도구이므로 네트워크에 노출하지 않는 것이 기본값입니다.
- 프론트엔드(`/`)는 빌드 스텝/CDN 없는 단일 HTML 파일로 패키지에 포함되어 완전히 오프라인 동작합니다.

옵션:

| 옵션 | 설명 | 기본값 |
|---|---|---|
| `--host` | 바인드 주소 | `127.0.0.1` |
| `--port` | 포트 | `8765` |
| `--open-browser` / `--no-browser` | 시작 시 브라우저 자동 오픈 여부 | 오픈함 |
| `--version` | 버전 출력 | - |

화면의 Init/Upgrade 탭은 `company-os init`/`company-os upgrade`의 옵션을 그대로 노출하며, 내부적으로 `company_os_cli.scaffold`의 `scaffold()`/`upgrade()`를 그대로 호출하므로 실제 파일시스템에 씁니다 — 시뮬레이션이 아닙니다. 제출 결과(생성 경로, 파일 개수, 남은 플레이스홀더 경고, conflict 목록, 에러 메시지)는 CLI 출력과 동일한 정보를 API 응답으로 보여줍니다.

Board 탭은 인스턴스 디렉터리를 대상으로 진행 현황을 한눈에 보여줍니다 — 새 파일 형식을 만들지 않고 `company-os-cli`가 이미 쓰는 문서만 읽습니다.

- **Done / To do / In review·QA**: `tasks/*.md`(`docs/task-template.md`로 만든 파일) 각각의 `Status` 필드(Backlog/Ready/In Progress/In Review/QA/Done)를 읽어 3열로 분류합니다.
- **Docs needing review**: `projects/<slug>/{prd,architecture,api,adr}/*.md`와 `memory/decision-memory/ADR-*.md`의 `Status` 필드가 `Draft`/`Proposed`인 문서(즉 아직 승인 전인 PRD/Architecture/ADR)를 모아 보여줍니다.
- 읽기 전용 GET 엔드포인트(`/api/board?out=...`)라서 파일을 쓰지 않으며, Refresh 버튼이나 Enter로 다시 스캔합니다.
- 카드를 클릭하면 우측 패널에서 해당 Markdown 문서 원문을 바로 렌더링해서 보여주며(`/api/doc?out=...&path=...`), 문서 안의 상대 링크(다른 PRD/Architecture/ADR로의 링크)를 클릭해 인스턴스 안을 계속 탐색할 수 있습니다. 인스턴스 디렉터리 바깥 경로나 `.md`가 아닌 파일은 거부합니다.

## 실제 에이전트 파일럿 (`/office`)

`company-os-agent-ui`를 띄우면 `/`(제어판) 외에 `/office`에서 **실제 PM → Architect → Backend 에이전트 파일럿**을 실행할 수 있습니다. `company-os init`으로 만든 인스턴스의 `agents/pm.yaml`·`roles/pm.md` 같은 실제 역할 정의와 `docs/prd-template.md`/`architecture-template.md` 포맷을 그대로 사용해 PRD → Architecture → Implementation Notes를 실제로 생성하고, PM→Architect, Architect→Backend 핸드오프 순간에는 화면의 오피스 맵에서 두 에이전트가 회의실로 이동하는 모습을 실시간(WebSocket)으로 보여줍니다.

```bash
company-os-agent-ui
# → http://127.0.0.1:8765/office
```

- **기본은 MOCK 모드**입니다 — 갓 `company-os init`한 인스턴스는 `runtime/.env`가 없으므로 네트워크 호출·비용 없이 캔드 텍스트로 전체 파이프라인을 시연할 수 있습니다.
- 실제 OpenRouter 호출을 켜려면 인스턴스의 `runtime/.env.example`을 `runtime/.env`로 복사한 뒤 `MOCK_LLM=false`와 `OPENROUTER_API_KEY`를 채우세요. 우선순위는 프로세스 환경변수 > `runtime/.env` > 기본값(`company-os-cli`의 `runtime/company_os/config.py`에 있는 `mock_llm()`과 동일한 규칙)입니다.
- **실행당 실제 LLM 호출은 정확히 3번**(PM/Architect/Backend)입니다. 핸드오프 회의의 대화는 LLM이 아니라 코드가 만드는 고정 문구입니다. 동시에 두 번 실행할 수 없습니다(중복 비용 방지).
- 산출물은 인스턴스 안 고정 경로(`projects/<slug>/{prd,architecture,api}/*-0000-pilot.md`)에 저장됩니다 — 재실행하면 같은 파일을 덮어씁니다. 정식 문서 번호 체계(PRD-0001부터)와 충돌하지 않도록 파일럿 전용 `0000` 번호를 씁니다.
- LLM 응답은 파일 경로를 절대 결정하지 않습니다 — 코드가 고정한 3개 경로에만, 텍스트 내용으로만 쓰입니다.
- `/`(제어판)와 달리 **`/office`는 CDN(React/Tailwind/Babel/Pretendard)을 사용**해 인터넷 연결이 필요합니다 — 저장소 소유자가 직접 만든 메타버스 오피스 프로토타입(`index.html`)의 컴포넌트를 그대로 재사용하기 위한 의도적 예외입니다.

## 이 레포 구조

```text
company-os-agent-ui/
├── pyproject.toml
├── LICENSE
├── index.html                       ← office.html의 디자인 원본이 된 손수 제작 프로토타입 (참고용)
├── src/company_os_agent_ui/
│   ├── __init__.py                  ← __version__
│   ├── app.py                       ← FastAPI 앱 (company-os-cli의 scaffold()/upgrade()/pilot.run_pilot() 호출)
│   ├── pilot.py                     ← PM→Architect→Backend 파일럿 오케스트레이션 + OpenRouter 클라이언트
│   ├── board.py                     ← tasks/*.md·projects/**/*.md 상태를 읽는 Board 탭 스캐너 (읽기 전용)
│   ├── cli.py                       ← Typer CLI (`company-os-agent-ui` 명령)
│   └── static/
│       ├── index.html               ← 제어판 프론트엔드 (빌드 스텝/CDN 없음)
│       └── office.html              ← 파일럿 프론트엔드 (CDN 사용 — 의도적 예외)
├── tests/test_web.py                ← 웹 제어판 API 스모크 테스트 (Board API 포함)
├── tests/test_pilot.py              ← 파일럿 오케스트레이션 테스트 (전부 MOCK 모드, 네트워크 불필요)
└── .github/workflows/               ← CI (테스트) + publish (태그 push 시 PyPI 배포)
```

## 로컬 개발

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

company-os-agent-ui --version
python -m unittest discover -s tests -v

# 배포용 빌드 확인
python -m build
python -m zipfile -l dist/company_os_agent_ui-*-py3-none-any.whl
```

## 배포 (PyPI)

1. `pyproject.toml`과 `src/company_os_agent_ui/__init__.py`의 버전을 올린다
2. `git tag vX.Y.Z && git push origin vX.Y.Z`
3. `.github/workflows/publish.yml`이 태그 push 시 테스트 → 빌드 → PyPI 업로드까지 수행
   - PyPI API 토큰(https://pypi.org/manage/account/token/) 발급 후, GitHub 저장소의 `pypi` Environment(Settings → Environments → pypi)에 `PYPI_API_TOKEN` 시크릿으로 등록
   - 이 패키지는 아직 PyPI에 존재하지 않으므로 첫 배포는 "Entire account" 스코프 토큰이 필요합니다 (프로젝트 스코프 토큰은 프로젝트가 이미 존재해야 발급 가능). 첫 배포 성공 후엔 `company-os-agent-ui` 프로젝트 스코프로 좁힌 토큰으로 교체 권장

## 라이선스

MIT — [LICENSE](LICENSE) 참고.
