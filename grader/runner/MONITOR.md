# AUDIT-01 고아 실행 태스크 감시 — 로컬 구현, 운영 미적용

기존 coordinator는 ARN을 아는 태스크에 StopTask를 요청하고 응답 유실 때 startedBy로 한 번 조회했다. ECS 지연 노출, coordinator 사망, 장기 provisioning, StopTask 실패 때 외부 재확인이 없었다.

## 계약

- grader/stale_runner_monitor.py의 handler를 별도 Lambda로 배포할 준비를 했다. package_release.py가 runner-monitor-lambda.zip을 생성한다.
- coordinator가 RunTask에 group=diveon:isolated-runner를 넣는다. 감시는 정확한 cluster ARN, 허용된 task definition revision들, FARGATE, group, 32자리 request ID를 모두 확인한다.
- 기본 관찰 모드 RUNNER_MONITOR_DRY_RUN=true. false는 별도 운영 승인 후 설정한다. 이벤트 payload는 범위나 중지 설정을 바꿀 수 없다.
- RUNNER_MONITOR_CLUSTER: 명시적 cluster ARN. RUNNER_MONITOR_DEFINITIONS: 동일 계정/리전의 정확한 revision ARN을 쉼표로 연결한다. 롤아웃 중 구/신 revision 둘 다 유지한다.
- RUNNER_MONITOR_MAX_AGE_SECONDS 기본 900, 900~3600만 허용. 기존 coordinator 전체 예산 780초보다 길다. createdAt으로 계산해 시작 전 장기 대기 역시 포착한다.
- 1분 반복 실행을 제안한다. ListTasks desiredStatus=RUNNING은 실제 PENDING/PROVISIONING도 포함하므로 desiredStatus=PENDING을 사용하지 않는다.
- 페이지 전체 조회/중복 제거 → DescribeTasks → 오래된 대상의 두 번째 조회 → StopTask. 응답 유실/조회 실패/중지 실패는 성공으로 삼지 않고 invocation 실패로 처리한다. 20초 미만 실행 시간 잔여 시 실패시켜 다음 스캔이 필요함을 드러낸다.
- stopRequested는 중지 요청 수이며 실제 종료 확인 수가 아니다. ECS/권한/스케줄 장애 시 16분 내 강제 종료를 보장하지 않는다.
- 기존 group 없는 태스크는 자동 중지하지 않는다. 이력 없이 추정해서 중지하지 말고 전환 전에 별도 inventory/drain한다.

## 실행·복구안 — 아직 실행하지 않음

1. 정확한 cluster/revision/image digest, 최근 실행 태스크와 스케줄을 읽기 확인한다. 현행 코드 예산 780초와 마감/retry를 대조한다.
2. 승인 후 독립 모니터 Lambda(권장 timeout 120초), 1분 스케줄, 실패 재시도/DLQ/오류·누락 호출 경보를 생성한다. 실행 역할은 해당 cluster ListTasks와 해당 cluster 태스크 DescribeTasks/StopTask만 허용하는 검토된 정책을 쓴다. 비밀·DB·일반 S3 권한은 필요 없다.
3. 먼저 dry-run으로 정상/오래된/다른 태스크 분류를 확인한다. 정확히 승인된 시험 태스크로 장기 provisioning·coordinator 사망·StopTask 권한 실패와 지연 노출을 확인한 뒤 중지 모드를 별도 승인한다.
4. 실패하면 스케줄/중지 모드를 비활성화하고 직전 Lambda 버전/env를 복구한다. 채점 접수를 잠시 막고 관찰된 새 runner만 조사한다. 기존 무격리 채점기로 복귀하지 않는다.
5. S3 executions/ prefix 고아 객체는 별도 retention/lifecycle 정책과 비용 검토가 필요하다. 이 모듈은 객체를 삭제하지 않는다.

로컬 테스트 11개: 기본 관찰, running/pending/provisioning, 타 태스크 제외, 마감 전/미래 시간, 조회 사이 종료, StopTask 재시도, 페이징/지연 노출, 불확실 응답/시간, 잘못된 설정, Lambda 시간 부족. AWS 호출/배포/태스크 중지 없음.

**AUDIT-01 P0 최우선 미해결 유지.** 실제 Fargate 커널·권한·네트워크·S3 통신 및 이 감시의 운영 적용은 아직 검증하지 않았다.

근거: [ListTasks](https://docs.aws.amazon.com/boto3/latest/reference/services/ecs/client/list_tasks.html), [DescribeTasks](https://docs.aws.amazon.com/boto3/latest/reference/services/ecs/client/describe_tasks.html).
