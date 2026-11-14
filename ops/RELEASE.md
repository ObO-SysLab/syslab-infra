# 검사와 릴리스 묶음

이 저장소의 CI(`.github/workflows/deploy.yml`)는 검사하고 묶음을 만드는 데까지만 한다. AWS 자격 증명이 없고, ECR push나 배포도 하지 않는다.

## CI가 하는 일

1. Python 단위 테스트(`grader/tests`, `vm/codebuild`, `vm/lifecycle`, `ops`)와 Node 테스트(`launch`, `ws-server`)를 돌린다. DB나 서버는 띄우지 않는다.
2. 조정기·실행기 이미지를 빌드하고, 실행기 시험 이미지를 네트워크 없이 띄워 Linux 격리를 확인한다.
3. 실습 이미지의 명령어 제한 층을 실제 Linux 이미지로 확인한다.
4. Lambda 조정기 이미지를 빌드해 Lambda와 같은 조건(읽기 전용, root가 아닌 사용자, 네트워크 없음)으로 띄워 본다.
5. `ops/package_release.py`로 릴리스 묶음을 만들어 아티팩트 `infrastructure-<커밋>`으로 올린다.

## 묶음 내용

`python -B ops/package_release.py --output release --revision <40자리 커밋>`이 만든다.

| 파일 | 내용 |
| --- | --- |
| `infrastructure.zip` | `grader/`, `vm/`의 배포용 파일. 테스트, `node_modules`, 시험용 Dockerfile은 뺀다. |
| `runner-monitor-lambda.zip` | 감시 Lambda 코드 (`stale_runner_monitor.handler`) |
| `grading-lambda.zip` | 옛 Fargate 조정기 트리거 (`index.handler`). 지금은 운영에서 쓰지 않는다. |
| `manifest.json` | 소스 커밋, 파일별·묶음별 SHA-256, Lambda 핸들러 |

ZIP 안의 파일 시각은 1980-01-01로 고정해서, 같은 커밋이면 같은 체크섬이 나온다. 필요한 파일이 하나라도 없으면 묶음을 만들지 않는다.

## 실제 PostgreSQL 테스트

채점 결과 저장, 점수 일관성, 재시도 테스트 일부는 실제 PostgreSQL이 있어야 돈다.

```sh
DIVEON_GRADING_PG_TEST=approved PG_BIN=<PostgreSQL bin 폴더> python -B -m unittest discover -s grader/tests -p 'test_*.py'
```

`psycopg2`가 필요하다. 백엔드와 같이 보는 테스트는 `syslab-backend`가 같은 부모 폴더에 있어야 한다. 환경 변수가 없으면 DB 테스트는 건너뛴다.

## 배포할 때 지킬 것

- 이미지는 `latest`를 쓰지 않고 ECR digest로 고정한다.
- 운영에 올리기 전에 지금 Lambda 이미지 digest, 작업 정의 리비전, VM 파일 해시를 적어 둔다. 되돌릴 때 이 값을 쓴다.
- 실패하면 새로 띄운 작업만 멈추고 직전 묶음으로 돌아간다. 격리 없는 옛 채점기나 인증 없는 터미널로 돌아가지 않는다.
- 이미 저장된 제출과 결과는 되돌리지 않는다.
