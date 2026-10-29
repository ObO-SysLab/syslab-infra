# AUDIT-08: 검증과 릴리스 묶음

로컬 구현, 원격 workflow 실행·push·배포 없음. 기존 main 자동 latest 배포는 CI 검증/아티팩트 생성으로 교체했다. 이 변경을 push/merge하기 전 기존 자동 배포 중단을 검토해야 한다.

## 검증

- 기본: Python grader/tests, vm/codebuild, vm/lifecycle, ops; Node launch와 ws-server 테스트. DB/서버는 기동하지 않는다.
- 실제 PostgreSQL은 DIVEON_GRADING_PG_TEST=approved, PG_BIN을 명시한 별도 승인 실행만 허용. Python 환경에 psycopg2 설치 필요. 교차 언어 테스트는 syslab-backend를 같은 부모 디렉터리에 지정 커밋으로 체크아웃해야 한다.
- CI 정의에 coordinator/runner 모두 빌드 및 실제 Linux sandbox 검증 포함. 이번 로컬 작업에서는 Docker를 기동하지 않았으며 CI 실행 결과도 아직 없다.
- CI 환경에서 실제 DB·브라우저·AWS 테스트는 수행하지 않음. 통과를 운영 준비 완료로 해석하지 않는다.

## 아티팩트와 실행안 — 별도 승인

1. 세 저장소의 정확한 전체 커밋과 테스트 결과를 릴리스 기록에 묶는다. infrastructure.zip, grading-lambda.zip, manifest.json 및 backend JAR/ops SQL, frontend out을 수집한다.
2. package_release.py는 lambda_trigger.mjs를 index.mjs로 이름을 바꾸고 launch.mjs와 함께 ZIP 루트에 넣어 기존 index.handler를 보존한다. 별도 감시는 stale_runner_monitor.handler다. manifest에 handler와 runner/coordinator/VM/터미널/빌드 소스 checksum을 기록한다. 실패한/불완전한 묶음은 배포하지 않는다.
3. 승인 후 해당 소스로 coordinator/runner/backend 이미지를 빌드·등록하고 ECR 실제 digest를 기록한다. 로컬 image ID는 registry digest가 아니며 manifest placeholder로 실행하지 않는다. latest 사용 금지.
4. 운영 DB 현재 스키마와 ops SQL을 대조하고 백업 후 별도 승인된 migration만 적용한다. 백엔드 prod는 ddl-auto=validate. 모든 플래그는 현재 기본 비활성 상태를 유지한다.
5. 백엔드 비밀은 읽기 전용 외부 파일 /run/diveon/application-secret.yml로 제공하며 SPRING_CONFIG_ADDITIONAL_LOCATION=file:/run/diveon/ 설정을 사용한다. 비밀을 JAR/이미지/CI 아티팩트에 넣지 않는다.
6. 채점 신규 접수를 일시 중단하고 이전 task/queue를 확인한다. pinned runner 태스크 정의 → coordinator → Lambda 두 모듈 → 호환 백엔드/스키마 → 터미널 및 VM 도우미 → 프론트 순으로 적용안을 확정한다. 실습 빌드 CodeBuild 설정은 별도 AUDIT-07 적용안 준수.
7. 승인된 AWS 시험 범위에서 권한·커널·S3·실패 복구 검증 후에만 기능별 활성화. 일반/대회 3유형 제출, 본인 결과, VM/PTY 종료, 10분 빌드 제한을 검증한다.

## 복구안

- 적용 전 모든 실제 image digest, Lambda 버전, task revision, 터미널/VM 파일 hash, 정적 사이트 원본을 보존한다.
- 실패 시 접수를 막고 새 태스크만 중지한다. 호환되는 직전 버전 묶음으로 복귀한다. 격리 없는 예전 채점기나 무인증 터미널을 다시 개방하지 않는다.
- 이미 저장된 제출/정답을 되돌리거나 ledger/recovery 테이블을 삭제하지 않는다. 실패한 migration은 트랜잭션 롤백, 이미 커밋된 스키마는 호환 검토 후 보존한다.
- frontend S3 sync --delete/CloudFront 무효화 전 이전 out 전체를 확보한다. 복구는 해당 out을 복원하는 별도 승인 작업이다.
- 기존 docker image prune -af 제거: 복구 이미지와 로그를 유지한다.

## 남은 운영 조건

AWS/DB 배포 및 외부 비밀 공급, GitHub 보호 규칙/승인자 설정, 실제 CI와 브라우저 통합, 큐 DLQ/visibility와 Lambda timeout 대조는 미실시다. 이 workflow에는 AWS 자격증명·배포 단계가 없다.

참고: [GitHub artifact](https://docs.github.com/en/actions/tutorials/store-and-share-data), [deployment protection](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/control-deployments).

## 2026-10-30 승인된 로컬 검증

- 사용자 승인 후 Python PostgreSQL 38개(기존 32+해결자 3+실제 migration/순위 3) 모두 통과. 첫 실행의 psycopg2 예외 import 누락 4건은 f2d3719에서 opt-in import로 보완했다. 운영 제품 SQL 수정 없이 재실행 통과했다.
- Java 복구/VM JPA 14개도 별도 통과. 기본 Java 209·Python mock 73·Node 37·프론트 76과 합계 447개, 중복 재실행 제외.
- 네 신규 DB(초기 실패 실행 포함)는 postmaster.pid 부재, pg_ctl status 3/no server running, shutdown 로그 확인. 기존 postgres 7개 프로세스는 보존했다.
- 로컬 실제 ZIP 검증에서 index.handler를 보존하도록 index.mjs+launch.mjs를 확인했다. monitor handler도 manifest에 기록한다. 원격 CI/실제 Docker 재기동·Fargate·운영 배포는 수행하지 않았다.
- 실제 대회/실습 Spring JPA 전체 흐름, Redis/PTY/SSH/브라우저, AWS 권한/통신/스케줄과 기존 운영 데이터는 별도 검증 대상이다. AUDIT-01 P0 유지.
