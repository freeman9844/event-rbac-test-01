# 구독 RBAC 역할 할당과 사용자 회사 정보 확인

Azure Subscription의 역할 할당 쓰기 성공 이벤트를 Event Grid로 받아 Azure Function에서 처리하는 검증 프로젝트입니다.

**현재 구현은 이벤트 수신 및 입력 검증까지입니다.** 실제 구독의 RBAC 이벤트 수신을 먼저 입증한 후 ARM 역할 조회와 Microsoft Graph `companyName` 조회를 추가합니다. 아직 사용자 속성 조회나 종단 간 검증이 완료된 상태가 아닙니다.

## 승인된 범위

- 이벤트: `Microsoft.Resources.ResourceWriteSuccess`
- 작업명 필터: `data.operationName`이 `Microsoft.Authorization/roleAssignments/write`
- 대상 범위: 구독, 하위 리소스 그룹, 개별 리소스의 직접 할당
- 최종 확인 항목: 직접 할당 대상, 역할, 적용 범위, 사용자의 `companyName`
- 결과 확인 장소: Application Insights

그룹 구성원 확장, 삭제 이벤트, Microsoft Entra 디렉터리 역할, PIM 전체 수명주기, 관리 그룹 할당의 하위 구독 감지는 제외합니다.

## 로컬 검증

Python 3.11을 사용합니다. 가상환경과 의존성은 작업 폴더 안에 준비합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -p "test_*.py" -v
```

Windows ARM64에서 네이티브 의존성의 휠이 없다면 x64 Python 3.11을 사용합니다. 테스트는 실제 Azure API를 호출하지 않습니다.

## 배포 전 필수 확인

배포에는 비용과 권한 변경이 발생합니다. 다음 값과 작업 승인을 받은 뒤에만 진행합니다.

| 항목 | 확인 내용 |
|---|---|
| 구독 및 테넌트 | 배포와 감시를 허용한 정확한 ID |
| 지역 | Flex Consumption과 Python 3.11을 지원하는 지역 |
| 리소스 그룹 | 이 검증 전용의 새 그룹 이름 |
| 이름 접두사 | 영문 소문자와 숫자, 하이픈으로 된 3~16자 |
| 테스트 사용자 | 역할 할당을 허용한 사용자 Object ID |
| 테스트 범위 | Reader 임시 할당을 허용한 리소스 그룹 또는 리소스 ID |
| 비용 및 권한 | Function, Storage, Application Insights 및 Workspace 생성과 관리 ID의 구독 Reader 부여 |
| 사설 Storage 네트워크 | 전용 VNet, Blob·Queue Private Endpoint 및 Private DNS 추가 비용 |

배포 수행자는 리소스 생성, 구독 수준 역할 부여, 구독 소스의 Event Grid 이벤트 구독 생성 권한이 필요합니다. Graph `User.Read.All` 부여는 이후 별도 관리자 단계이며 초기 인프라 배포가 자동으로 부여하지 않습니다.

이 PoC는 전용 VNet의 위임된 `functions` 서브넷에 Function을 통합하고, 별도 서브넷의 Blob·Queue Private Endpoint와 Private DNS를 통해 Storage에 접근합니다. Storage의 공개 네트워크, 익명 Blob 및 공유 키 접근은 끕니다. Function의 공개 HTTPS 엔드포인트는 네이티브 Event Grid 트리거 인증으로 보호하고 익명 HTTP 함수를 제공하지 않습니다. Function 인바운드까지 사설화해야 하는 조직에는 추가 설계가 필요합니다. Private Endpoint와 Private DNS에는 별도 비용이 발생합니다.

## 단계별 배포

필요한 도구는 Azure CLI와 Bicep입니다. 코드 게시에는 Azure Functions Core Tools v4 또는 Azure CLI의 ZIP 원격 빌드를 사용합니다. 기존 로그인 계정을 임의로 바꾸지 않고 승인된 구독을 각 명령에 명시합니다.

```powershell
az functionapp list-flexconsumption-locations -o table
az bicep build --file .\infra\main.bicep
```

`infra\main.bicep`은 구독 범위 템플릿입니다. 전용 리소스 그룹, Linux Flex Consumption Function, 사설 Storage 네트워크, Application Insights 및 Workspace를 만듭니다. Function 관리 ID에는 구독 Reader, 호스트 Storage 접근 역할과 텔레메트리 게시 역할을 부여합니다. Storage 공개 네트워크·공유 키·익명 Blob 접근과 Application Insights 로컬 인증은 비활성화합니다.

다음 값은 예시 자리 표시자입니다. 실제 승인된 값으로 바꾸고 실행합니다. `namePrefix`는 최초 배포 이후 유지합니다.

```powershell
$subscriptionId = '<승인된 구독 ID>'
$tenantId = '<승인된 테넌트 ID>'
$resourceGroupName = '<신규 검증 전용 리소스 그룹>'
$location = '<지원되는 승인 지역>'
$prefix = 'rbac-test'

$accountTenant = az account show --subscription $subscriptionId --query tenantId -o tsv
if ($LASTEXITCODE -ne 0) { throw '대상 구독에 접근할 수 없습니다.' }
if ($accountTenant -ne $tenantId) { throw '승인된 테넌트와 로그인 테넌트가 다릅니다.' }

az policy assignment list --subscription $subscriptionId --scope "/subscriptions/$subscriptionId" --disable-scope-strict-match true --filter '"atScope()"' --query "[].{name:name,scope:scope,definition:policyDefinitionId}" -o table
if ($LASTEXITCODE -ne 0) { throw '상속된 정책을 포함한 배포 정책 확인에 실패했습니다.' }

$parameters = @(
    "resourceGroupName=$resourceGroupName"
    "location=$location"
    "namePrefix=$prefix"
)

az deployment sub what-if --subscription $subscriptionId --location $location --template-file .\infra\main.bicep --parameters $parameters enableEventSubscription=false
if ($LASTEXITCODE -ne 0) { throw 'what-if 검증에 실패했습니다.' }
```

what-if에 예상한 리소스 생성 및 역할 부여만 있는지 검토한 뒤 배포합니다. 구독 기본 선택을 변경하는 `az account set`은 사용하지 않습니다.

관리 그룹에서 상속된 `modify` 정책은 what-if가 성공해도 실제 설정을 바꿀 수 있습니다. 최초 공개 Storage 구성은 이 정책 때문에 코드 게시가 403으로 실패했고, 사용자 승인 후 현재의 사설 Storage 구성으로 변경했습니다. 코드 게시 전 Private Endpoint 연결 승인과 DNS VNet 연결을 확인합니다. 공유 키를 켜거나 조직 정책을 우회하지 않습니다.

```powershell
$deployment = az deployment sub create --subscription $subscriptionId --location $location --template-file .\infra\main.bicep --parameters $parameters enableEventSubscription=false -o json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw '인프라 배포에 실패했습니다.' }
$appName = $deployment.properties.outputs.functionAppName.value

func azure functionapp publish $appName --subscription $subscriptionId
if ($LASTEXITCODE -ne 0) { throw 'Function 게시에 실패했습니다.' }

az functionapp function list --subscription $subscriptionId --resource-group $resourceGroupName --name $appName -o table
if ($LASTEXITCODE -ne 0) { throw 'Function 등록 확인에 실패했습니다.' }
```

`RbacAssignmentObserver` 등록을 확인한 후에만 이벤트를 연결합니다. 관리 ID의 권한 전파가 지연되면 원인을 확인하고 전파 후 다시 게시하며, 공유 키를 켜서 우회하지 않습니다.

기존 Function에 VNet을 나중에 연결한 경우, 연결·DNS·역할이 정상인데도 이전 배포 환경에서 Storage 403이 지속될 수 있습니다. 이번 새 테스트 앱은 VNet 통합 후 재시작하고 같은 ZIP을 다시 게시해 성공했습니다. 운영 앱 재시작은 중단 영향을 별도로 검토합니다.

Core Tools가 없다면 프로젝트 루트의 `host.json`, `function_app.py`, `requirements.txt`와 `rbac_monitor`의 Python 소스만 ZIP 루트에 패키징한 후 다음 명령으로 대신 게시할 수 있습니다. 가상환경, 로컬 설정, 테스트나 문서는 포함하지 않습니다. Windows용 설치 패키지를 업로드하지 않도록 Python은 항상 Linux 원격 빌드를 요청합니다.

```powershell
az functionapp deployment source config-zip --subscription $subscriptionId --resource-group $resourceGroupName --name $appName --src '<수신기 소스 ZIP 경로>' --build-remote true
if ($LASTEXITCODE -ne 0) { throw 'Function ZIP 원격 빌드 게시에 실패했습니다.' }
```

```powershell
az deployment sub what-if --subscription $subscriptionId --location $location --template-file .\infra\main.bicep --parameters $parameters enableEventSubscription=true
if ($LASTEXITCODE -ne 0) { throw '이벤트 연결 what-if에 실패했습니다.' }

az deployment sub create --subscription $subscriptionId --location $location --template-file .\infra\main.bicep --parameters $parameters enableEventSubscription=true
if ($LASTEXITCODE -ne 0) { throw '이벤트 연결 배포에 실패했습니다.' }
```

`enableEventSubscription=false`는 새 이벤트 연결 생성을 생략하는 설정입니다. 증분 배포이므로 이미 연결한 이벤트 구독을 끄거나 삭제하지 않습니다.

## 첫 번째 실제 이벤트 검증 관문

1. 수신 Function을 게시하고 함수 이름 `RbacAssignmentObserver`가 등록됐는지 확인합니다.
2. 코드 게시 이후에만 Event Grid 이벤트 구독을 연결합니다.
3. 테스트 사용자에게 같은 범위의 직접 Reader 할당이 이미 있는지 확인합니다. 기존 할당을 삭제하지 않습니다.
4. 승인된 범위에 GUID 이름의 임시 Reader 할당을 생성하고 생성 성공한 할당 ID를 별도로 보관합니다.
5. Activity Log에서 `Microsoft.Authorization/roleAssignments/write`의 성공을 확인합니다.
6. Event Grid 전달 지표 및 Application Insights에서 해당 할당 ID의 수신 로그를 확인합니다.
7. 기록된 `eventId`, 역할 할당 ID, 실제 소스 필드가 일치하는지 확인합니다. 전체 이벤트나 claims를 로그에 추가하지 않습니다.
8. 생성 성공한 테스트 할당 ID만 제거합니다. 검증 전에 할당을 삭제하면 향후 ARM 보강 조회가 404로 실패할 수 있습니다.

현재 수신 로그는 `eventName=RbacRoleAssignmentReceived`이며 최종 결과인 `RbacRoleAssignmentObserved`와 다릅니다.

테스트 할당 생성 예제입니다. `$testScope`는 승인된 테스트 리소스 그룹 ID 또는 리소스 ID로 지정합니다. 사용자 Object ID를 직접 사용하므로 사용자 UPN 검색을 위한 Graph 권한은 필요하지 않습니다.

```powershell
$testUserObjectId = '<승인된 테스트 사용자 Object ID>'
$testScope = "/subscriptions/$subscriptionId/resourceGroups/$resourceGroupName"
$readerRoleId = 'acdd72a7-3385-48ef-bd42-f606fba81ae7'

$existing = az role assignment list --subscription $subscriptionId --scope $testScope --fill-principal-name false --query "[?principalId=='$testUserObjectId' && ends_with(roleDefinitionId, '$readerRoleId')]" -o json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw '기존 역할 할당 조회에 실패했습니다.' }
if (@($existing).Count -gt 0) { throw '기존 직접 Reader 할당이 있습니다. 별도 테스트 범위를 사용하세요.' }

$assignmentName = [guid]::NewGuid().ToString()
$createdAssignment = az role assignment create --subscription $subscriptionId --name $assignmentName --assignee-object-id $testUserObjectId --assignee-principal-type User --role Reader --scope $testScope -o json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw '테스트 할당 생성에 실패했습니다. 생성 ID를 확인하기 전에는 삭제하지 마세요.' }

az monitor activity-log list --subscription $subscriptionId --resource-id $createdAssignment.id --offset 1h --status Succeeded -o table
```

```kusto
traces
| where timestamp > ago(1h)
| extend record = parse_json(message)
| where tostring(record.eventName) == "RbacRoleAssignmentReceived"
| project timestamp, cloud_RoleName,
          eventId=tostring(record.eventId),
          roleAssignmentId=tostring(record.roleAssignmentId)
| order by timestamp desc
```

공유 Application Insights에서는 `cloud_RoleName`을 배포한 앱 이름으로 추가 필터링합니다. Workspace에서 직접 조회한다면 `AppTraces`, `TimeGenerated`, `Message`, `AppRoleName`을 사용합니다.

실제 이벤트가 수신되지 않으면 배포, 필터와 전달 상태를 조사하고 여기서 중단합니다. 합성 이벤트 테스트 성공을 실제 RBAC 이벤트 지원 검증으로 대체하지 않습니다.

확인 후 또는 검증 실패를 기록한 후, 이 실행에서 생성에 성공한 임시 할당만 제거합니다. 생성 결과를 잃었다면 범위 전체를 삭제하지 말고 기록한 GUID로 정확한 할당부터 확인합니다.

```powershell
if ($createdAssignment.id) {
    az role assignment delete --subscription $subscriptionId --ids $createdAssignment.id
    if ($LASTEXITCODE -ne 0) { throw '임시 할당 정리에 실패했습니다. 해당 ID를 기록해 관리자에게 전달하세요.' }
}
```

## 실제 이벤트 수신 검증 결과

2026-11-15 한국 중부에서 실제 배포와 임시 RG Reader 할당으로 검증했습니다. Activity Log의 역할 할당 성공, Event Grid의 매칭 1건·전달 성공 1건·전달 시도 실패 0건, Application Insights의 `RbacRoleAssignmentReceived`를 대조했습니다. 수신 로그의 할당 ID가 테스트 할당과 일치했고 이벤트 ID도 해당 Activity Log의 correlation ID와 일치했습니다. 이는 이번 관측 결과이며 모든 이벤트에서 두 ID가 같다고 가정하지 않습니다. 임시 사용자 Reader와 임시 Storage 쓰기 진단 설정은 삭제·부재 확인을 마쳤습니다.

로컬 수신기 테스트 14개와 Bicep 컴파일도 통과했습니다. 한정 Checkov ARM 검사 결과는 22 passed, 0 failed입니다. 비적용·승인된 PoC 제약·ARM 이름 식 오탐에 해당하는 `CKV_AZURE_17/18/36/43/67/206/212/213/222/225`는 이 검사에서만 제외했습니다. 제외 범위는 클라이언트 인증서·HTTP/2·Azure Services 우회·동적 이름·복제/인스턴스/가용 영역·별도 상태 확인·Function 공개 수신 관련 권고입니다. Storage 공개 접근 차단 검사는 제외하지 않았으며, 전체 운영 보안 검사 통과를 뜻하지 않습니다.

배포 리소스는 후속 구현을 위해 유지하므로 Storage·Private Endpoint·Private DNS·로그 관련 비용이 발생할 수 있습니다. 현재 검증은 이벤트 수신까지이며 ARM·Graph 보강과 사용자·역할명·`companyName` 조회는 아직 구현하지 않았습니다. Graph 권한은 변경하지 않았습니다.

## 운영 한계

Event Grid는 중복 및 역순 전달이 가능하며, 초기 구성에는 dead-letter나 재처리 큐가 없습니다. 전달 실패로 이벤트가 유실될 수 있고, 전달 성공 뒤 함수 실행 실패가 항상 재전달로 복구되는 것도 아닙니다.

초기 샘플링은 비활성화하지만 수집 한도, 수집 지연과 서비스 장애로 로그가 누락될 수 있습니다. 이 구성은 손실 없는 감사 원장이 아닙니다. 실제 사용자 정보가 포함되는 로그의 접근 권한과 보존 기간은 조직 정책을 따릅니다.

## 설계와 계획

- 설계: `docs\superpowers\specs\2026-11-15-subscription-rbac-companyname-design.md`
- 구현 계획: `docs\superpowers\plans\2026-11-15-subscription-rbac-companyname.md`
