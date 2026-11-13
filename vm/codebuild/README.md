# 실습 이미지 빌드 (AUDIT-07)

로컬 구현이며 운영 미적용이다. API·DB·프로젝트 buildspec·EventBridge 규칙을 함께 전환해야 한다. 전체 실행/복구안은 백엔드 `ops/2026-10-30-practice-build.md`에 있다. 서버 기동·운영 DB/AWS 변경·배포·push는 별도 승인 대상이다.

```text
API: 요청 UUID + 10분 마감 저장
  → S3: prob-N/builds/요청UUID/source.zip
  → 기존 EventBridge S3_KEY 환경변수 전달
  → CodeBuild: 검증된 ZIP 빌드 → ECR build-CodeBuildUUID 태그
  → EventBridge: 모든 Build State Change → 기존 SQS
  → API: CodeBuild 실제 ID/소스/상태 + ECR digest 조회 → READY/FAILED
```

알림이 누락되면 API가 30초 주기로 재조회한다. 현재 요청/빌드만 결과를 반영하고 READY에는 digest 주소를 저장한다. API/DB 장애 중 처리 시각까지 보장하지 않으며 10분 마감 뒤 성공 결과는 거절한다. CodeBuild의 실제 실행 중지와 플랫폼 결과 마감은 별개다.

`build_image.py`는 신뢰하는 오케스트레이션이다. 사용자 ZIP에서 이 파일이나 buildspec을 실행하지 않는다. `render_buildspec.py`가 Python을 inline으로 포함하는 `buildspec.yml`을 생성한다. **프로젝트 buildspec 설정에 파일 내용 자체를 반영**해야 하며 사용자 소스의 buildspec 경로로 설정하면 안 된다.

필수 환경: `S3_KEY`(기존 트리거 전달), `CODEBUILD_BUILD_ID`(CodeBuild 제공), `ACCOUNT_ID`, `AWS_REGION`, `ECR_REPOSITORY`, `PRACTICE_SOURCE_BUCKET`(새 설정). Python3·AWS CLI·Docker와 Docker build가 가능한 기존 빌드 환경이 필요하다. ZIP 루트에 Dockerfile이 있어야 하며 경로 이탈/링크, 2048개 초과 또는 총 512 MiB 초과 압축 해제 파일을 거절한다.

이미지 태그는 문제 번호가 아니라 실제 빌드 UUID다. Docker build 또는 push 실패를 성공으로 통지하지 않는다. 기존 post_build의 직접 SQS 전송은 제거하며 `result-event-pattern.json`으로 프로젝트별 모든 상태 이벤트를 원문 전달한다. 큐 정책/DLQ/추가 조회 권한은 실행안에서 검토한다.

2026-10-30 읽기 조회: 프로젝트의 실행 제한 60분·큐 대기 480분. 운영 설정을 바꾸지 않았다. 플랫폼 10분과 맞출 제한 분배는 별도 승인 대상이며 실행안의 5분+5분은 검토안이다. 자동 StopBuild와 고아 산출물 자동 삭제는 구현하지 않았다.

## 명령어 제한 (2026-11-13, 운영 미적용)

출제자가 허용 명령어를 정하면, 백엔드가 `source.zip`에 S3 메타데이터 `diveon-commands: v1:<정렬된 이름,쉼표>`를 붙여 올린다. 다운로드는 `aws s3api get-object` 한 번으로 해서 객체와 메타데이터를 함께 받는다. 메타데이터가 없으면 지금과 같은 빌드다.

메타데이터가 있으면 출제자 이미지(`diveon-src:build-…`) 위에 신뢰하는 층을 하나 더 얹는다.

- `lab` 계정(uid 10001)을 만들고 `USER`, `WORKDIR /home/lab`, `ENTRYPOINT []`를 정한다.
- 허용 명령어, bash, sleep, 허용 스크립트의 해석기를 뺀 모든 실행 파일에서 group/other의 읽기·실행 권한을 지운다. 공유 라이브러리는 그대로 둔다.
- 그래서 VM helper와 ws-server의 `docker exec`는 바꾸지 않아도 lab으로 열린다.

이미지에 없는 명령어, busybox 명령어, 이미 있는 lab 계정은 빌드 실패로 처리한다. 성공하면 `build-<uuid>`와 함께 `build-<uuid>-cmd-<sha256 앞 16자>` 태그를 push한다. 백엔드는 이 표식 태그가 같은 digest에 있을 때만 READY로 바꾼다. 해시 예시 값은 백엔드 `PracticeCommandPolicyTest`와 같다.

홈 폴더 규칙(noexec 대비):
- `/home/lab`의 출제자 파일은 root 소유로 둔다. lab은 읽기와 실행은 할 수 있지만 고칠 수는 없다.
- lab이 쓸 수 있는 곳은 `~/work`와 `/tmp`뿐이다.
- 이미지에 `LABEL net.diveon.restriction=v1`을 붙이면 VM 도우미(`vm/lifecycle`)가 이 폴더들을 noexec tmpfs로 덮는다.

제작 실패 원인: 빌드가 실패하면 `prob-N/builds/<요청>/result.json`(`{"version":1,"code":…,"command":…}`)을 남긴다.
- 코드: `SOURCE_INVALID`, `POLICY_INVALID`, `DOCKERFILE_FAILED`, `COMMAND_NOT_FOUND`, `BUSYBOX_COMMAND`, `LAB_ACCOUNT_EXISTS`, `IMAGE_TOOL_MISSING`, `RESTRICTION_FAILED`, `PUSH_FAILED`
- 레지스트리 로그인처럼 플랫폼 쪽 장애는 남기지 않는다.
- 백엔드는 목록에 있는 코드와 명령어 이름만 받고, 작성자에게만 보여 준다.
- CodeBuild 역할에 이 키의 `s3:PutObject`가 필요하다.

실제 Linux 검증(Docker와 네트워크 필요, CI docker 단계에서 실행):

```text
DIVEON_RESTRICTION_LINUX=1 python -B -m unittest discover -s vm/codebuild -p 'test_restriction_linux.py' -v
```

로컬 검증(실제 AWS/Docker 실행 없음):

```powershell
python -B vm/codebuild/render_buildspec.py
python -B -m unittest discover -s vm/codebuild -p 'test_*.py' -v
python -B -m unittest discover -s vm/lifecycle -v
```

빌드 모의 10개, 기존 VM helper 18개+digest 전달 1개를 검증한다. 실제 CodeBuild 빌드·SQS 전달·ECR pull은 별도 승인 후 검증해야 한다. `sqs_message_sample.json`은 과거 참고 자료이며 새 소비자는 source/detail-type/project-name/build-id가 있는 표준 이벤트를 요구한다. `Dockerfile.zip` 예제는 자동 수정하지 않았다.
