# 코딩 채점기

코딩 제출 하나를 채점하는 코드다. 믿을 수 있는 조정기와, 제출 코드를 실제로 돌리는 격리 실행기로 나뉜다.

| 구성 | 파일 | 운영 |
| --- | --- | --- |
| 조정기 | `lambda_coordinator.py`, `grader.py`, `runner_client.py` | Lambda `syslab-grader-coordinator` (이미지 `Dockerfile.lambda`) |
| 실행기 | `runner/worker.py`, `runner/sandbox.py`, `runner/launcher.c` | ECS 작업 정의 `syslab-isolated-runner` (이미지 `runner/Dockerfile`) |
| 요청·결과 형식 | `execution_protocol.py` | 양쪽이 같이 쓴다 |
| OBO 장면 생성 | `obo_checker.py`, `obo_assessment.py`, `obo_runtime/` | 조정기 안에서 node로 실행 |
| 남은 실행기 정리 | `stale_runner_monitor.py` | Lambda `syslab-runner-monitor` ([MONITOR.md](runner/MONITOR.md)) |

## 흐름

1. 백엔드가 제출 정보를 SQS `syslab-grading-queue`에 넣는다. Lambda는 메시지를 한 번에 하나씩 받는다.
2. 조정기가 메시지 값을 검사하고(시간 1–30초, 메모리 16–1024MiB, 테스트케이스 1–100개), DB에 채점 시작을 기록한다.
3. S3에서 코드·입력·정답을 읽는다. 실행기에는 코드와 입력만 교환용 버킷(`RUNNER_BUCKET`)으로 넘긴다. 실행기는 서명된 URL로 요청 하나를 받고 결과 하나를 올린다.
4. 제출마다 Fargate 실행기 작업을 새로 띄운다. 작업 정의의 리비전과 이미지 digest를 띄우기 전에 확인한다.
5. 실행기가 끝나면 조정기가 출력을 정답과 비교하고, OBO 장면을 만들어 결과와 함께 RDS에 저장한다. 대회 제출이면 대회 점수도 같은 트랜잭션에서 갱신한다.

조정기는 제출 코드를 컴파일하거나 실행하지 않는다. 설정이 빠졌거나, 결과가 요청과 맞지 않거나, 격리 준비에 실패하면 인프라 실패로 처리한다. 격리 없이 실행하는 대체 경로는 없다.

## 격리

실행기 작업에는 작업 역할(task role)이 없다. DB 자격 증명, 정답, 공유 저장소도 없다. 루트 파일 시스템은 읽기 전용이고 `/work` 볼륨만 쓸 수 있다.

실행기 안에서 제출 코드는 `launcher`가 만든 감옥 안에서 돈다.

- 여분의 파일 디스크립터를 닫고 환경 변수를 비운다.
- 컴파일러와 실행에 필요한 파일만 있는 chroot로 들어간다.
- UID/GID를 10001로 낮추고 보조 그룹을 지운다. no-new-privileges를 건다.
- seccomp 허용 목록을 건다. 소켓·연결 같은 네트워크 시스템 호출, 자격 증명·프로세스 그룹 변경을 막는다. 실행 단계에서는 fork와 스레드도 막는다.
- 테스트케이스마다 새 작업 폴더를 쓴다.

컴파일도 같은 감옥 안에서 한다. 이 격리는 커널과 컴파일러의 취약점까지 막아 주지는 못하므로, 실행기 이미지와 `launcher.c`의 의존성은 계속 갱신해야 한다.

## 제한

| 항목 | 값 |
| --- | --- |
| 언어 | Python 3.11, C, C++17 (Debian bookworm) |
| 케이스당 시간·메모리 | 1–30초, 16–1024MiB (RSS가 아니라 가상 주소 공간) |
| 케이스 수·전체 시간 | 1–100개, `20 + 개수 × (시간 + 2) ≤ 600`초 |
| 컴파일 | 10초, 1024MiB, UID당 프로세스 32개 |
| 실행 | 프로세스 1개, 추가 프로세스·스레드 금지 |
| 코드·입력·정답 | 각각 1MiB |
| 출력 | 실행 1번에 stdout+stderr 1MiB, stderr는 버린다 |
| 성공 출력 합계 | 8MiB를 넘으면 이후 케이스는 출력 초과 |
| 요청·결과 JSON | 각각 16MiB (base64 포함) |
| 파일 크기·디스크립터 | 컴파일 16MiB, 실행 파일당 1MiB, 64개 |
| 실행기 작업 | 1 vCPU, 2048MiB |
| 교환 만료 | 780초 (작업 시작 시간 포함) |
| 실행기 감시 시간 | 시작 뒤 최대 630초 |

범위를 벗어난 요청은 값을 줄이지 않고 거절한다. 파일당 크기 제한은 전체 디스크 할당량이 아니라서, 작은 파일을 많이 만들면 작업의 임시 저장소가 찰 수 있다.

## 판정

저장되는 결과는 CORRECT, WRONG, ERROR 세 가지다.

- 시간 초과는 WRONG이다.
- 컴파일 오류, 실행 오류, 출력 초과, 잘못된 UTF-8 출력은 ERROR다.
- 인프라 실패는 판정하지 않고 다시 시도한다.

## 실패와 재시도

- 채점 중 인프라 실패는 `coding_grade_job`에 기록되고, 백엔드가 다음 시도로 다시 보낸다.
- 시작조차 못 한 메시지(본문 오류, DB 비밀 값을 못 읽음)만 SQS로 돌려보낸다. 세 번 실패하면 `syslab-grading-dlq`로 간다.
- `begin_grading`의 시도 번호 검사로 같은 메시지가 두 번 와도 두 번 채점하지 않는다.
- Lambda 제한 시간은 900초, SQS 숨김 시간은 960초다. 실행기 예산 780초보다 길게 잡았다.
- 새 Lambda 컨테이너는 뜰 때 OBO 검사기를 한 번 데운다. 처음 뜬 컨테이너의 첫 채점이 node 기동 시간 때문에 실패한 적이 있어서 넣었다.

## 설정

Lambda 환경 변수:

| 이름 | 내용 |
| --- | --- |
| `DB_SECRET_ARN` | DB 접속 정보가 든 Secrets Manager 비밀 (`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`) |
| `S3_BUCKET` | 코드·테스트케이스 버킷 |
| `RUNNER_BUCKET` | 실행기와 주고받는 교환 버킷 |
| `RUNNER_CLUSTER`, `RUNNER_TASK_DEFINITION` | 실행기 클러스터, 리비전까지 고정한 작업 정의 ARN |
| `RUNNER_SUBNET_ID`, `RUNNER_SECURITY_GROUP` | 실행기 전용 서브넷과 보안 그룹 ([NAT_NETWORK.md](runner/NAT_NETWORK.md)) |
| `DEPLOYED_IMAGE` | 지금 올라간 이미지 태그 (기록용) |

교환 버킷은 공개 차단, HTTPS 강제, SSE-S3, 짧은 보관 기간을 둔다. 실행기에 넘기는 값은 요청 GET URL과 결과 POST 정책 두 개뿐이고, 로그에 남기지 않는다.

대회 순위는 DB에서 읽는다. `update_scoreboard`는 테스트 호환용으로 남은 빈 함수라서 Redis 설정은 필요 없다.

## 배포

1. `main` 커밋에서 `grader/`만 묶는다(`git archive`).
2. `Dockerfile.lambda`로 이미지를 빌드해 ECR `diveon-runner-validation`에 `coordinator-lambda-<커밋>` 태그로 올린다.
3. Lambda 이미지를 새 digest로 바꾸고 `DEPLOYED_IMAGE`를 고친다.
4. 잘못된 메시지를 하나 보내 거절 로그가 남는지 보고, 실제 제출을 하나 채점해 본다.

되돌릴 때는 Lambda 이미지를 이전 digest로 바꾼다. 실행기를 바꿀 때는 새 작업 정의를 등록하고 `RUNNER_TASK_DEFINITION`과 감시 Lambda의 `RUNNER_MONITOR_DEFINITIONS`를 함께 고친다.

## 테스트

```sh
python -B -m unittest discover -s grader/tests -p 'test_*.py'
node --test grader/tests/launch.test.mjs
```

실제 PostgreSQL을 쓰는 테스트는 `DIVEON_GRADING_PG_TEST=approved`와 `PG_BIN`(PostgreSQL bin 폴더)을 줘야 돈다. 없으면 건너뛴다.
실행기 격리 검사는 Linux Docker가 필요해서 CI에서 돈다(`runner/test_*_linux.py`).

참고: [Fargate 작업 역할](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-iam-roles.html), [S3 POST 정책](https://docs.aws.amazon.com/AmazonS3/latest/developerguide/sigv4-HTTPPOSTConstructPolicy.html), [seccomp](https://docs.kernel.org/userspace-api/seccomp_filter.html)
