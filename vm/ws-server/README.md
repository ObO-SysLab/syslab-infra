# 웹 터미널 서버

브라우저의 xterm을 사용자 실습 컨테이너의 셸에 잇는 WebSocket 서버다. VM EC2에서 `ws-terminal.service`(Node 22)로 돌고, 컨테이너 안에서 `docker exec -it <컨테이너> /bin/bash`를 node-pty로 연다.

```text
브라우저 ─ wss://diveon.net/ws/terminal ─ API Nginx ─ VM 10.0.3.30:8081 (이 서버)
                                                       │ 티켓 확인: SSH 터널 127.0.0.1:18080 → API 백엔드
                                                       └ docker exec → 실습 컨테이너
```

## 접속 순서

1. 브라우저가 로그인한 상태로 백엔드에서 30초짜리 일회용 티켓을 받는다.
2. WebSocket을 열고 첫 프레임으로 티켓을 보낸다. 티켓은 URL에 넣지 않는다.
3. 서버가 티켓을 백엔드에 보내 서버 전용 lease로 바꾼다. 확인이 끝나기 전에는 셸을 열지 않는다.
4. 서버가 `ready` 프레임을 보내면 브라우저가 xterm을 붙인다.
5. 서버는 5초마다 lease로 세션을 다시 확인한다. 세션이 끝났거나 멈췄거나 확인이 실패하면 연결을 닫는다.

## 크기 프레임

`ready` 뒤에 브라우저는 PTY 크기를 자기 터미널 크기에 맞춘다.
- 바이너리 프레임 `\0DVRS` + uint16 열 + uint16 행 (빅 엔디언, 열 20–500, 행 5–200)
- 서버는 PTY 크기만 바꾸고 이 프레임을 셸에 입력하지 않는다. 범위를 벗어나면 무시한다.
- xterm이 보내는 바이너리 입력은 ESC로 시작하므로 이 프레임과 겹치지 않는다.
- 이 프레임을 보내는 프론트엔드보다 서버를 먼저 배포해야 한다. 옛 서버는 이 프레임을 셸에 그대로 입력한다.

## 테스트

리스너, Docker, AWS, Redis, DB 없이 도는 테스트다.

```sh
npm ci
node --test auth.test.js terminal.test.js server.test.js
```

운영 설정, 교체 절차, 되돌리기는 [DEPLOYMENT.md](DEPLOYMENT.md)에 있다.
