# 05A. 선택 실습: 새 사용자의 companyName 확인

> 새 사용자에 `companyName=company001`을 설정하고, 실제 RBAC 이벤트를 처리한 Function의 보강 로그와 성공 요청까지 대조합니다.

이전: [05. 기본 수신 검증](05-rbac-event-validation.md) · [홈](../README.md) · 다음: [06. 정리](06-cleanup-production.md)

## 목표

- Event Grid에 회사명이 포함되는 것이 아니라 Function이 ARM·Graph에서 보강한다는 차이를 확인합니다.
- 실행자와 다른 신규 사용자에게 이번 RG Reader 한 건만 할당합니다.
- 동일 할당·사용자·역할·scope·회사명과 성공 InvocationId/OperationId를 대조합니다.
- 신규 사용자·Graph 권한을 먼저 정리하고, 구독 Reader·RG를 Module 06에서 제거합니다.

## 예상 소요 시간

추가 30~45분. Entra 관리자 준비 시간은 별도이며 기본 과정 110분에 포함되지 않습니다.

## 시작 전 상태와 승인

기초 워크숍은 Graph 애플리케이션 권한 없이 계속 수행할 수 있습니다. **이 확장을 선택한다면 Module 01의 상태 초기화 직후 아래 관리자 사전 점검부터 수행**하고 비용 자원을 만드세요. 이미 Module 05를 마쳤다면 Module 06으로 정리하기 전에 수행합니다.

- 독립 교육용 구독의 동일 실행자 계정으로 진행합니다. Azure Owner만으로 Entra 관리자 작업을 할 수는 없습니다.
- 사용자 생성·일반 삭제와 Microsoft Graph 애플리케이션 권한 부여가 가능한 관리자 권한이 필요합니다. 지원 기본 역할은 User Administrator + Privileged Role Administrator 또는 이미 권한이 있는 Global Administrator입니다. 실습 도구는 관리자 역할을 부여하거나 활성화하지 않습니다.
- 관리자 API 호출자의 Graph 위임 권한도 필요합니다. 사전 읽기 점검이 실제 쓰기 성공을 보장하지 않으며, 거부된 쓰기는 명시적으로 실패합니다.
- 이번 Function 관리 ID에 **구독 Reader**와 **Graph `User.Read.All` 애플리케이션 권한**을 부여하는 것에 승인합니다. 후자는 테넌트 사용자 프로필 조회 권한입니다.
- 새 사용자는 로그인 불가·무라이선스이며, 비밀번호는 생성 요청의 메모리에서만 사용합니다. 기존 사용자를 수정·재사용하지 않습니다.
- 저장소 루트에서 실행하고 Module 01의 공통 함수를 복구합니다. `requests` 모듈이 없는 오류가 발생한 경우에만 Python 의존성 환경을 먼저 준비합니다.

## 1. 관리자 사전 점검

🟢 **실행**

```bash
preflight_companyname() {
  restore_workshop || return
  python3 -m scripts.companyname preflight "$STATE_FILE"
}
preflight_companyname
```

📋 **확인**

`ready=true`, 이번 테넌트의 관리형 초기 도메인과 Graph 역할 식별 결과가 나와야 합니다. 사용자·권한을 생성하는 명령이 아닙니다. 권한이 없으면 관리자에게 준비를 요청하며 기본 로그인·구독·정책을 바꾸지 않습니다.

## 2. Function 읽기 권한 추가

👁️ **설명**

Module 02~05를 마친 뒤 사용합니다. 기초 실험의 즉시 삭제 순서는 바꾸지 않습니다. 이 장의 고급 실험만 보강 완료까지 다른 신규 사용자의 역할을 유지합니다.

먼저 새 구독 Reader가 예상대로 추가되는지 검토합니다. Storage의 공개 접근·공유 키 제한을 해제하지 않습니다.

🟢 **실행**

```bash
preview_companyname_reader() {
  restore_workshop || return
  python3 -m scripts.companyname preflight "$STATE_FILE" || return
  az deployment sub what-if --subscription "$SUB_ID" --name "$EVENTS_DEPLOYMENT" \
    --location "$LOCATION" --template-file infra/main.bicep \
    --parameters "resourceGroupName=$RG" "location=$LOCATION" "namePrefix=$PREFIX" \
      createResourceGroup=false assignSubscriptionReader=true \
      enableIdentityEnrichment=false enableEventSubscription=true
}
preview_companyname_reader
```

검토한 뒤에만 다음 명령을 실행합니다.

```bash
prepare_companyname_reader() {
  restore_workshop || return
  python3 -m scripts.companyname preflight "$STATE_FILE" || return
  az deployment sub create --subscription "$SUB_ID" --name "$EVENTS_DEPLOYMENT" \
    --location "$LOCATION" --template-file infra/main.bicep \
    --parameters "resourceGroupName=$RG" "location=$LOCATION" "namePrefix=$PREFIX" \
      createResourceGroup=false assignSubscriptionReader=true \
      enableIdentityEnrichment=false enableEventSubscription=true --output none || return
  python3 scripts/workshop.py capture "$STATE_FILE" --deployment "$EVENTS_DEPLOYMENT"
}
prepare_companyname_reader
```

📋 **확인**

capture가 성공하고 기본 상태의 `outputs.subscriptionReaderAssignmentId`가 채워져야 합니다. **이 역할은 RG 삭제로 사라지지 않습니다.** 기본 과정의 false 명령으로 되돌려 출력만 비우려 하지 마세요.

## 3. 새 사용자와 명시적인 Graph 동의

🟢 **실행**

```bash
prepare_companyname_user() {
  restore_workshop || return
  python3 -m scripts.companyname prepare "$STATE_FILE" --company-name company001
}
prepare_companyname_user
```

📋 **확인**

`prepared=true`, 새 `userId`, `companyName=company001`을 확인합니다. `<runId>-companyname.json`은 기초 상태 옆에 생성되며 실행자 ID는 바뀌지 않습니다. Function에는 `User.Read.All` 읽기 권한만 부여하고 사용자 생성·수정·삭제 권한은 부여하지 않습니다.

이 단계가 실패하면 정확한 요청 의도로 신규 사용자·Graph 권한 정리를 시도하고 원래 실패를 보고합니다. 정리도 실패했다면 같은 상태의 `cleanup`으로 복구하며 새 사용자를 반복 생성하지 않습니다.

## 4. 보강 활성화

🟢 **실행**

```bash
enable_companyname_observation() {
  restore_workshop || return
  az deployment sub create --subscription "$SUB_ID" --name "$EVENTS_DEPLOYMENT" \
    --location "$LOCATION" --template-file infra/main.bicep \
    --parameters "resourceGroupName=$RG" "location=$LOCATION" "namePrefix=$PREFIX" \
      createResourceGroup=false assignSubscriptionReader=true \
      enableIdentityEnrichment=true enableEventSubscription=true --output none || return
  python3 scripts/workshop.py capture "$STATE_FILE" --deployment "$EVENTS_DEPLOYMENT"
}
enable_companyname_observation
```

📋 **확인**

배포 성공, 동일 관리 ID와 기록된 Reader를 확인합니다. 이 설정은 `ENABLE_IDENTITY_ENRICHMENT=true`를 전달합니다. Graph 권한 준비 전에 활성화하거나 기초 `test-event`를 재실행하지 않습니다.

## 5. 실제 보강 로그와 성공 요청 검증

🟢 **실행**

```bash
exercise_companyname() {
  restore_workshop || return
  python3 -m scripts.companyname exercise "$STATE_FILE" --timeout-seconds 900
}
exercise_companyname
```

📋 **확인**

`verified=true`와 `function.companyName=company001`을 확인합니다. 비공개 `<runId>/companyname-verification.json`에는 Activity 쓰기 성공, 보강 로그, 동일 성공 요청의 실행 ID와 시각이 남습니다. `RbacRoleAssignmentReceived`만으로는 성공하지 않습니다.

관측 메시지의 핵심 **형식**은 다음과 같습니다. 실제 ID는 공개하지 않습니다.

```json
{
  "eventName": "RbacRoleAssignmentObserved",
  "principalId": "<새 사용자 Object ID>",
  "principalType": "User",
  "roleName": "Reader",
  "scope": "/subscriptions/<구독 ID>/resourceGroups/<이번 RG>",
  "companyName": "company001",
  "enrichmentStatus": "succeeded"
}
```

Workspace의 `AppTraces`에서 `parse_json(Message)`로 이 필드를 조회하고, `AppRequests`의 같은 `OperationId`와 `Properties.InvocationId`, `Success=true`를 대조합니다. Application Insights 범위에서는 `traces/requests`와 열 이름이 다릅니다.

이 값은 **Graph 조회 시점의 프로필**이며 과거 할당 시점의 확정 회사명이나 신뢰할 수 있는 회사 인증을 뜻하지 않습니다. null·빈 문자열은 `companyNameNotSet`, 비사용자는 `notApplicable`이며 403·404를 미설정으로 바꾸지 않습니다.

## 6. 고급 정리 후 Module 06

👁️ **설명**

`exercise`는 성공·시간 초과·중단에서도 이번 테스트 Reader → 이번에 추가한 Graph 권한 → 신규 사용자를 정리합니다. 기존 Graph 권한이 있었다면 보존하고 결과에 명시합니다. 사용자 삭제는 일반 삭제이며 영구 삭제하지 않습니다.

🟢 **실행**

준비 실패·중단 또는 정리 실패가 있었다면 재실행할 수 있습니다.

```bash
cleanup_companyname() {
  restore_workshop || return
  python3 -m scripts.companyname cleanup "$STATE_FILE"
}
cleanup_companyname
```

📋 **확인**

`complete=true`와 비공개 `companyname-cleanup.json`을 확인합니다. 이후 즉시 [Module 06](06-cleanup-production.md)의 preview·확인 문구로 **Function 구독 Reader와 전용 RG까지** 제거합니다. 고급 정리 오류를 무시하고 RG부터 삭제하지 않습니다.

## 트러블슈팅

| 증상 | 확인과 복구 |
|---|---|
| 관리자 사전 점검 실패 | 현재 활성 Entra 역할·테넌트·동일 실행자 확인. Azure Owner와 Graph 권한을 구분 |
| 사용자 생성 또는 Graph 동의 403 | 호출자 위임 권한과 관리자 역할 확인. Function에 쓰기 권한을 부여하지 않음 |
| Function `stage=user`, HTTP 403 | 관리 ID의 Graph 애플리케이션 권한·전파 확인. 운영자 조회 성공으로 대체하지 않음 |
| 역할 할당 404 | 생성 전파 또는 너무 이른 삭제 여부 확인. 조회 실패를 미설정으로 처리하지 않음 |
| 조회 시간 초과 | 증거와 Function 오류 확인. 자동 정리 결과 확인 후 필요하면 새 실행으로 시작 |
| 소유권 불일치·잔여 항목 | 원본 상태를 보존하고 관리자와 정확한 ID 대조. 임의 상태 수정·이름 검색 일괄 삭제 금지 |

## 검증 상태

새 보강·실험 경로의 로컬 테스트는 실제 Azure 호출 없이 수행합니다. 이 절차의 실제 Azure 실측 결과는 실행 증거가 확보된 경우에만 별도로 기록합니다. [기존 수신 전용 PoC 실측](05-rbac-event-validation.md#4-실제-관측-예시)을 회사명 검증 결과로 재사용하지 않습니다.

## 완료 체크

- [ ] 새 사용자가 실행자와 다르고 로그인 불가·무라이선스인지 확인했습니다.
- [ ] 실제 보강 로그의 `company001`, 대상·역할·scope와 같은 성공 요청을 대조했습니다.
- [ ] 고급 정리 완료 후 Module 06에서 구독 Reader와 RG 부재까지 확인했습니다.

이전: [05](05-rbac-event-validation.md) · 다음: [06. 정리](06-cleanup-production.md)
