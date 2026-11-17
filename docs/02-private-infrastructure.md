# 02. 사설 Storage 인프라 배포

> 소유 태그가 있는 전용 RG에 Function과 사설 Storage 경로를 배포합니다.

이전: [01. 준비·상태 복구](01-prerequisites.md) · [홈](../README.md) · 다음: [03. 코드 게시](03-function-deployment.md)

## 목표

- 이번 실행의 새 RG를 만들고 what-if에서 변경 범위를 검토합니다.
- 구독 Reader를 만들지 않는 워크숍 매개변수로 배포하고 출력을 보관합니다.
- Storage 차단·PE 승인·DNS 링크·Function VNet 통합을 확인합니다.

## 예상 소요 시간

25분. 인프라 배포와 Private Endpoint 준비에 따라 늘어날 수 있습니다.

## 시작 전 상태

- Module 01을 마쳤고 `STATE_FILE`·공통 함수를 복구할 수 있습니다.
- 신규 실행이면 RG를 아직 생성하지 않았습니다. 이미 만든 RG를 재개한다면 생성 블록을 건너뜁니다.
- 상속 정책, 필수 태그와 전용 VNet `10.76.0.0/16` 사용을 검토했습니다.

## 1. 전용 RG 만들기

👁️ **설명**

먼저 01의 `STATE_FILE`·공통 함수를 복구합니다. 태그가 있는 **새 RG**를 명시적으로 만든 뒤 Bicep에는 `createResourceGroup=false`를 전달하여 기존 태그를 건드리지 않습니다. 수신 전용 Function에는 구독 Reader가 필요 없어 `assignSubscriptionReader=false`도 항상 전달합니다.

🟢 **실행**

<!-- command-contract: create-group -->
```bash
create_workshop_group() {
  restore_workshop || return
  local exists
  exists=$(az group exists --subscription "$SUB_ID" --name "$RG" --output tsv) || return
  if [[ "$exists" != "false" ]]; then
    printf '%s\n' "RG가 이미 있거나 존재 여부가 불명확합니다. 생성은 중단합니다." >&2
    return 1
  fi
  az group create --subscription "$SUB_ID" --name "$RG" --location "$LOCATION" \
    --tags workshop=rbac-event "runId=$RUN_ID" managedBy=workshop --output none
}
create_workshop_group
```

📋 **확인**

Portal → **리소스 그룹 → 이번 RG → 태그**에서 세 태그를 확인합니다. 정책이 추가한 태그는 허용하지만 세 소유 태그를 변경하면 보조 도구가 중단합니다. RG가 이미 있는 동일 실행을 재개할 때는 이 생성 블록을 반복하지 말고 `restore_workshop` 통과 후 다음 단계부터 재개합니다.

⚠️ **주의**

Bicep의 기본값은 기존 PoC 호출 호환성을 위해 RG 생성·구독 Reader 부여를 유지합니다. **두 false 옵션을 생략하지 마세요.** 필수 정책 태그가 더 있다면 관리자와 검토하여 이 새 RG 생성 명령에 추가하며 기존 RG를 덮어쓰지 않습니다.

## 2. 변경 예상 확인

👁️ **설명**

구독 배포가 RG 안에 Function Flex Consumption/Python 3.11, Storage, Workspace·Application Insights, 비연결 VNet, Blob·Queue PE와 DNS를 만듭니다. Storage·텔레메트리용 관리 ID 역할은 해당 리소스 범위에 필요합니다. Function의 **구독 Reader만** 제외합니다.

🟢 **실행**

<!-- command-contract: infra-preview -->
```bash
preview_infrastructure() {
  restore_workshop || return
  az deployment sub what-if --subscription "$SUB_ID" --name "$INFRA_DEPLOYMENT" \
    --location "$LOCATION" --template-file infra/main.bicep \
    --parameters "resourceGroupName=$RG" "location=$LOCATION" "namePrefix=$PREFIX" \
      createResourceGroup=false assignSubscriptionReader=false enableEventSubscription=false
}
preview_infrastructure
```

📋 **확인**

생성 대상이 이번 RG 안에만 있는지, 구독 범위 Reader가 없는지 검토합니다. `enableEventSubscription=false`이므로 아직 시스템 토픽·이벤트 구독을 연결하지 않습니다. 예상 밖 변경·삭제·공유 네트워크 수정이 보이면 중단합니다.

⚠️ **주의**

what-if 출력의 미확정 식이나 정책 효과는 관리자가 확인해야 합니다. `false`는 이미 만들어진 이벤트 연결을 제거하는 스위치가 아닙니다. 증분 배포는 생략한 기존 리소스를 삭제하지 않습니다.

## 3. 인프라 배포와 출력 보관

🟢 **실행**

what-if를 직접 승인한 뒤에만 다음 블록을 실행합니다.

<!-- command-contract: infra-create -->
```bash
deploy_infrastructure() {
  restore_workshop || return
  az deployment sub create --subscription "$SUB_ID" --name "$INFRA_DEPLOYMENT" \
    --location "$LOCATION" --template-file infra/main.bicep \
    --parameters "resourceGroupName=$RG" "location=$LOCATION" "namePrefix=$PREFIX" \
      createResourceGroup=false assignSubscriptionReader=false \
      enableEventSubscription=false --output none || return
  python3 scripts/workshop.py capture "$STATE_FILE" --deployment "$INFRA_DEPLOYMENT"
}
deploy_infrastructure
```

📋 **확인**

`PASS: deployment outputs captured.`가 나와야 합니다. `capture`는 배포 성공, 이번 RG·이름, Function의 실제 관리 ID까지 대조합니다.

```bash
python3 scripts/workshop.py show "$STATE_FILE"
```

`outputs.subscriptionReaderAssignmentId`는 빈 문자열이어야 합니다. Portal → **구독 → 배포**에서 이번 `infra` 배포 성공과 RG 내 리소스를 확인합니다. 실패한 배포도 과금 리소스를 남길 수 있으므로 06의 부분 실패 정리를 사용합니다.

## 4. Storage·PE·DNS 확인

👁️ **설명**

Storage의 공개 네트워크·익명 Blob·공유 키 접근을 차단합니다. Function만 VNet 통합과 사설 DNS를 통해 Blob·Queue로 접근합니다. Cloud Shell에서 사설 Blob을 직접 읽지 못하는 것은 이 설계에서 정상입니다.

🟢 **실행**

```bash
inspect_private_storage() {
  restore_workshop || return
  local app suffix storage
  app=$(state_value outputs.functionAppName) || return
  suffix=${app#func-rbac-lab-}
  storage="strbac$suffix"
  az rest --subscription "$SUB_ID" --method get \
    --url "https://management.azure.com/subscriptions/$SUB_ID/resourceGroups/$RG/providers/Microsoft.Storage/storageAccounts/$storage?api-version=2023-05-01" \
    --query 'properties.{publicNetworkAccess:publicNetworkAccess,allowSharedKeyAccess:allowSharedKeyAccess,allowBlobPublicAccess:allowBlobPublicAccess,minimumTlsVersion:minimumTlsVersion}' \
    --output json || return
  az network private-endpoint list --subscription "$SUB_ID" --resource-group "$RG" \
    --query '[].{name:name,status:privateLinkServiceConnections[0].privateLinkServiceConnectionState.status}' \
    --output table || return
  for zone in privatelink.blob.core.windows.net privatelink.queue.core.windows.net; do
    az network private-dns link vnet list --subscription "$SUB_ID" \
      --resource-group "$RG" --zone-name "$zone" --output table || return
  done
}
inspect_private_storage
```

📋 **확인**

Storage는 `Disabled / false / false / TLS1_2`, 두 PE는 `Approved`, 두 DNS zone은 이번 VNet과 연결되어 있어야 합니다. Portal → **Function → 네트워킹 → VNet 통합**, **Storage → 네트워킹 → 프라이빗 엔드포인트 연결**, **Private DNS zone → 가상 네트워크 링크**에서도 확인합니다.

⚠️ **주의**

Function HTTPS 인바운드와 Application Insights 수집·조회 경로는 공개 엔드포인트입니다. 익명 HTTP 함수는 제공하지 않지만 “전체 사설”이라고 설명하지 않습니다. 운영 인바운드 사설화는 Event Grid 전달 방식까지 별도 설계해야 합니다.

## 트러블슈팅

| 증상 | 확인과 다음 행동 |
|---|---|
| what-if에 구독 범위 Reader가 보입니다 | `assignSubscriptionReader=false`가 빠지지 않았는지 확인하고 배포를 중단합니다. |
| 정책이 배포를 거부합니다 | 실패한 배포의 정책 ID·효과·관리 그룹 상속을 관리자와 검토합니다. 제한을 해제해 우회하지 않습니다. |
| `capture`가 실패합니다 | 이번 배포의 성공 여부와 상태 파일을 확인합니다. 성공한 동일 배포의 capture를 재시도하며 출력을 추정하지 않습니다. |
| PE가 Approved가 아니거나 DNS 링크가 없습니다 | 해당 연결 승인과 이번 VNet 링크를 확인하고 Module 03으로 진행하지 않습니다. |
| 배포가 실패했지만 RG에 자원이 남았습니다 | 같은 상태 파일로 [Module 06의 preview](06-cleanup-production.md)를 수행합니다. 실패를 무과금·미생성으로 해석하지 않습니다. |

## 완료 체크

- [ ] 전용 RG 태그와 두 false 옵션을 확인했습니다.
- [ ] 배포 성공·출력 capture·빈 구독 Reader ID를 확인했습니다.
- [ ] Storage 차단, PE 승인, DNS·VNet 통합을 확인했습니다.

이전: [01](01-prerequisites.md) · 다음: [03. 코드 게시](03-function-deployment.md)
