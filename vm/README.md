# 실습 환경 (VM EC2 위 Docker 컨테이너)

2026-11-13 기준 운영 구성이다. 화면에서는 "실습 환경"이라고 부르며, 실제로는 VM EC2 한 대 위에서 사용자별 Docker 컨테이너로 동작한다.

## 전체 흐름

```
[출제] Dockerfile zip (+ 선택: 허용 명령어)
  → 백엔드: S3 prob-N/builds/<요청UUID>/source.zip 업로드
    (허용 명령어는 객체 메타데이터 diveon-commands로 함께 보낸다)
  → EventBridge(syslab-practice-s3-trigger, 키가 source.zip으로 끝날 때만) → CodeBuild syslab-practice-build
  → codebuild/build_image.py
      - 출제자 이미지를 빌드한다.
      - 허용 명령어가 있으면 제한 층(lab 계정, 실행 권한 제거, 라벨)을 얹는다.
      - ECR syslab-practice에 build-<빌드UUID> 태그로 push한다(제한 이미지는 -cmd-<해시> 태그도 함께).
      - 실패하면 같은 폴더에 result.json(사유 코드)을 남긴다.
  → EventBridge(syslab-practice-build-complete) → SQS → 백엔드
      - digest와 표식 태그를 확인한 뒤 READY로 바꾼다.

[실습] "실습 환경 켜기"
  → 백엔드 → SSH(고정 호스트 키) → VM의 /usr/local/libexec/diveon-vm-helper.py
      - 요청은 JSON으로 stdin에 넘긴다.
      - lifecycle/diveon_vm_helper.py가 컨테이너를 만든다. 세션은 2시간이다.
  → 브라우저(xterm)
      - 30초짜리 일회용 티켓을 받는다.
      - wss://diveon.net/ws/terminal → API Nginx → VM 10.0.3.30:8081 ws-server로 연결한다.
      - ws-server는 티켓을 lease로 바꾸고 5초마다 다시 확인한다.
      - docker exec -it <컨테이너> /bin/bash. 크기 프레임 \0DVRS로 PTY 크기를 맞춘다.
```

## 컨테이너 격리

| 대상 | 옵션 |
|---|---|
| 모든 실습 | `--memory 256m --memory-swap 256m`(스왑 없음), `--pids-limit 256`, `--cpus 0.3`, `--network none`, `--init` |
| 명령어 제한 이미지 (라벨 `net.diveon.restriction=v1`) | 위에 더해: `/tmp`·`/var/tmp`·`/home/lab/work`를 `noexec,nosuid,nodev` tmpfs로, `--security-opt no-new-privileges` |

일반 실습에는 no-new-privileges를 걸지 않는다. sudo·setuid로 권한을 올리는 실습 문제를 지원하기 위해서다(2026-11-13 결정).

명령어 제한의 원리와 남은 한계는 [codebuild/README.md](codebuild/README.md)의 "명령어 제한"에 있다.

## 운영 위치

| 구분 | 값 |
|---|---|
| API EC2 | `syslab-api` 10.0.1.166. Nginx(`/api/`→Spring 8080, `/ws/`→VM 8081), 백엔드 컨테이너 |
| VM EC2 | `syslab-vm` 10.0.3.30(서브넷 10.0.3.0/24, NAT `syslab-nat` 경유). Docker, ws-server, 도우미 |
| ws-server | `/opt/diveon/vm-20261113/ws-server`(systemd `ws-terminal.service`, Node 22), 터널 `diveon-terminal-tunnel.service` |
| 도우미 | `/usr/local/libexec/diveon-vm-helper.py`, 상태 `/var/lib/diveon-vm`(ec2-user, 0700) |
| 이전 버전 | `/opt/diveon/vm-20261105`, `/var/lib/diveon/releases/vm-before-20261113`(오전: 도우미·서비스 백업), `vm-before-20261113b`(오후: 프로세스·스왑 제한 전 도우미) |
| 이미지 | ECR `syslab-practice`(옛 `prob-N` 태그와 새 `build-<UUID>` 태그) |

되돌릴 때는 위 표의 "이전 버전" 위치에 남긴 파일을 쓴다.

## 디렉터리

```
vm/
├── README.md        ← 이 파일
├── codebuild/       ← 실습 이미지 빌드(build_image.py → buildspec.yml), 단위·Linux 테스트
├── lifecycle/       ← VM 도우미(컨테이너 생성·확인·삭제·복구), 단위 테스트
├── ws-server/       ← 인증 터미널(티켓·lease·PTY·크기 프레임), node 테스트
└── nat-migration.md ← 과거 NAT 전환 기록(다시 실행하지 않음)
```
