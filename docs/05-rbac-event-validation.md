# 05. 실제 역할 할당과 로그 검증

⏱️ **20분** · 목표: 같은 할당의 요청·수신·실행 성공을 대조합니다.

이전: [04. 이벤트 연결](04-event-grid.md) · [홈](../README.md) · 다음: [06. 정리](06-cleanup-production.md)

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

## 완료 체크

- [ ] 자기 임시 Reader의 생성·삭제와 부재를 확인했습니다.
- [ ] 같은 할당의 대상·역할·Activity 성공과 수신·실행 성공을 대조했습니다.
- [ ] UTC 구간과 null/0 차이를 확인하고 비공개 증거를 보관했습니다.

이전: [04](04-event-grid.md) · 다음: [06. 정리](06-cleanup-production.md)
