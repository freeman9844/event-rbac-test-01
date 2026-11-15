# 구독 RBAC 이벤트 수신 워크숍 구현 계획

> **에이전트 작업자 안내:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development` 또는 `superpowers:executing-plans`로 작업별 구현을 수행한다. 이번 프로젝트에서 이미 선택한 실행 방식은 **Native 직접 실행**이다. 단계는 체크박스로 추적한다.

**Goal:** 검증된 구독 RBAC 수신기를 Cloud Shell Bash에서 재현하고 로그 대조와 안전한 정리까지 완료하는 한국어 워크숍으로 만들어 지정 GitHub 저장소에 게시한다.

**Architecture:** 기존 수신기와 사설 Storage 구조를 유지한다. 핵심 Azure CLI 명령은 교재에 노출하고, Python 표준 라이브러리 도구가 상태·소스 ZIP·테스트 역할·로그 증거·삭제 안전성을 담당한다. 7개 모듈과 README를 제공하며 ARM·Graph 보강은 구현하지 않는다.

**Tech Stack:** Python 3.11, unittest, Azure CLI, Bicep, Cloud Shell Bash, Markdown, Mermaid.

**Spec:** `docs\superpowers\specs\2026-11-15-rbac-event-workshop-design.md`

## Global Constraints

- 참가자 명령은 저장소 루트의 Cloud Shell Bash를 기준으로 작성한다.
- 기존 Python 수신기와 Event Grid 필터 의미는 유지한다.
- 새 앱 런타임 의존성이나 일반 목적의 배포 프레임워크를 추가하지 않는다.
- 사용자·역할명·`companyName` 자동 조회와 Graph 권한 부여는 제외한다.
- 소스인 구독마다 시스템 토픽은 하나만 허용된다. 참가자별 독립 실습 구독을 전제로 하며, 동일 구독의 동시 실행은 지원하지 않는다.
- 기존 구독 소스 시스템 토픽을 발견하면 새 생성 전에 중단한다.
- Storage는 공개 네트워크·익명 Blob·공유 키 접근을 비활성화한다.
- 기존 PoC 호출의 기본 동작은 유지하되 워크숍 명령에서는 Function 구독 Reader 옵션을 명시적으로 끈다.
- 토큰, 공유 키, Function 키와 전체 claims는 상태나 로그 증거에 기록하지 않는다.
- 조회 오류를 리소스 부재로 취급하지 않으며, 소유권 불일치 때는 삭제를 진행하지 않는다.
- 새 워크숍 전체를 실제 Azure에 재배포했다고 주장하지 않는다. 별도 승인 없는 추가 배포는 하지 않는다.
- 대상의 기존 내용을 강제 덮어쓰지 않고 `main`에 일반 push한 뒤 원격 HEAD와 실제 README·모듈을 확인한다.

## Review Focus

1. 기존 구독 소스 토픽이 다른 RG에 있거나 목록 조회가 거부된 경우: 새 실행을 허용하지 않는다. Task 1에서 검사한다.
2. 다른 실행의 상태·RG·배포 출력을 잘못 선택하거나 소유 태그가 변한 경우: 잘못된 저장과 삭제를 중단한다. Task 1·2·3에서 검사한다.
3. 역할 생성 요청이 서버에서는 성공했지만 응답이 실패한 경우: 사전 기록한 GUID로 자기 역할만 정리하고 실패를 숨기지 않는다. Task 3에서 검사한다.
4. UTC 문자열, 로그 수집 지연, eventId와 correlationId 불일치, 지표 표본 없음: 시간대와 증거 범위를 보존하고 합성 성공이나 0으로 대체하지 않는다. Task 3에서 검사한다.
5. 중간 배포 실패·RG 선삭제·구독 Reader 잔존·403: 가능한 자기 대상만 검증하여 정리하고 알 수 없는 대상을 삭제하지 않는다. Task 3에서 검사한다.

## 파일과 책임

| 파일 | 책임 |
|---|---|
| `scripts\workshop.py` | 표준 라이브러리 기반 실습 CLI. 상태·Azure 조회·포장·테스트·증거·정리 기능 |
| `tests\test_workshop_state.py` | 상태, 인증 경계, 기존 토픽 및 조회 실패 |
| `tests\test_workshop_artifacts.py` | 배포 출력 경계, 소스 ZIP과 비공개 자료 제외 |
| `tests\test_workshop_lifecycle.py` | 실제 역할 테스트의 모의 Azure 경계, 증거 판정, 정리 안전성 |
| `tests\test_workshop_docs.py` | 문서 링크, 코드 펜스, Bash 구문, 필수 배포 옵션 일관성 |
| `infra\main.bicep` | 태그를 보존하는 기존 RG 모드, 구독 Reader 선택 옵션과 정확한 정리 출력 |
| `README.md`, `docs\00-azure-rbac-basics.md` ~ `docs\06-cleanup-production.md` | 학습 진입점과 번호형 실습 |
| `docs\development.md` | 기존 Windows 개발 안내와 검증 한계 |
| `.gitignore`, `.funcignore` | 상태·증거·개발 환경을 Git과 배포 ZIP에서 제외 |

기존 `function_app.py`, `rbac_monitor`의 동작과 기존 테스트는 변경하지 않는다.
새 작업은 승인 후 기존 구현 HEAD에서 만든 `feat/rbac-workshop` 브랜치와 `.worktrees\rbac-workshop`에서 진행한다.
원래 `rbac-companyname` 워크트리와 그 비공개 실행 자료는 보존한다.

---

### Task 1: 실행 상태와 사전 점검

**Files:** 생성 `scripts\workshop.py`, `tests\test_workshop_state.py`; 수정 `.gitignore`.

**Interfaces**

- `run_az(args: list[str]) -> object`: 셸 문자열을 조합하지 않고 Azure CLI를 실행해 JSON을 반환한다. 비정상 종료, 시간 초과와 잘못된 JSON은 `WorkshopError`로 명시한다.
- `load_state(path: Path) -> dict`: 검증된 상태만 반환한다.
- `save_state(path: Path, state: dict) -> None`: 검증 후 같은 파일시스템의 임시 파일로 원자적으로 저장한다.
- `init_run(root: Path, subscription_id: str, tenant_id: str, location: str) -> Path`: 읽기 전용 사전 점검 후 새 상태 경로를 반환한다.
- `check_run(state: dict) -> None`: 계정·테넌트·사용자와 존재하는 전용 RG의 소유권을 확인한다.
- CLI: `init --subscription UUID --tenant UUID --location REGION`, `show STATE_FILE`, `check STATE_FILE`.

상태는 `schemaVersion=1`이다. `runId`는 임의 8자리 소문자 16진수, RG는 `rg-rbac-workshop-{runId}`, 접두사는 `rbac-lab`이다.
`tenantId`, `subscriptionId`, `userObjectId`, `location`, `createdAtUtc`, `deploymentNames.infra=rbac-ws-{runId}-infra`/`deploymentNames.events=rbac-ws-{runId}-events`, `outputs`, `assignment`를 기록한다.
`outputs`는 초기 빈 객체, `assignment`는 초기 null이다. 상태 경로는 `.workshop-state/{runId}.json`이다.
RG 소유 태그는 `workshop=rbac-event`, `runId={runId}`, `managedBy=workshop`이다. 세 필수 값은 일치해야 하며 추가 정책 태그는 허용한다.

- [x] **Step 1:** 정상 상태의 `show` CLI가 JSON을 반환하는 실패 테스트를 먼저 작성한다. 없는 스크립트의 비정상 종료 때문에 성공 조건이 실패하는지 확인한다.
- [x] **Step 2:** 최소 CLI와 상태 검증·저장을 구현하여 첫 테스트를 통과시킨다.
- [x] **Step 3:** 잘못된 UUID, runId/RG 불일치, 경로·명령 주입 문자열, 기존 상태 덮어쓰기 거부 테스트를 추가하고 RED → GREEN을 수행한다.
- [x] **Step 4:** `run_az` 경계만 모의 처리해 사용자 유형·테넌트 불일치, 다른 RG의 동일 source 시스템 토픽, 토픽 조회 403 테스트를 작성하고 RED → GREEN을 수행한다. `az account set`이나 자원 생성 명령이 실행되지 않았는지도 확인한다.
- [x] **Step 5:** `.workshop-state/`, `.superpowers/`, 소스 ZIP 등 실행 자료를 Git에서 제외한다. 오류를 빈 목록이나 부재로 바꾸지 않는지 확인한다.
- [x] **Step 6:** `.\.venv\Scripts\python.exe -m unittest discover -s .\tests -p "test_*.py" -v` 전체 PASS 후 관련 파일만 커밋한다.

### Task 2: 안전한 인프라 입력·배포 출력·소스 ZIP

**Files:** 수정 `infra\main.bicep`, `scripts\workshop.py`, `.funcignore`; 생성 `tests\test_workshop_artifacts.py`.

**Consumes:** Task 1의 검증된 상태와 `run_az`, `save_state`, `check_run`.

**Produces**

- Bicep `createResourceGroup bool = true`, `assignSubscriptionReader bool = true`. 워크숍은 먼저 소유 태그가 있는 새 RG를 만들고 두 옵션 모두 false로 전달한다.
- Bicep `subscriptionReaderAssignmentId` 출력은 역할을 생성할 때만 정확한 ID, 아니면 빈 문자열이다. 기존 함수·관리 ID·모니터링·토픽 출력은 유지한다.
- `capture_deployment(state_file: Path, deployment_name: str) -> None`: 해당 실행의 허용된 배포 이름과 `Succeeded`를 확인하고 경계 검증된 출력만 저장한다.
- `package_sources(root: Path, destination: Path) -> list[str]`: 허용된 런타임 소스만 ZIP 루트에 넣는다.
- CLI: `capture STATE_FILE --deployment NAME`, `package STATE_FILE`. package stdout은 생성한 ZIP 경로이며 증거 디렉터리는 `.workshop-state/{runId}/`이다.

- [x] **Step 1:** 모의 성공 배포 출력의 capture와 ZIP 생성이 성공해야 하는 실패 테스트를 작성하고 실행한다.
- [x] **Step 2:** 다른 실행 배포 이름, 실패한 배포, 다른 구독/RG의 리소스 출력, 잘못된 관리 ID UUID를 거부하는 테스트를 추가한다.
- [x] **Step 3:** capture와 package를 구현한다. ZIP은 `function_app.py`, `host.json`, `requirements.txt`, `rbac_monitor`의 Python 소스만 포함하며 심볼릭 링크와 루트 밖 경로는 거부한다.
- [x] **Step 4:** `.venv`, `.python_packages`, `local.settings.json`, 상태, 문서와 테스트가 있는 임시 프로젝트로 포장해 제외 및 ZIP 루트 구조를 확인한다.
- [x] **Step 5:** Bicep에 기존 RG 모드·Reader 조건·출력을 추가한다. 기존 RG 생성 선언은 그대로 두고 워크숍은 태그 있는 새 RG를 먼저 만든 뒤 `createResourceGroup=false`로 호출한다. 두 조건이 비활성일 때도 module scope·의존성과 출력이 유효하게 컴파일되도록 한다. 다른 런타임·네트워크 설정은 바꾸지 않는다.
- [x] **Step 6:** 전체 unittest, `az bicep build --file .\infra\main.bicep`, 기존 격리 Checkov를 사용한 관련 ARM 검사와 `git diff --check`를 실행한다. 기존에 문서화된 한정 예외를 운영 보안 전체 통과로 표현하지 않는다.
- [x] **Step 7:** 관련 파일만 커밋한다.

### Task 3: 실제 이벤트 실험·증거 대조·안전한 정리 도구

**Files:** 수정 `scripts\workshop.py`; 생성 `tests\test_workshop_lifecycle.py`.

**Consumes:** 검증된 실행 상태, Azure 배포 출력, 고유 증거 디렉터리.

**Produces**

- `exercise_event(state_file: Path) -> dict`: 자기 사용자에게 전용 RG Reader를 잠시 생성하고 `finally`에서 정확한 ID를 검증·삭제한다. 생성 요청 전에 GUID와 UTC 시각을 상태에 기록한다.
- `verify_event(state_file: Path, timeout_seconds: int = 600, interval_seconds: int = 20) -> dict`: 같은 할당 ID의 Activity와 수신 로그, 같은 실행의 성공 및 정확한 UTC 구간의 지표를 읽고 증거와 요약을 저장한다.
- `cleanup_run(state_file: Path, confirmation: str | None, dry_run: bool = False) -> dict`: 전체 대상 사전 검증 후 정확한 확인 문구를 받은 자기 대상만 정리한다.
- CLI: `test-event STATE_FILE`, `verify STATE_FILE`, `cleanup STATE_FILE [--dry-run]`.

테스트 역할은 Reader `acdd72a7-3385-48ef-bd42-f606fba81ae7`이며 scope는 상태의 전용 RG로 고정한다.
상태의 `assignment`에는 `name`, `id`, `requestedAtUtc`, `created`, `removed`를 저장한다.
기존 동일 직접 Reader는 삭제·교체하지 않는다. 기존 assignment 상태가 있으면 새 테스트 생성을 거부하고 기존 검증·정리를 안내한다.
Azure 리소스 조회는 ARM 응답의 `properties` 등 명시적 구조를 사용하고 CLI 표시용 속성 평탄화에 의존하지 않는다.
삭제 확인 문구는 `DELETE {runId} FROM {subscriptionId}`이다.

- [x] **Step 1:** 정상 test-event가 자기 역할을 생성·삭제하고 상태와 부재 증거를 남기는 테스트를 작성해 RED → GREEN을 수행한다.
- [x] **Step 2:** 기존 Reader 보존, 생성 응답 유실 뒤 예약 GUID 정리, 조회 403, 응답의 사용자·역할·scope 불일치 및 삭제 실패 테스트를 추가해 RED → GREEN을 수행한다. 생성 실패도 성공으로 숨기지 않는다.
- [x] **Step 3:** verify의 정상 및 수집 지연 테스트를 작성한다. 실제 SDK나 서비스가 아니라 `run_az`와 시계 경계만 모의 처리하고, 저장된 증거·요약을 검사한다.
- [x] **Step 4:** 다른 할당 로그만 존재하는 경우, `Success=false`, eventId와 Activity correlationId가 서로 다른 정상 이벤트, 지표 null 및 잘못된 UTC timespan, 시간 초과 테스트를 추가해 RED → GREEN을 수행한다.
- [x] **Step 5:** 증거 요약에 처리 시각·수집 시각, 함수 실행 ID, 대상 할당, 모니터링 범위와 표본 없는 지표를 구분한다. 증거에 전체 claims나 키가 들어가지 않게 한다.
- [x] **Step 6:** cleanup의 preview·확인 거부·정상 삭제 테스트를 작성해 RED → GREEN을 수행한다. 먼저 전체 소유권을 검사하고 자기 테스트 역할, 확인된 구독 Reader가 있으면 그 역할, 전용 RG 순으로 정리한다.
- [x] **Step 7:** 다른 실행 태그, 조회 403, 배포 출력 없는 부분 실패, 이미 없는 RG, RG 밖에 남은 기록된 Reader와 삭제 대기 초과 테스트를 추가한다. 확인되지 않은 역할은 삭제하지 않고 오류를 알린다.
- [x] **Step 8:** 전체 unittest와 `git diff --check` PASS 후 관련 파일만 커밋한다.

### Task 4: 한국어 단계별 교재와 문서 검증

**Files:** 수정 `README.md`; 생성 아래 문서와 `tests\test_workshop_docs.py`.

- `docs\00-azure-rbac-basics.md`: 개념, 대상과 수행자, 전체 구성·이벤트 흐름.
- `docs\01-prerequisites.md`: Cloud Shell·도구·권한·비용, clone·init·복구, 토픽 충돌 중단.
- `docs\02-private-infrastructure.md`: 소유 태그가 있는 새 RG 생성, what-if, RG 생성·Reader 비활성 인프라 배포, capture, PE·DNS·Storage 확인.
- `docs\03-function-deployment.md`: 소스 ZIP, Linux 원격 빌드, 등록 확인, 사설 Storage 403 진단과 무조건 재시작 금지.
- `docs\04-event-grid.md`: 코드 게시 후 what-if·eventing 활성화, 정확한 유형·작업·destination·retry 확인.
- `docs\05-rbac-event-validation.md`: 안전한 Reader 실험, Activity·KQL·함수 성공·지표 대조, 실제와 예시 구분.
- `docs\06-cleanup-production.md`: preview·정확한 확인·부분 실패·외부 역할·삭제 완료·운영 한계.
- `docs\development.md`: Windows 개발·전체 unittest·Bicep·한정 검사 및 기존 실측 예시의 한계.

**Consumes:** Task 1~3의 실제 CLI 이름·상태 필드·동작.

- [ ] **Step 1:** 예정 문서 링크의 존재, 상대 링크와 닫힌 코드 펜스, Bash 블록 구문, 모든 배포 블록의 `assignSubscriptionReader=false`를 검증하는 실패 테스트를 작성한다.
- [ ] **Step 2:** README를 110분 목차·권한·비용·Mermaid·복구·정리 중심의 시작 페이지로 바꾸고 00~06을 작성한다.
- [ ] **Step 3:** 각 장에 목표, 필요한 상태 복구, 별도 줄의 설명·실행·확인·주의, 실제 확인 기준, Portal 경로, 완료 체크와 이전·다음 링크를 제공한다.
- [ ] **Step 4:** 핵심 Azure 명령은 코드 블록에 보인다. 변경 명령은 앞선 검사 실패 시 실행되지 않게 연결하고 참가자 Cloud Shell에 전역 `set -e`나 강제 `exit`를 적용하지 않는다.
- [ ] **Step 5:** Linux Cloud Shell과 Windows 개발 안내를 분리하고 비밀 없는 예시를 사용한다. 키·정책 우회와 공유 키 활성화, claims로 대상 추론, 구독 Reader 불필요 부여를 권하지 않는다.
- [ ] **Step 6:** 표 파이프와 링크를 점검하고 Bash는 실행하지 않는 `bash -n`으로 검사한다. 실제 새 워크숍을 재배포한 것으로 표현하지 않는다.
- [ ] **Step 7:** 전체 unittest, Bicep 컴파일, `git diff --check` PASS 후 관련 파일만 커밋한다.

### Task 5: 전체 검토와 GitHub 동기화

**Files:** 필요한 경우 검토 결과와 연결된 파일만 수정한다. 새 런타임 기능은 추가하지 않는다.

**Consumes:** Task 1~4의 검증된 워크숍 브랜치와 대상 저장소의 쓰기 권한.

- [ ] **Step 1:** Native 절차의 전체 브랜치 검토를 한 번 수행하고 실제 참가자 경로·권한·정리·문서와 명령 일치 여부를 확인한다.
- [ ] **Step 2:** 유효한 지적을 수정한 경우 관련 회귀 테스트와 전체 unittest·Bicep·문서 검증을 재실행한다.
- [ ] **Step 3:** 추적 파일, ZIP 허용 목록 및 게시할 Git 이력에 환경 식별자와 비공개 실행 자료가 없는지 점검한다. 필요한 식별자는 비공개 원본과 로컬에서만 대조한다.
- [ ] **Step 4:** `gh api`의 대상 push 권한과 기존 Git 인증의 dry-run을 다시 확인한다. 403이면 준비된 로컬 커밋을 보존하고 사용자에게 차단 상태를 알린다. 계정 전환·대체 공개 저장소 생성·강제 push는 하지 않는다.
- [ ] **Step 5:** 권한이 있으면 정확한 대상 `https://github.com/freeman9844/event-rbac-test-01.git`에 일반 `HEAD:main` push를 수행한다. 대상이 더 이상 비어 있지 않으면 그 변경을 먼저 비교하고 덮어쓰지 않는다.
- [ ] **Step 6:** 원격 `main` SHA와 로컬 HEAD, GitHub README·모듈·실습 파일을 조회해 영구 게시를 확인한다. 공개 파일에는 비공개 상태나 ZIP이 없어야 한다.
- [ ] **Step 7:** 실제 완료와 미완료, 검증 범위, 게시 주소를 한국어로 보고한다. 권한 때문에 게시하지 못했다면 동기화 성공이라고 말하지 않는다.

## 자체 검토 결과와 실행 승인

설계 1~8절을 Task 1~5에 연결했다. 위 Review Focus 다섯 항목은 해당 작업의 구체적인 실패 테스트에 반영했다.
인터페이스는 단일 실습 CLI와 schema 1 상태를 기준으로 통일했고, 기존 앱 보강 작업은 섞지 않는다.
현재는 계획 검토 대기이며 구현 체크박스는 아직 완료되지 않았다. 기존에 선택된 Native 방식을 유지한다.
