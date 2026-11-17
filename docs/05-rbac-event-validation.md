# 05. 실제 역할 할당과 로그 검증

> 자기 사용자에게 임시 Reader를 할당하고, 같은 할당의 요청·수신·실행 성공을 따라갑니다.

이전: [04. 이벤트 연결](04-event-grid.md) · [홈](../README.md) · 다음: [06. 정리](06-cleanup-production.md)

## 목표

- 이번 RG에 자기 Reader 한 건만 생성하고 정확한 ID로 정리합니다.
- Activity Log의 대상·역할·scope와 Function의 수신·실행 성공을 연결합니다.
- 실제 시각과 수집 시각, 개별 실행과 토픽 집계를 구분해 증거를 보관합니다.

## 예상 소요 시간

20분. `verify`는 로그 수집을 최대 약 10분 기다리며, 시간 초과는 성공이 아닙니다.

## 시작 전 상태

- Module 04의 배포·capture와 실제 필터·목적지 확인을 마쳤습니다.
- 이번 상태에 이미 실행한 실험이 있다면 `test-event`를 반복하지 않고 `verify`·cleanup으로 복구합니다.
- 새 실험의 대상은 로그인한 자기 사용자, 범위는 이번 전용 RG, 역할은 Reader입니다.

## 1. 안전한 Reader 실험

👁️ **설명**

01의 `STATE_FILE`·함수를 복구하고 04 배포 성공을 확인합니다. 도구는 자기 사용자와 이번 전용 RG의 Reader로 제한합니다. 생성 **전에** GUID·할당 ID·UTC 시각을 저장하고 `finally`에서 정확한 대상을 확인해 삭제합니다. 이번 Function은 ARM 보강을 하지 않으므로 역할을 먼저 삭제하고 로그 수집을 기다려도 됩니다.

🟢 **실행**

```bash
test_rbac_event() {
  restore_workshop || return
  python3 scripts/workshop.py test-event "$STATE_FILE"
}
test_rbac_event
```

📋 **확인**

응답의 `created=true`, `removed=true`를 확인합니다. `.workshop-state/<실행ID>.json`의 `assignment`에는 `name`, `id`, `requestedAtUtc`, `created`, `removed`가 남습니다.

핵심 Azure 동작은 `scripts/workshop.py`에서 확인할 수 있습니다.

| 순서 | Azure CLI 경계 | 안전 조건 |
|---|---|---|
| 기존 역할 조회 | `az rest --method get` 역할 할당 목록·전체 페이지 | 동일 직접 Reader가 있으면 중단, 기존 역할 보존 |
| 생성 | `az rest --method put` 예약한 할당 ID | `principalId=자기 사용자`, `principalType=User`, Reader 정의 ID |
| 삭제 | `az rest --method delete` 같은 ID | 실제 응답의 사용자·역할·scope 대조 후 삭제·부재 확인 |

⚠️ **주의**

생성 응답이 유실되어도 저장한 GUID로 자기 대상만 확인·정리합니다. 생성 오류를 성공으로 바꾸지 않으며 삭제 실패는 `removed=false`로 남깁니다. 오류가 나면 **다시 test-event를 실행하지 말고** [06의 정리](06-cleanup-production.md)를 사용합니다. 이미 기록한 실험은 새 실험으로 덮어쓰지 않습니다.

## 2. 수집 완료까지 대조

👁️ **설명**

수신까지의 지연과 로그 수집 지연은 다릅니다. `verify`는 최대 약 10분간 조회하며 Activity의 쓰기·삭제 성공과 대상·Reader 요청 본문, 같은 할당 수신 및 같은 InvocationId/OperationId의 AppRequests 성공을 대조합니다.

🟢 **실행**

```bash
verify_rbac_event() {
  restore_workshop || return
  python3 scripts/workshop.py verify "$STATE_FILE" --timeout-seconds 600
}
verify_rbac_event
```

Log Analytics 명령이 없는 환경에서는 오류를 확인한 후에만 확장을 설치하고 **verify만** 재시도합니다.

```bash
# "az monitor log-analytics query" 명령이 없다는 오류가 난 경우에만 실행합니다.
az extension add --name log-analytics
```

📋 **확인**

`verified=true`와 `.workshop-state/<실행ID>/verification.json`을 확인합니다. 이 파일은 명시적 UTC 조회 구간, 성공 Activity 시각·correlationId, 대상 Object ID·역할 ID, Function 수신·수집 시각, invocationId·operationId·eventId·durationMs, 토픽 전체 지표를 포함합니다. 전체 claims·인증 토큰·원본 이벤트를 저장하지 않습니다.

| 확인 영역 | 성공 판단 |
|---|---|
| 대상·역할·scope | Activity 쓰기 요청의 `principalId`·Reader ID·동일 할당 리소스 ID |
| 생성·정리 | 같은 할당의 write/delete `Succeeded` |
| 실제 수신 | `RbacRoleAssignmentReceived`의 동일 할당 ID |
| 실제 실행 | 동일 InvocationId와 OperationId의 AppRequests `Success=true` |
| 지표 구간 | 요청 UTC start/end와 Azure 반환 `timespan`이 같음 |

⚠️ **주의**

시간 초과는 성공이 아닙니다. 로컬 증거는 `verified=false`로 남습니다. 진단 후 같은 실행의 verify를 재시도하며 새 역할을 더 만들지 않습니다. 로그 읽기 403은 “아직 로그가 없음”으로 취급하지 않습니다.

## 3. Portal에서 직접 따라가기

👁️ **설명**

**구독 → 활동 로그**에서 이번 RG·시각으로 필터링하고 역할 할당 생성과 삭제를 엽니다. JSON의 요청 본문은 시작 레코드에 있고 성공 레코드에는 없을 수 있습니다. 동일 **할당 ID**를 기준으로 묶어 대상과 성공을 대조합니다.

🟢 **실행**

Portal → **Log Analytics Workspace → 로그**에서 아래 KQL의 자리 표시자를 자기 상태의 값으로 바꿉니다. `AppTraces/AppRequests`는 Workspace 테이블입니다. Application Insights 범위에서는 `traces/requests`와 다른 열 이름을 사용하므로 그대로 섞지 않습니다.

```kusto
let app = "<이번 Function App 이름>";
let assignment = "<이번 전체 역할 할당 ID>";
let received = AppTraces
| where TimeGenerated > ago(1h)
| where AppRoleName =~ app
| extend body = parse_json(Message), InvocationId = tostring(Properties.InvocationId)
| where tostring(body.eventName) == "RbacRoleAssignmentReceived"
| where tostring(body.roleAssignmentId) =~ assignment
| project ReceivedAt=TimeGenerated, IngestedAt=ingestion_time(),
          EventId=tostring(body.eventId), InvocationId, OperationId;
received
| join kind=inner (
    AppRequests
    | where TimeGenerated > ago(1h) and AppRoleName =~ app
    | project RequestInvocationId=tostring(Properties.InvocationId),
              RequestOperationId=OperationId, Success, DurationMs
) on $left.OperationId == $right.RequestOperationId
| where isnotempty(InvocationId) and InvocationId == RequestInvocationId
| project ReceivedAt, IngestedAt, EventId, InvocationId, OperationId, Success, DurationMs
```

📋 **확인**

실제 수신 행과 `Success=true`를 확인합니다. Activity `correlationId`와 Event Grid `eventId`가 같지 않아도 할당 ID와 함수 실행 ID로 연결할 수 있습니다.

**Event Grid 시스템 토픽 → 메트릭**에서 증거의 UTC 구간을 그대로 적용하고 `PublishSuccessCount`, `MatchedEventCount`, `DeliverySuccessCount`, `DeliveryAttemptFailCount`를 `Total`로 봅니다.

⚠️ **주의**

지표는 특정 할당의 추적 ID가 아니라 **토픽 전체 집계**입니다. 게시 2건·매칭 1건이어도 다른 관리 이벤트나 필터가 원인일 수 있습니다. 숫자 표본 없는 `null`을 `0`으로 해석하지 않습니다. 실제 0 표본만 0입니다. 비동기 지표와 로그 수집에는 시차가 있습니다.

## 4. 실제 관측 예시

👁️ **설명**

다음은 **2026-11-17 기존 PoC를 재배포한 실제 관측**입니다. 이 장의 Cloud Shell 보조 도구를 처음부터 끝까지 재실행한 결과는 아니며, 참가자의 출력·성능 보장·목표 시간이 아닙니다. 환경·사용자·할당 ID는 공개하지 않습니다.

| 단계 | 관측 결과 |
|---|---|
| 새 전용 RG의 테스트 사용자에게 Reader 할당 | 14:26:59.268 KST 쓰기 성공 |
| Event Grid 필터 매칭·전달 | 해당 UTC 구간의 토픽 집계에서 매칭 1 / 전달 성공 1 |
| `RbacRoleAssignmentReceived` | 14:27:05.786 KST 수신. 쓰기 성공 후 약 **6.52초** |
| Function 실행 | 같은 OperationId와 InvocationId의 AppRequests `Success=true`, 약 36.2ms |
| 수신 로그 수집 | 14:27:40.645 KST. 수신 후 약 **34.86초** |
| 일반 관리 이벤트와의 구분 | RG 태그 갱신도 수행. 테스트 구간 수신 1건, validation 오류 0건 |
| 정리 | RG와 내부 17개 자원, 이번 PoC의 두 임시 Reader 삭제. 14:41:27 KST 별도 조회에서 RG 부재와 두 역할 404 확인 |

📋 **확인**

실제 수신 메시지의 **형식**은 다음과 같습니다. 자리 표시자는 원본 식별자를 가린 것으로, 실행하거나 붙여 넣을 JSON 입력이 아닙니다.

```json
{
  "schemaVersion": 1,
  "eventName": "RbacRoleAssignmentReceived",
  "eventId": "<수신한 이벤트 ID>",
  "roleAssignmentId": "/subscriptions/<구독 ID>/resourceGroups/<이번 RG>/providers/Microsoft.Authorization/roleAssignments/<할당 GUID>"
}
```

이 로그에는 사용자명·역할명이 없습니다. 위 실측의 대상 Object ID와 Reader 역할은 운영자가 ARM으로 따로 대조했습니다. **실제 이벤트 수신 성공을 자동 보강 기능 구현 완료로 해석하지 않습니다.**

⚠️ **주의**

해당 관측 구간은 게시 성공 14·매칭 1·전달 성공 1의 **토픽 전체 집계**이며, 개별 비-RBAC 이벤트의 전체 전달 경로를 증명하지 않습니다. 전달 시도 실패는 수치 표본이 없어 **측정된 0이 아닙니다**.

기존 PoC 기본 배포에는 Function 관리 ID의 구독 Reader가 포함되어 사용자 임시 Reader와 함께 두 건을 정리했습니다. **정상 워크숍은 `assignSubscriptionReader=false`**를 사용하므로 그 권한을 만들지 않습니다. 세부 검증 경계와 이전 관측은 [개발 안내](development.md)를 참고합니다.

## 트러블슈팅

| 증상 | 확인과 다음 행동 |
|---|---|
| `test-event`가 실패했습니다 | 저장한 할당 ID와 상태를 보존하고 Module 06의 preview로 복구합니다. 새 역할을 더 만들지 않습니다. |
| 수신 로그가 있지만 실행 성공 행이 없습니다 | 요청 로그의 수집 지연과 동일 OperationId·InvocationId를 확인하고 같은 실행의 verify만 재시도합니다. |
| `verified=false` 또는 시간 초과입니다 | Activity Log·수신·요청 중 빠진 증거와 오류를 확인합니다. 새 실험이 아니라 조회를 재시도합니다. |
| 로그 읽기에서 403이 발생합니다 | 권한 오류를 해결합니다. 빈 결과나 “아직 수집 중”으로 처리하지 않습니다. |
| 지표가 null이거나 eventId와 correlationId가 다릅니다 | null과 0을 구분하고, 동일 할당 ID·실행 ID로 대조합니다. 두 이벤트 ID가 같아야 한다고 가정하지 않습니다. |

## 완료 체크

- [ ] 자기 임시 Reader의 생성·삭제와 부재를 확인했습니다.
- [ ] 같은 할당의 대상·역할·Activity 성공과 수신·실행 성공을 대조했습니다.
- [ ] UTC 구간과 null/0 차이를 확인하고 비공개 증거를 보관했습니다.

이전: [04](04-event-grid.md) · 다음: [06. 정리](06-cleanup-production.md)
