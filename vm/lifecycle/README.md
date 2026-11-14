# VM 도우미

사용자별 실습 컨테이너를 만들고, 확인하고, 지우는 스크립트다. VM EC2에 `/usr/local/libexec/diveon-vm-helper.py`로 설치돼 있다.

백엔드는 고정 호스트 키로 SSH 접속해 `python3 /usr/local/libexec/diveon-vm-helper.py`를 실행하고, 요청을 버전 1 JSON으로 stdin에 넘긴다.

## 요청

받는 명령은 `create`, `inspect`, `remove`, `reconcile` 네 가지다.

- 세션 요청에는 `sessionId`, `userId`, `problemId`, `expiresAtEpochMs`가 들어간다. `create`에는 이미지도 있어야 한다.
- 예전 방식으로 만든 컨테이너는 `inspect`·`remove`에 전체 `legacyContainerId`를 주고, 이름이 예전 규칙과 정확히 같을 때만 다룬다.

## 동작 규칙

- 만들기 전에 소유 기록(manifest)을, 지우기 전에 종료 표시를 먼저 남긴다.
- 세션마다 기다리지 않는 flock을 걸어 같은 세션의 명령이 겹치지 않게 한다.
- 이미 있는 컨테이너는 정해진 이름과 라벨이 맞을 때만 확인하거나 지운다.
- Docker 조회가 실패하면 "없다"로 보지 않는다. 지운 뒤에는 정말 없어졌는지 다시 확인한다.
- `reconcile`은 종료된 세션까지 기록을 돌아가며 보고, 응답을 잃은 뒤에 끝난 Docker 작업을 정리한다. SSH 명령이 중간에 끊겨도 같은 세션만 계속 보지 않도록 진행 위치를 먼저 저장한다.
- 기록이 없거나 맞지 않는 컨테이너는 자동으로 건드리지 않는다. 사람이 확인한다.

## 컨테이너 옵션

모든 실습 컨테이너에 `--memory 256m --memory-swap 256m`(스왑 없음), `--pids-limit 256`, `--cpus 0.3`, `--network none`, `--init`을 붙인다.
일반 이미지에는 no-new-privileges를 붙이지 않는다. sudo·setuid로 권한을 올리는 실습 문제가 있기 때문이다.

명령어 제한 이미지(라벨 `net.diveon.restriction=v1`)에는 옵션을 더 붙인다. 만들기 전에 라벨을 읽고, 이미지가 VM에 없으면 먼저 받는다.
- `/tmp`, `/var/tmp`, `/home/lab/work`를 `noexec,nosuid,nodev` tmpfs로 덮는다.
- `no-new-privileges`를 붙인다.
- 모르는 라벨 값이면 만들지 않는다. 제한이 덜 걸린 컨테이너가 생기지 않게 하려는 것이다.

## 설치와 교체

- 상태 폴더는 `/var/lib/diveon-vm`이다. SSH 실행 사용자(ec2-user) 소유, 권한 0700이다.
- 도우미와 상태 폴더는 실습 컨테이너에서 보이면 안 된다. 컨테이너에 Docker 소켓이나 상태 폴더를 마운트하지 않는다.
- 교체할 때는 이전 파일을 `/var/lib/diveon/releases/` 아래에 남기고 새 파일을 덮어쓴다. 바뀐 옵션은 새로 만드는 컨테이너부터 적용된다.
- manifest, 종료 표시, 진행 위치는 교체하거나 되돌릴 때도 지우지 않는다.

백엔드 설정: `vm.ssh-known-hosts`(필수), `vm.recovery.enabled`(기본 꺼짐), `vm.recovery.poll-ms`(기본 30000). 조회 주기는 삭제 시점을 보장하지 않는다. API·DB·SSH·Docker 장애가 있으면 정리가 늦어진다.

## 테스트

Docker, DB, SSH, 네트워크 없이 도는 단위 테스트다.

```sh
python -B -m unittest discover -s vm/lifecycle -p 'test_*.py' -v
```

설치와 SQL 변경 절차는 백엔드 저장소의 `ops/2026-10-30-vm-lifecycle.md`에 있다.
