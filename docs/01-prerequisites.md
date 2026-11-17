# 01. 실습 준비

> 권한·정책·비용을 확인하고, 다음 장에서도 복구할 수 있는 실행 상태를 만듭니다.

이전: [00. 개념](00-azure-rbac-basics.md) · [홈](../README.md) · 다음: [02. 인프라](02-private-infrastructure.md)

## 목표

- 독립 구독의 권한·공급자·지역·상속 정책을 확인합니다.
- 기존 시스템 토픽 충돌 없이 실행 상태를 한 번만 초기화합니다.
- 새 Cloud Shell에서도 기존 `STATE_FILE`과 공통 함수를 복구합니다.

## 예상 소요 시간

15분. 공급자 등록이나 권한 조치가 필요하면 실습 전에 별도로 완료합니다.

## 시작 전 상태

- 승인된 구독·테넌트 ID를 알고 있으며 Cloud Shell **Bash**를 열 수 있습니다.
- 비용과 정리 시간을 승인받았고, 이번 실행의 RG는 아직 만들지 않았습니다.
- 중단한 실습을 재개한다면 새 초기화 대신 3절의 상태 복구를 사용합니다.

## 1. 환경과 권한

👁️ **설명**

Azure Public Cloud의 **독립 실습 구독**과 Portal의 **Cloud Shell Bash**를 사용합니다. PowerShell 탭에 Bash 명령을 붙이지 않습니다. Python 3.11 이상과 Azure CLI·Bicep·Git이 필요합니다. 로컬 Windows 개발은 [별도 안내](development.md)를 사용합니다.

권장 권한은 실습 구독의 **Owner**입니다. Contributor와 User Access Administrator 또는 Role Based Access Control Administrator를 조합할 수도 있지만, 상속·조건을 포함하여 리소스 배포, 구독 소스 Event Grid 관리, `Microsoft.Authorization/roleAssignments/write` 및 `delete`, 조회 권한이 모두 유효해야 합니다. Contributor만으로는 Storage·텔레메트리 역할과 시험 Reader를 만들 수 없습니다.

사용자형 CLI 로그인을 사용합니다. 도구가 자기 프로필 확인을 위해 `az ad signed-in-user show`를 읽지만 Graph 애플리케이션 권한이나 `User.Read.All`을 부여하지는 않습니다.

🟢 **실행**

Portal → **Cloud Shell → Bash**를 엽니다. 아래 질문에는 사전에 승인받은 ID를 입력합니다. 기존 기본 구독은 바꾸지 않습니다.

```bash
read -r -p "실습 구독 ID: " SUB_ID
read -r -p "실습 테넌트 ID: " TENANT_ID
LOCATION=koreacentral
az account show --subscription "$SUB_ID" --output table
az cloud show --query name --output tsv
az version
python3 --version
az bicep version
az functionapp list-flexconsumption-locations --subscription "$SUB_ID" --output table
```

`LOCATION`은 Python 3.11 Flex Consumption과 필요한 네트워크 기능을 지원하는 승인 지역으로 선택합니다. 한국 중부를 사용할 수 없으면 **배포 전에** 값을 바꿉니다.

<!-- prerequisite-contract: providers -->
```bash
for provider in Microsoft.Web Microsoft.App Microsoft.Storage Microsoft.Network Microsoft.Insights \
                Microsoft.OperationalInsights Microsoft.EventGrid; do
  az provider show --namespace "$provider" --subscription "$SUB_ID" \
    --query '{namespace:namespace,state:registrationState}' --output table
done
az policy assignment list --subscription "$SUB_ID" \
  --scope "/subscriptions/$SUB_ID" --output table
```

📋 **확인**

계정·테넌트가 승인한 값이고 cloud는 `AzureCloud`인지 확인합니다. 리소스 공급자는 `Registered`여야 합니다. 특히 Flex Consumption VNet 통합에는 **`Microsoft.App` 등록**도 필요합니다([공식 요건](https://learn.microsoft.com/azure/azure-functions/flex-consumption-how-to#configure-virtual-network-integration)). 미등록·권한 오류는 관리자가 해소한 뒤 진행합니다. 마지막 CLI 정책 목록만으로 관리 그룹의 상속 정책까지 모두 검토했다고 판단하지 않습니다.

Portal → **정책 → 할당**에서 구독과 상위 관리 그룹의 상속 정책까지 검토합니다. 허용 지역, 필수 태그, Private Endpoint 승인, Storage 공개 접근 차단, 조직별 VNet CIDR 제약을 확인합니다. 전용 VNet은 `10.76.0.0/16`이며 다른 VNet과 피어링하지 않습니다.

⚠️ **주의**

Private Endpoint 두 개·Private DNS 두 개·Storage·로그는 유휴 상태에서도 비용을 발생시킬 수 있습니다. 예산과 정리 시간을 먼저 승인받습니다. what-if도 실제 정책 적용 성공이나 가용 용량을 보장하지 않습니다. 정책·공유 키 제한을 해제해 우회하지 않습니다.

## 2. 저장소와 새 실행 만들기

👁️ **설명**

보조 도구는 표준 라이브러리만 사용합니다. Cloud Shell에서 수신기용 `pip install`은 필요하지 않습니다. Function 의존성은 03의 Linux 원격 빌드에서 설치합니다.

🟢 **실행**

```bash
git clone https://github.com/freeman9844/event-rbac-test-01.git &&
  cd event-rbac-test-01
```

clone과 디렉터리 이동이 성공한 뒤 실행합니다.

```bash
initialize_workshop() {
  local created
  created=$(python3 scripts/workshop.py init --subscription "$SUB_ID" \
    --tenant "$TENANT_ID" --location "$LOCATION") || return
  STATE_FILE=$created
  export STATE_FILE
  python3 scripts/workshop.py show "$STATE_FILE"
}
initialize_workshop
```

📋 **확인**

`.workshop-state/<실행ID>.json`에 구독·테넌트·자기 Object ID, 전용 RG, 고유 배포 이름이 기록됩니다. `outputs={}`, `assignment=null`이 초기 상태입니다. 시스템 토픽은 **모든 페이지**를 조회하며 같은 구독 소스가 있으면 초기화를 중단합니다.

⚠️ **주의**

`init`을 반복하여 새 실행을 만들지 않습니다. 상태 파일을 잃었다고 구독 전체의 RG·토픽·역할을 검색해 일괄 삭제하지 않습니다. 상태와 증거에는 실제 환경 ID가 있으므로 Git·이슈·공개 채팅에 올리지 않습니다.

## 3. 상태 복구와 공통 함수

👁️ **설명**

이후 각 장은 같은 Bash 세션과 아래 함수를 사용합니다. Cloud Shell 세션이 바뀌면 저장소로 이동하고, **기존** 상태 파일을 선택한 뒤 함수를 다시 정의합니다. 영구 저장소를 연결하지 않은 Cloud Shell은 세션 종료 때 파일이 사라질 수 있으므로 승인된 개인 저장소에 비공개로 보관합니다.

🟢 **실행**

복구할 때만 파일 목록에서 정확한 실행 하나를 고릅니다. 새 실행을 방금 만들었다면 `STATE_FILE`을 다시 설정하지 않습니다.

```bash
find .workshop-state -maxdepth 1 -type f -name '*.json' -print
# 복구할 때만 아래 자리 표시자를 실제 기존 파일로 바꿉니다.
# STATE_FILE=.workshop-state/기존실행ID.json
```

아래 블록은 신규·복구 실행 모두에서 한 번 정의합니다. 상태를 `eval` 또는 `source`로 실행하지 않습니다.

```bash
state_value() {
  python3 scripts/workshop.py show "$STATE_FILE" |
    python3 -c '
import json, sys
value = json.load(sys.stdin)
for key in sys.argv[1].split("."):
    value = value[key]
if not isinstance(value, str):
    raise TypeError("Expected a string state value")
print(value)
' "$1"
}
restore_workshop() {
  if [[ -z "${STATE_FILE:-}" || ! -f "$STATE_FILE" ]]; then
    printf '%s\n' "기존 STATE_FILE을 먼저 선택하세요." >&2
    return 1
  fi
  python3 scripts/workshop.py check "$STATE_FILE" || return
  SUB_ID=$(state_value subscriptionId) || return
  TENANT_ID=$(state_value tenantId) || return
  RG=$(state_value resourceGroupName) || return
  RUN_ID=$(state_value runId) || return
  LOCATION=$(state_value location) || return
  PREFIX=$(state_value namePrefix) || return
  INFRA_DEPLOYMENT=$(state_value deploymentNames.infra) || return
  EVENTS_DEPLOYMENT=$(state_value deploymentNames.events) || return
}
restore_workshop
```

📋 **확인**

`PASS: run identity and ownership checked.`가 나와야 합니다. 다른 사용자·테넌트·RG 소유 태그가 발견되면 중단합니다. 첫 배포 전 RG가 없는 것은 정상이며, 이미 존재하는 다른 RG를 소유한 것으로 취급하지 않습니다.

각 변경 블록은 함수 내부에서 검사 실패 시 `return`합니다. 참가자 셸에 전역 `set -e`나 강제 `exit`를 적용하지 않습니다. 한 블록이 실패하면 원인을 해결할 때까지 다음 장으로 진행하지 않습니다.

## 트러블슈팅

| 증상 | 확인과 다음 행동 |
|---|---|
| Contributor인데 역할 할당이 거부됩니다 | RBAC 생성·삭제 권한이 부족합니다. Owner 또는 동등한 유효 권한을 관리자에게 확인합니다. |
| 같은 구독 소스 시스템 토픽이 있습니다 | 기존 토픽을 변경하지 말고 별도 독립 실습 구독을 준비합니다. |
| `Microsoft.App` 등이 미등록입니다 | 승인된 관리자가 등록한 후 상태를 재조회합니다. 실습 도구가 정책이나 등록 권한을 우회하지 않습니다. |
| signed-in user 조회가 실패합니다 | 사용자형 CLI 로그인과 자기 프로필 읽기 권한을 확인합니다. Graph 애플리케이션 권한을 새로 부여하는 절차가 아닙니다. |
| Cloud Shell을 다시 열어 변수가 없습니다 | 기존 상태 파일을 선택하고 3절의 함수를 다시 정의합니다. `init`을 반복하지 않습니다. |
| 상태 파일·소유 태그가 일치하지 않습니다 | 다른 실행을 선택했는지 확인하고 중단합니다. 상태나 태그를 억지로 바꿔 통과시키지 않습니다. |

## 완료 체크

- [ ] 비용·권한·상속 정책과 독립 구독을 확인했습니다.
- [ ] 새 실행을 한 번만 만들고 상태 파일을 보관했습니다.
- [ ] 공통 함수를 정의하고 `restore_workshop` 검사를 통과했습니다.

이전: [00](00-azure-rbac-basics.md) · 다음: [02. 인프라](02-private-infrastructure.md)
