# 실습 이미지 빌드

출제자가 실습형 문제에 올린 Dockerfile ZIP으로 실습 이미지를 만드는 코드다. CodeBuild 프로젝트 `syslab-practice-build`에서 돈다.

```text
백엔드: 요청 UUID와 10분 마감을 저장
  → S3 prob-N/builds/<요청UUID>/source.zip
  → EventBridge syslab-practice-s3-trigger (키가 source.zip으로 끝날 때만, S3_KEY 전달)
  → CodeBuild: build_image.py가 ZIP을 검사하고 이미지를 빌드
  → ECR syslab-practice, 태그 build-<CodeBuild 빌드 UUID>
  → EventBridge syslab-practice-build-complete: 빌드 상태 변경 이벤트 전체 → SQS syslab-practice-build-queue
  → 백엔드: CodeBuild 빌드 ID·소스·상태와 ECR digest를 확인하고 READY 또는 FAILED
```

알림을 놓치면 백엔드가 30초마다 다시 조회한다. 지금 요청의 빌드 결과만 반영하고, READY에는 digest 주소를 저장한다. 10분 마감이 지난 뒤 들어온 성공 결과는 받지 않는다.
CodeBuild 제한 시간은 8분, 대기열 제한은 5분이다.

## 빌드 규칙

`build_image.py`는 믿을 수 있는 쪽의 코드다. 사용자 ZIP 안의 스크립트나 buildspec은 실행하지 않는다.
`render_buildspec.py`가 이 파이썬 코드를 그대로 넣은 `buildspec.yml`을 만든다. CodeBuild 프로젝트에는 이 내용을 직접 넣어야 하고, 사용자 소스 안의 buildspec 경로를 가리키게 하면 안 된다.

- ZIP 맨 위에 Dockerfile이 있어야 한다.
- 경로 이탈, 링크, 파일 2048개 초과, 압축을 푼 크기 512MiB 초과는 거절한다.
- 이미지 태그는 문제 번호가 아니라 실제 빌드 UUID다.
- Docker build나 push가 실패하면 성공으로 알리지 않는다.

필요한 환경 변수: `S3_KEY`(트리거가 넘김), `CODEBUILD_BUILD_ID`(CodeBuild가 넣음), `ACCOUNT_ID`, `AWS_REGION`, `ECR_REPOSITORY`, `PRACTICE_SOURCE_BUCKET`. 빌드 환경에는 Python 3, AWS CLI, Docker가 있어야 한다.

## 명령어 제한

출제자가 허용 명령어를 정하면, 백엔드가 `source.zip`에 S3 메타데이터 `diveon-commands: v1:<정렬된 이름,쉼표>`를 붙여 올린다. 빌드는 `aws s3api get-object` 한 번으로 객체와 메타데이터를 함께 받는다. 메타데이터가 없으면 일반 빌드다.

메타데이터가 있으면 출제자 이미지(`diveon-src:build-…`) 위에 신뢰하는 층을 하나 더 얹는다.

- `lab` 계정(uid 10001)을 만들고 `USER`, `WORKDIR /home/lab`, `ENTRYPOINT []`를 정한다.
- 허용 명령어, bash, sleep, 허용 스크립트의 해석기를 뺀 모든 실행 파일에서 group/other의 읽기·실행 권한을 지운다. 공유 라이브러리는 그대로 둔다.
- 그래서 VM 도우미와 ws-server의 `docker exec`를 바꾸지 않아도 lab 계정으로 열린다.

이미지에 없는 명령어, busybox 명령어, 이미 있는 lab 계정은 빌드 실패로 처리한다. 성공하면 `build-<uuid>`와 함께 `build-<uuid>-cmd-<sha256 앞 16자>` 태그를 push한다. 백엔드는 이 표식 태그가 같은 digest에 있을 때만 READY로 바꾼다. 해시 예시 값은 백엔드 `PracticeCommandPolicyTest`와 같다.

홈 폴더 규칙:
- `/home/lab`의 출제자 파일은 root 소유로 둔다. lab은 읽고 실행할 수 있지만 고칠 수는 없다.
- lab이 쓸 수 있는 곳은 `~/work`와 `/tmp`뿐이다.
- 이미지에 `LABEL net.diveon.restriction=v1`이 붙으면 VM 도우미(`vm/lifecycle`)가 이 폴더들을 noexec tmpfs로 덮는다.

빌드가 실패하면 `prob-N/builds/<요청>/result.json`(`{"version":1,"code":…,"command":…}`)을 남긴다.
- 코드: `SOURCE_INVALID`, `POLICY_INVALID`, `DOCKERFILE_FAILED`, `COMMAND_NOT_FOUND`, `BUSYBOX_COMMAND`, `LAB_ACCOUNT_EXISTS`, `IMAGE_TOOL_MISSING`, `RESTRICTION_FAILED`, `PUSH_FAILED`
- 레지스트리 로그인 같은 플랫폼 쪽 장애는 남기지 않는다.
- 백엔드는 목록에 있는 코드와 명령어 이름만 받아 작성자에게만 보여 준다.
- CodeBuild 역할에 이 키의 `s3:PutObject` 권한이 있어야 한다.

구조상 막을 수 없는 것(문서로만 두고 출제 화면에는 안내하지 않는다):
- `echo *`, `read`, `printf` 같은 bash 내장 명령으로 `ls`·`cat`을 흉내 낼 수 있다. 내장 명령은 실행 파일이 아니라서 권한으로 막을 수 없다.
- `gcc`는 `cc1`·`as`·`ld`·`collect2`를, `man`은 `groff`·`less`를 실행한다. 이런 명령어를 허용하려면 내부 프로그램도 목록에 넣어야 실제로 동작한다. 빌드는 이것을 감지하지 못한다.
- busybox 이미지는 명령 하나만 허용해도 busybox 전체가 열리므로 빌드를 실패시킨다(`BUSYBOX_COMMAND`).
- 출제자가 `/home/lab`에 넣은 실행 파일은 실행된다(문제용 바이너리). 다른 셸이나 busybox를 넣으면 제한이 의미 없어진다.
- 허용한 도구의 원래 기능은 막지 않는다. `python3`를 허용하면 파이썬으로 할 수 있는 일은 모두 되고, 다른 실행 파일 실행만 권한으로 막힌다.

## 테스트

```sh
python -B vm/codebuild/render_buildspec.py
python -B -m unittest discover -s vm/codebuild -p 'test_*.py' -v
```

실제 Linux 이미지로 명령어 제한을 확인하는 테스트는 Docker와 네트워크가 필요해서 CI에서 돈다.

```sh
DIVEON_RESTRICTION_LINUX=1 python -B -m unittest discover -s vm/codebuild -p 'test_restriction_linux.py' -v
```
