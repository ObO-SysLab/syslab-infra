# syslab-infra

DiveOn에서 API 서버 밖에서 도는 코드를 모아 둔 저장소다. 코딩 문제 채점기와 실습 환경(컨테이너 도우미, 웹 터미널, 실습 이미지 빌드)이 여기에 있다.
AWS 자원 정의는 [syslab-terraform](https://github.com/ObO-SysLab/syslab-terraform)에 있다.

## 구성

| 폴더 | 하는 일 | 운영에서 도는 곳 |
| --- | --- | --- |
| [`grader/`](grader/README.md) | 코딩 제출 채점. 조정기가 실행기를 띄우고 결과를 저장한다. | Lambda `syslab-grader-coordinator`, ECS 작업 정의 `syslab-isolated-runner` |
| [`grader/runner/`](grader/runner/MONITOR.md) | 격리 실행기, 오래 남은 실행기 정리 | Fargate 작업, Lambda `syslab-runner-monitor`(1분마다) |
| [`vm/lifecycle/`](vm/lifecycle/README.md) | 사용자별 실습 컨테이너 생성·확인·삭제 | VM EC2 `/usr/local/libexec/diveon-vm-helper.py` |
| [`vm/ws-server/`](vm/ws-server/README.md) | 브라우저 터미널을 컨테이너 셸에 잇는 WebSocket 서버 | VM EC2 `ws-terminal.service` |
| [`vm/codebuild/`](vm/codebuild/README.md) | 출제자가 올린 Dockerfile로 실습 이미지 빌드 | CodeBuild `syslab-practice-build` |
| [`ops/`](ops/RELEASE.md) | 릴리스 묶음과 체크섬 생성 | GitHub Actions |

실습 환경 전체 흐름은 [vm/README.md](vm/README.md)에 정리했다.

## 코딩 채점 흐름

```text
백엔드 ─ SQS syslab-grading-queue ─▶ Lambda 조정기
                                      │ S3에서 코드·입력·정답을 읽는다
                                      ▼
                                 Fargate 실행기 (제출마다 새로 뜸)
                                 코드와 입력만 받는다. 정답·DB 자격 증명은 없다
                                 chroot · UID 10001 · seccomp · rlimit 안에서 컴파일·실행
                                      │ 출력과 종료 상태만 돌려준다
                                      ▼
                                 조정기가 정답과 비교해 RDS에 결과를 저장한다
```

제출에서 결과까지 40초대가 걸린다. 대부분은 실행기 Fargate 작업이 뜨는 시간이다.

## 네트워크

실행기와 VM은 사설 서브넷에 있고, 밖으로 나가는 트래픽은 기존 NAT EC2(`syslab-nat`)를 거친다. S3는 무료 Gateway 엔드포인트로 간다. 유료 Interface 엔드포인트는 쓰지 않는다.
실행기 서브넷의 허용·차단 규칙은 [grader/runner/NAT_NETWORK.md](grader/runner/NAT_NETWORK.md)에 있다.

## 테스트

PR과 `main`·`fix/**`·`feat/**` push마다 [CI](.github/workflows/deploy.yml)가 아래를 돌린다. AWS에는 접속하지 않는다.

- Python 단위 테스트: `grader/tests`, `vm/codebuild`, `vm/lifecycle`, `ops`
- Node 테스트: `grader/tests/launch.test.mjs`, `vm/ws-server`
- 조정기·실행기·Lambda 이미지 빌드, 실제 Linux 컨테이너에서 실행기 격리와 실습 명령어 제한 검사
- 릴리스 묶음 생성(아티팩트로 남김)

로컬에서는 이렇게 돌린다.

```sh
pip install -r grader/requirements.txt
python -B -m unittest discover -s grader/tests -p 'test_*.py'
python -B -m unittest discover -s vm/codebuild -p 'test_*.py'
python -B -m unittest discover -s vm/lifecycle -p 'test_*.py'
node --test grader/tests/launch.test.mjs
cd vm/ws-server && npm ci && node --test auth.test.js server.test.js terminal.test.js
```

## 배포

CI는 검사와 묶음 생성까지만 한다. 운영 반영은 사람이 직접 한다.

- 채점 Lambda: `main` 커밋의 `grader/`로 `Dockerfile.lambda` 이미지를 빌드해 ECR `diveon-runner-validation`에 `coordinator-lambda-<커밋>` 태그로 올리고, Lambda 이미지를 그 digest로 바꾼다. 환경 변수 `DEPLOYED_IMAGE`에 태그를 적는다.
- 실행기: `grader/runner/Dockerfile` 이미지를 같은 ECR에 올리고 작업 정의를 새 digest로 등록한다. 조정기의 `RUNNER_TASK_DEFINITION`도 함께 바꾼다.
- VM 도우미·ws-server: VM의 `/var/lib/diveon/releases/`에 이전 파일을 남긴 뒤 바꾼다. 절차는 각 폴더 문서에 있다.

`grader/Dockerfile`, `grader/lambda_trigger.mjs`, `grader/launch.mjs`는 2026-11-12까지 쓰던 Fargate 조정기 경로다. 운영 자원은 11/13에 지웠고, 코드는 테스트와 함께 남겨 두었다.
