# ws-server

## 개요

VM EC2에서 실행되는 Node.js WebSocket 서버입니다.
브라우저(xterm.js)와 Docker 컨테이너의 bash를 연결하는 역할을 합니다.

---

## 동작 방식

```
브라우저 (xterm.js)
    ↕ wss://diveon.net/ws/terminal?containerId=xxx
CloudFront
    ↕
Nginx (API EC2)          ← /ws/ 경로를 VM EC2로 프록시
    ↕ ws://10.0.1.19:8081/ws/terminal
ws-server (Node.js)      ← 이 서버
    ↕ docker exec -it {containerId} /bin/bash
Docker 컨테이너
```

---

## 실행 환경

| 항목 | 값 |
|---|---|
| 서버 | VM EC2 (10.0.1.19) |
| 포트 | 8081 |
| 경로 | `/ws/terminal` |
| Node.js | v20.20.2 |
| 위치 | `/home/ec2-user/ws-server/server.js` |

---

## 쿼리 파라미터

| 파라미터 | 필수 | 설명 |
|---|---|---|
| `containerId` | ✅ | Docker 컨테이너 ID (백엔드가 VM 생성 후 반환) |

**연결 예시:**
```
wss://diveon.net/ws/terminal?containerId=8a2f1c93b4e7
```

---

## 현재 실행 방식

현재 `nohup` 방식으로 실행 중입니다.

```bash
# 현재 실행 방식 (nohup)
nohup node server.js > ws-server.log 2>&1 &
echo $! > ws-server.pid
```

> ⚠️ VM EC2 재부팅 시 자동으로 죽습니다.
> 시연 전 반드시 systemd로 전환해야 합니다. (`systemd-setup.sh` 참고)

---

## systemd 전환 방법 (미완료)

```bash
# VM EC2 접속
ssh -i ~/.ssh/syslab-key.pem ec2-user@10.0.1.19

# 스크립트 실행
bash systemd-setup.sh

# 정상 확인
sudo systemctl status ws-server
```

전환 후 재부팅 테스트:
```bash
sudo reboot
# 재접속 후
sudo systemctl status ws-server  # active (running) 확인
```

---

## 로그 확인

```bash
# 실시간 로그
tail -f ~/ws-server/ws-server.log

# systemd 전환 후
journalctl -u ws-server -f
```

---

## 미완료 사항

- [ ] systemd 등록 (`systemd-setup.sh` 실행 필요)
- [ ] WebSocket 인증 — 현재 `containerId`만 알면 누구나 접속 가능
  - 옵션 A (권장): 백엔드가 VM 생성 시 단기 토큰 발급 → ws-server가 검증
  - 옵션 B: JWT secret 공유 후 ws-server가 직접 검증

---

## 파일 목록

| 파일 | 설명 |
|---|---|
| `server.js` | 현재 VM EC2에서 구동 중인 ws-server 코드 |
| `systemd-setup.sh` | nohup → systemd 전환 스크립트 |
