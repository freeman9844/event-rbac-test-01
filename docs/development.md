# 개발·검증 안내

[워크숍 홈](../README.md) · [상태 복구](01-prerequisites.md) · [정리](06-cleanup-production.md)

## 로컬 개발과 Cloud Shell 구분

워크숍은 Linux Cloud Shell Bash를 대상으로 합니다. 아래는 **Windows 개발자용 PowerShell**이며 두 셸의 문법을 섞지 않습니다. Python 3.11 이상, Azure CLI·Bicep, 문서 Bash 검사에는 Git for Windows의 Bash가 필요합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -p "test_*.py" -v
az bicep build --file .\infra\main.bicep
git --no-pager diff --check
```

Windows ARM64에서 의존성 휠이 없으면 x64 Python 3.11로 별도 가상환경을 만듭니다. 전역 도구·기존 로그인·기본 구독을 임의 변경하지 않습니다.

Linux에서 이미 의존성을 준비했다면 다음으로 테스트합니다.

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
az bicep build --file infra/main.bicep
git --no-pager diff --check
```

수신기 테스트는 엄격한 이벤트 범위·작업·소스 검증과 전체 payload/claims 비기록을 검사합니다. 워크숍 테스트는 실제 상태 파일·ZIP·증거를 다루며 Azure CLI와 시계 경계만 모의 처리합니다. 문서는 상대 링크·코드 펜스·`bash -n`을 검사하고, 표시한 변경 블록은 격리된 fake CLI에서 실행하여 실제 인자·사전 검사 실패 시 중단을 검증합니다. 테스트는 Azure 리소스를 생성·삭제하지 않습니다.

## Bicep 호환성

기존 PoC 호출의 기본 동작을 유지합니다.

| 파라미터 | 기본값 | 워크숍에서 전달할 값 |
|---|---|---|
| `createResourceGroup` | `true` | `false` — 소유 태그가 있는 RG를 CLI로 먼저 생성 |
| `assignSubscriptionReader` | `true` | `false` — 수신에 불필요한 구독 Reader 제외 |
| `enableEventSubscription` | `false` | 인프라 단계 false / 코드 게시·등록 이후 true |

`enableEventSubscription=false`로 재배포해도 기존 이벤트 구독은 삭제되지 않습니다. 구독 수준 Reader는 RG 삭제에 따라 자동 제거되지 않으므로 과거 PoC·수동 호출은 해당 실행의 정확한 ID와 소유권을 별도로 확인해야 합니다. 현재 워크숍 도구에 과거 실행의 RG·토픽·ID를 넣지 않습니다.

## 과거 실측 예시와 현재 검증 범위

2026-11-15의 **기존 PoC**에서는 실제 임시 RG Reader 생성·삭제, Activity Log, Event Grid, Function 및 AppRequests를 대조했습니다. 재시험 관측 예시는 다음과 같습니다. 환경·사용자·할당·correlation 식별자는 공개하지 않습니다.

| 관측 항목 | 과거 관측 |
|---|---|
| write 성공 → Function 수신 | 약 3.0초 |
| AppRequests 처리 시간 | 약 36.2ms |
| 수신 → ingestion | 약 37.1초 |
| 해당 UTC 구간 토픽 집계 | 게시 2 / 매칭 1 / 전달 성공 1 |
| 전달 시도 실패 | 숫자 표본 없음. **명시적 0이 아님** |
| 임시 역할 | 삭제·부재 확인 |

이는 성능 보장·새 워크숍 전체 재배포 결과가 아닙니다. Event Grid eventId와 Activity correlationId가 같았던 관측을 일반 불변 조건으로 쓰지 않습니다. 새 워크숍은 모의 CLI 기반 자동 검증과 템플릿 컴파일을 수행하며 실제 Azure 재배포는 별도 승인·실행이 필요합니다.

## 한정 정적 검사

기존 격리 Checkov 환경에서 컴파일한 `infra/main.json`에 ARM 검사 22 pass, 0 fail, parsing error 0을 확인했습니다. 이 결과는 다음 검사 ID를 해당 실행에서만 제외한 **한정 검사**입니다.

`CKV_AZURE_17`, `CKV_AZURE_18`, `CKV_AZURE_36`, `CKV_AZURE_43`, `CKV_AZURE_67`, `CKV_AZURE_206`, `CKV_AZURE_212`, `CKV_AZURE_213`, `CKV_AZURE_222`, `CKV_AZURE_225`

제외 범위는 클라이언트 인증서·HTTP/2·Azure Services 우회 권고, ARM 동적 이름 해석, 복제·인스턴스·가용 영역·별도 상태 확인과 Function 공개 수신 관련 PoC 제약입니다. Storage 공개 접근 차단은 제외하지 않았습니다. 전체 운영 보안·컴플라이언스 통과를 뜻하지 않으며 전역 skip 파일을 만들지 않습니다.

## 설계 자료

- [승인된 워크숍 설계](superpowers/specs/2026-11-15-rbac-event-workshop-design.md)
- [구현 계획](superpowers/plans/2026-11-15-rbac-event-workshop.md)

공개 커밋·Function ZIP에 `.workshop-state`, `.superpowers`, 가상환경·로그·로컬 설정이 들어가지 않게 합니다. 실제 환경 ID를 테스트 fixture 대신 사용하지 않습니다.
