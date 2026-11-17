# 04. Event Grid 연결

> 코드 게시가 끝난 Function에 역할 할당 쓰기 성공만 전달하도록 연결합니다.

이전: [03. 코드 게시](03-function-deployment.md) · [홈](../README.md) · 다음: [05. 로그 검증](05-rbac-event-validation.md)

## 목표

- 실제 함수 등록을 다시 확인하고 이벤트 연결의 what-if를 검토합니다.
- source·이벤트 유형·작업명 필터·Function destination을 대조합니다.
- 이벤트 연결 성공과 실제 RBAC 수신 검증을 구분합니다.

## 예상 소요 시간

10분

## 시작 전 상태

- Module 03에서 `RbacAssignmentObserver`의 실제 등록을 확인했습니다.
- 이번 실행 상태와 배포 출력을 복구할 수 있습니다.
- 다른 실행이나 기존 모니터링의 시스템 토픽을 재사용하지 않습니다.

## 1. 게시 확인과 변경 예상

👁️ **설명**

01의 공통 함수를 복구하고, 03을 완료한 뒤 진행합니다. 코드 게시 전 destination을 연결하지 않습니다. 새 구독 소스 시스템 토픽은 이 실행의 전용 RG 안에 생성됩니다.

🟢 **실행**

먼저 게시 확인 함수를 정의합니다. 이후 두 배포 블록이 이 검사를 다시 수행합니다.

```bash
check_published_receiver() {
  restore_workshop || return
  local app registered
  app=$(state_value outputs.functionAppName) || return
  registered=$(az functionapp function list --subscription "$SUB_ID" \
    --resource-group "$RG" --name "$app" \
    --query "[?ends_with(name, '/RbacAssignmentObserver')].name" --output tsv) || return
  if [[ "$registered" != "$app/RbacAssignmentObserver" ]]; then
    printf '%s\n' "정확한 수신 함수가 등록되지 않았습니다. 03을 확인하세요." >&2
    return 1
  fi
}
```

<!-- command-contract: events-preview -->
```bash
preview_events() {
  check_published_receiver || return
  az deployment sub what-if --subscription "$SUB_ID" --name "$EVENTS_DEPLOYMENT" \
    --location "$LOCATION" --template-file infra/main.bicep \
    --parameters "resourceGroupName=$RG" "location=$LOCATION" "namePrefix=$PREFIX" \
      createResourceGroup=false assignSubscriptionReader=false enableEventSubscription=true
}
preview_events
```

📋 **확인**

이번 시스템 토픽과 `rbac-role-assignment-writes` 이벤트 구독 추가가 예상됩니다. Function·Storage·VNet의 의도하지 않은 변경이나 구독 Reader 추가가 보이면 중단합니다. 배포는 동일 템플릿을 재평가하므로 “이벤트 두 개만 생성한다”고 단정하지 않습니다.

⚠️ **주의**

초기화 후 다른 시스템 토픽이 생겨 충돌하면 기존 토픽을 재사용·수정·삭제하지 않습니다. 이번 실행을 정리하고 별도 실습 구독으로 이동합니다.

## 2. 이벤트 연결

🟢 **실행**

what-if를 검토한 뒤 실행합니다. 두 false 옵션은 그대로 유지합니다.

<!-- command-contract: events-create -->
```bash
connect_events() {
  check_published_receiver || return
  az deployment sub create --subscription "$SUB_ID" --name "$EVENTS_DEPLOYMENT" \
    --location "$LOCATION" --template-file infra/main.bicep \
    --parameters "resourceGroupName=$RG" "location=$LOCATION" "namePrefix=$PREFIX" \
      createResourceGroup=false assignSubscriptionReader=false \
      enableEventSubscription=true --output none || return
  python3 scripts/workshop.py capture "$STATE_FILE" --deployment "$EVENTS_DEPLOYMENT"
}
connect_events
```

📋 **확인**

배포와 capture가 성공해야 합니다. Portal → **Event Grid 시스템 토픽 → 이번 토픽 → 이벤트 구독 → rbac-role-assignment-writes**에서 다음을 확인합니다.

| 항목 | 기대값 |
|---|---|
| source / topic type | 이번 구독 전체 ID / `Microsoft.Resources.Subscriptions` |
| 이벤트 유형 | `Microsoft.Resources.ResourceWriteSuccess` |
| 고급 필터 | `data.operationName` / `StringIn` / `Microsoft.Authorization/roleAssignments/write` |
| 스키마 / destination 유형 | `EventGridSchema` / `AzureFunction` |
| destination resource ID | 이번 Function의 `/functions/RbacAssignmentObserver` |
| 배치 / 재시도 / TTL | 최대 배치 1 / 최대 시도 30 / 1440분 |

아래는 ARM 원본 구조를 읽는 확인 명령입니다.

```bash
inspect_event_route() {
  restore_workshop || return
  local topic
  topic=$(state_value outputs.systemTopicName) || return
  az rest --subscription "$SUB_ID" --method get \
    --url "https://management.azure.com/subscriptions/$SUB_ID/resourceGroups/$RG/providers/Microsoft.EventGrid/systemTopics/$topic/eventSubscriptions/rbac-role-assignment-writes?api-version=2025-02-15" \
    --query 'properties.{filter:filter,destination:destination,retryPolicy:retryPolicy,eventDeliverySchema:eventDeliverySchema}' \
    --output json
}
inspect_event_route
```

⚠️ **주의**

삭제 이벤트는 이 필터의 대상이 아닙니다. 재시도 30회는 무손실 보장이 아니며, 이 교재에는 dead-letter·재처리 큐가 없습니다. 연결이 생겼다는 것만으로 실제 RBAC 이벤트 지원 검증을 대신하지 않습니다.

## 트러블슈팅

| 증상 | 확인과 다음 행동 |
|---|---|
| Function 등록 검사에서 중단됩니다 | Module 03의 함수 등록과 호스트 오류를 먼저 확인합니다. 목적지를 먼저 만들지 않습니다. |
| 기존 시스템 토픽 충돌로 연결이 실패합니다 | 다른 실행의 토픽을 변경하지 않습니다. 이번 실행을 정리하고 독립 실습 구독을 준비합니다. |
| 이벤트 유형만 있고 작업명 필터가 없습니다 | `inspect_event_route`에서 `data.operationName`·`StringIn`·정확한 값을 확인합니다. 실제 수신 실험 전 이번 배포 설정을 바로잡습니다. |
| 연결은 성공했지만 이벤트가 아직 없습니다 | 연결 성공은 수신 검증이 아닙니다. Module 05에서 실제 임시 Reader 할당과 로그를 대조합니다. |

## 완료 체크

- [ ] 정확한 Function 등록 확인 후 이벤트를 연결했습니다.
- [ ] source·유형·고급 필터·destination·retry를 확인했습니다.
- [ ] 구독 Reader 비활성과 현재 실행 출력을 유지했습니다.

이전: [03](03-function-deployment.md) · 다음: [05. 로그 검증](05-rbac-event-validation.md)
