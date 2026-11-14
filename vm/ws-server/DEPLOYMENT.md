# 웹 터미널 운영

## 백엔드와 주고받는 것

1. 브라우저가 로그인 토큰으로 `POST /api/vm/terminal-ticket`을 부른다. 본문은 `{ "probId": 9, "containerId": "<64자 Docker ID>" }`이다.
   응답은 `{ "ticket": "<43자 무작위 값>", "expiresAt": <epoch 밀리초> }`이다. 소유자, 문제, RUNNING 상태, 남은 만료 시간이 모두 맞아야 발급된다.
2. 브라우저가 Origin `https://diveon.net`으로 `/ws/terminal?container_id=<ID>`를 연다. URL에는 로그인 토큰도 티켓도 없다. 첫 텍스트 프레임은 `{ "type": "auth", "ticket": "..." }`이다.
3. 터미널 서버가 `X-Terminal-Service-Key` 헤더와 함께 `POST /internal/terminal/connect`를 부른다. 본문은 `{ "ticket": "...", "containerId": "..." }`이다.
   백엔드는 Redis에서 티켓을 한 번에 꺼내 지우고(GETDEL), DB 세션을 다시 확인한 뒤 `{ "lease": "...", "containerId": "...", "expiresAt": ... }`를 돌려준다.
4. 터미널 서버는 PTY 출력보다 먼저 `{ "type": "ready" }`를 보낸다. 그 뒤로는 PTY 입출력을 그대로 주고받는다.
5. 터미널 서버는 5초마다 `POST /internal/terminal/check`에 `{ "lease": "..." }`를 보낸다. lease는 서버만 쓰고, 티켓 대신 쓸 수 없다.

티켓은 최대 30초, lease는 세션의 원래 만료 시각까지 유효하다. 확인이나 재접속으로 세션이 늘어나지 않는다.
세션이 멈췄거나 지워졌거나 다른 컨테이너로 바뀌었을 때, 그리고 Redis·DB 장애나 이상한 응답이 오면 접속을 막는다. 백엔드 호출은 응답 본문까지 2초 안에 끝나야 한다. 그래서 세션이 끊긴 뒤 약 7초 안에 연결이 닫힌다. 인증하지 않은 소켓은 5초 뒤에 닫는다.
Redis 키는 `terminal:v1:ticket:`, `terminal:v1:lease:`로 시작하고 저절로 만료된다.

## 운영 구성

| 위치 | 내용 |
| --- | --- |
| 터미널 서버 | `/opt/diveon/vm-20261113/ws-server`, systemd `ws-terminal.service`, `10.0.3.30:8081`에서만 듣는다 |
| 환경 파일 | `/etc/diveon/terminal.env` (root, 0600). systemd가 ec2-user로 바꾸기 전에 읽는다 |
| 백엔드 연결 | systemd `diveon-terminal-tunnel.service`. VM 계정 `diveon-tunnel`이 API EC2의 `diveon-terminal-tunnel` 계정으로 SSH 접속해 `127.0.0.1:18080` → API `127.0.0.1:8080`을 연다 |
| 이전 버전 | `/opt/diveon/vm-20261105`, `/var/lib/diveon/releases/vm-before-*` |

환경 변수:

| 이름 | 값 |
| --- | --- |
| `TERMINAL_SERVICE_KEY` | 백엔드와 같은 기계용 비밀 값(32–256자). 로그인 JWT 비밀 값과 다르다. 없거나 짧으면 백엔드가 터미널 접속을 내주지 않는다. |
| `TERMINAL_API_URL` | `http://127.0.0.1:18080` (SSH 터널) |
| `TERMINAL_ALLOWED_ORIGIN` | `https://diveon.net` (와일드카드 없음) |
| `TERMINAL_BIND_HOST` | `10.0.3.30` |

`TERMINAL_API_URL`은 HTTPS이거나 숫자 루프백 주소의 HTTP만 받는다. VPC 사설 IP로 가는 평문 HTTP는 일부러 거절한다.

터널 계정은 이 용도로만 쓰는 Ed25519 키를 쓰고, API 쪽 호스트 키를 고정했다. API 계정은 `127.0.0.1:8080`으로 가는 로컬 포워딩만 할 수 있고 셸·PTY·에이전트·X11 포워딩은 막혀 있다. 접속 출발지는 VM 보안 그룹으로 제한했다.
`/internal/terminal/*`은 CloudFront와 공개 Nginx 경로로 열지 않는다.

비밀 값은 Git, JAR, 로그, 스크린샷, 이 문서 어디에도 넣지 않는다.

## 교체 절차

1. 지금 서비스 파일, 환경 변수 이름, 배포 폴더의 파일 해시를 적어 두고 `/var/lib/diveon/releases/` 아래에 복사한다.
2. 새 버전을 날짜가 붙은 새 폴더에 두고 VM에서 `npm ci`를 돌린다. node-pty는 네이티브 모듈이라 Windows의 `node_modules`를 복사하면 안 된다.
3. `ws-terminal.service`의 작업 폴더와 실행 경로를 새 폴더로 바꾸고 서비스를 다시 띄운다. `systemd-setup.sh`는 서비스 파일 틀을 출력만 하고 설치하지 않는다.
4. 확인한다.
   - 잘못된 티켓은 `ready` 없이 닫히고, 허용하지 않은 Origin은 403을 받는다.
   - 실제 실습 세션에서 입력·출력, Ctrl-C, 창 크기 변경, 종료가 된다.
   - 세션을 멈추면 7초 안에 연결이 닫힌다.
5. 크기 프레임처럼 프로토콜이 바뀌었으면 서버를 먼저 올리고 프론트엔드를 그다음에 배포한다.

## 되돌리기

1. API Nginx에서 `/ws/terminal`을 막고 `ws-terminal.service`를 멈춘다. 열려 있던 터미널은 모두 닫힌다.
2. 서비스 파일을 이전 폴더로 되돌리고 다시 띄운 뒤, 위 확인을 거쳐 경로를 연다.

인증 없는 옛 터미널로 돌아가지 않는다. 실습 컨테이너와 그 안의 데이터는 되돌리기로 지워지지 않는다. 티켓과 lease는 저절로 만료되므로 Redis를 비우지 않는다.

참고: [node-pty](https://github.com/microsoft/node-pty/blob/main/typings/node-pty.d.ts), [Spring Data Redis ValueOperations](https://docs.spring.io/spring-data/redis/docs/current/api/org/springframework/data/redis/core/ValueOperations.html)
