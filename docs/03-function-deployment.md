# 03. Function 코드 게시

⏱️ **15분** · 목표: 비공개 파일 없이 Python 코드를 Linux 원격 빌드로 게시합니다.

이전: [02. 인프라](02-private-infrastructure.md) · [홈](../README.md) · 다음: [04. 이벤트 연결](04-event-grid.md)

## 1. 최소 소스 ZIP과 원격 빌드

👁️ **설명**

01에서 `STATE_FILE`과 공통 함수를 복구하고 02의 출력 capture·네트워크 확인을 마칩니다. ZIP에는 `function_app.py`, `host.json`, `requirements.txt`, `rbac_monitor`의 Python 소스만 들어갑니다. 상태·증거·로컬 설정·문서·테스트·가상환경·Windows용 의존성은 올리지 않습니다.

🟢 **실행**

<!-- command-contract: publish -->
```bash
publish_receiver() {
  restore_workshop || return
  local app zip
  app=$(state_value outputs.functionAppName) || return
  zip=$(python3 scripts/workshop.py package "$STATE_FILE") || return
  if [[ ! -f "$zip" ]]; then
    printf '%s\n' "소스 ZIP이 없습니다. 게시하지 않습니다." >&2
    return 1
  fi
  az functionapp deployment source config-zip --subscription "$SUB_ID" \
    --resource-group "$RG" --name "$app" --src "$zip" --build-remote true
}
publish_receiver
```

📋 **확인**

원격 빌드와 배포 완료 응답을 확인합니다. `--build-remote true`가 Linux용 의존성을 설치합니다. 게시 응답만으로 함수가 등록되었다고 판단하지 말고 다음 확인을 수행합니다.

⚠️ **주의**

배포 ZIP이나 빌드 로그를 공개하지 않습니다. Storage가 막혀 있다고 공유 키·공개 접근을 켜거나 로컬 Windows의 `.python_packages`를 묶어서 올리지 않습니다.

## 2. 함수 등록 확인

🟢 **실행**

```bash
inspect_published_receiver() {
  restore_workshop || return
  local app
  app=$(state_value outputs.functionAppName) || return
  az functionapp function list --subscription "$SUB_ID" \
    --resource-group "$RG" --name "$app" --output table
}
inspect_published_receiver
```

📋 **확인**

`RbacAssignmentObserver`가 Event Grid trigger로 등록되어야 합니다. Portal → **Function App → 함수 → RbacAssignmentObserver**에서도 확인합니다. 등록이 지연되면 잠시 기다린 뒤 **조회만** 다시 합니다. 아직 없으면 원인을 해결하고 04로 진행하지 않습니다.

Function의 설정은 `EXPECTED_SUBSCRIPTION_ID`, 관리 ID 기반 `AzureWebJobsStorage__...`, Entra 인증 Application Insights 게시로 구성됩니다. 키·연결 문자열 전체를 터미널에 출력하는 명령은 사용하지 않습니다.

## 3. 사설 Storage 게시 실패 진단

👁️ **설명**

403은 공개 접근이 필요하다는 뜻이 아닙니다. 단계와 오류 코드를 확인해야 합니다.

| 증상 | 확인할 항목 |
|---|---|
| Storage 또는 패키지 경로 403 | 02의 PE 승인·DNS VNet 링크·Function VNet 통합, Storage 리소스 범위 관리 ID 역할 |
| 새 역할 직후 인증 실패 | RBAC 전파를 기다린 뒤 같은 실행·같은 ZIP으로 재시도 |
| 빌드 실패 | 원격 빌드 로그, Python 3.11·requirements·소스 ZIP 구조 |
| 게시 성공인데 함수 없음 | 호스트 시작 오류, 모듈 import, 트리거 등록·동기화 |
| 정책 관련 배포 실패 | Portal 배포 오류의 정책 ID·효과·관리 그룹 상속 |

🟢 **실행**

Portal → **Function App → 배포 센터/진단 및 문제 해결**, **Application Insights → 실패/로그**에서 원본 오류를 확인합니다. 먼저 02의 읽기 전용 네트워크 확인을 반복합니다.

📋 **확인**

기존 PoC에서는 VNet을 뒤늦게 연결한 새 테스트 앱의 이전 게시 컨텍스트가 Storage 403을 유지했습니다. 네트워크·권한 확인 후 승인된 앱만 재시작하고 같은 ZIP을 게시하여 해결했습니다. 이것은 **과거 관측된 특정 원인**이지 모든 403의 해결책은 아닙니다.

⚠️ **주의**

이 장은 자동 재시작 명령을 실행하지 않습니다. 운영 또는 다른 참가자의 Function을 재시작하지 않습니다. 이번 전용 앱에서도 영향과 원인을 검토한 뒤 별도 승인합니다.

## 완료 체크

- [ ] 최소 소스만 들어 있는 ZIP을 만들었습니다.
- [ ] Linux 원격 빌드가 완료되었습니다.
- [ ] `RbacAssignmentObserver` 등록을 확인했습니다.

이전: [02](02-private-infrastructure.md) · 다음: [04. 이벤트 연결](04-event-grid.md)
