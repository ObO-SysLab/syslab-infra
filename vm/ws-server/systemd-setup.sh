#!/bin/bash
# VM EC2에서 실행
# ws-server를 nohup → systemd로 전환하는 스크립트
# 실행 전 VM EC2 접속 필요: ssh -i ~/.ssh/syslab-key.pem ec2-user@10.0.1.19

# 1. systemd 서비스 파일 생성
sudo tee /etc/systemd/system/ws-server.service > /dev/null <<'EOF'
[Unit]
Description=SysLab WebSocket Server
After=docker.service
Requires=docker.service

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/home/ec2-user/ws-server
ExecStart=/usr/bin/node /home/ec2-user/ws-server/server.js
Restart=always
RestartSec=5
StandardOutput=append:/home/ec2-user/ws-server/ws-server.log
StandardError=append:/home/ec2-user/ws-server/ws-server.log

[Install]
WantedBy=multi-user.target
EOF

# 2. 기존 nohup 프로세스 종료
kill $(cat /home/ec2-user/ws-server/ws-server.pid)

# 3. systemd 등록 및 시작
sudo systemctl daemon-reload
sudo systemctl enable --now ws-server

# 4. 상태 확인
sudo systemctl status ws-server
