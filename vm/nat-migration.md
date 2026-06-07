# VPC Endpoint → NAT Instance 전환 가이드

## 배경 및 목적

기존에 Fargate용으로 생성한 VPC Interface Endpoint 3개가 월 **$21** 비용 발생.
NAT Instance로 대체하면 월 **$3.8** 수준으로 절감 가능.

추가로 VM EC2를 새 서브넷으로 이전하면서 ECR 접근 문제도 함께 해결.

---

## 기존 구조

```
Fargate (subnet-06d9954c1d63e9407, 10.0.2.0/24)
  → VPC Endpoint (ECR DKR, ECR API, CloudWatch Logs)
  → ECR / CloudWatch

VM EC2 (subnet-009d6a6be71dfc7b4, 10.0.1.0/24)
  → API EC2와 같은 서브넷 → 라우팅 테이블 분리 불가
  → ECR 접근 불가
```

---

## 변경 후 구조

```
Fargate (subnet-06d9954c1d63e9407, 10.0.2.0/24)
  → NAT Instance (Public Subnet, 10.0.1.x)
  → IGW → ECR / CloudWatch

VM EC2 (subnet-0b0f731c9fdb19c39, 10.0.3.0/24, ap-northeast-2c)
  → NAT Instance
  → IGW → ECR
```

---

## 전환 순서

### 1. NAT Instance 생성

AWS 콘솔 → EC2 → 인스턴스 시작

| 항목 | 값 |
|---|---|
| 이름 | `syslab-nat` |
| AMI | Amazon Linux 2023 |
| 인스턴스 유형 | `t3.nano` |
| 서브넷 | Public Subnet (`10.0.1.0/24`) |
| 퍼블릭 IP 자동 할당 | **활성화** |
| 보안 그룹 | `SG-VM` (기존 사용) |
| 키 페어 | `syslab-key` |

생성 후 **소스/대상 확인 비활성화**:

```powershell
aws ec2 modify-instance-attribute --region ap-northeast-2 --instance-id {NAT_INSTANCE_ID} --no-source-dest-check
```

---

### 2. NAT Instance 내부 설정

NAT Instance에 SSH 접속 후:

```bash
# iptables 설치
sudo dnf install -y iptables-services

# IP 포워딩 활성화
sudo sysctl -w net.ipv4.ip_forward=1
echo "net.ipv4.ip_forward=1" | sudo tee -a /etc/sysctl.d/custom-ip-forward.conf

# Masquerade 설정 (패킷 주소 변환)
sudo iptables -t nat -A POSTROUTING -o ens5 -j MASQUERADE

# 재부팅 후에도 유지
sudo service iptables save
```

---

### 3. VM EC2용 새 서브넷 생성

기존 VM EC2(`10.0.1.0/24`)가 API EC2와 같은 서브넷이라 라우팅 테이블 분리 불가.
`ap-northeast-2c`에 이미 `10.0.3.0/24` 서브넷이 존재하므로 그대로 사용.

| 항목 | 값 |
|---|---|
| 서브넷 ID | `subnet-0b0f731c9fdb19c39` |
| CIDR | `10.0.3.0/24` |
| AZ | `ap-northeast-2c` |

---

### 4. Private 라우팅 테이블 생성 및 설정

```powershell
# 라우팅 테이블 생성
aws ec2 create-route-table --region ap-northeast-2 --vpc-id vpc-059c175e86a99bd3b

# NAT Instance로 향하는 기본 라우트 추가
aws ec2 create-route --region ap-northeast-2 --route-table-id {RTB_ID} --destination-cidr-block 0.0.0.0/0 --instance-id {NAT_INSTANCE_ID}

# S3 Gateway Endpoint 라우팅 추가 (패키지 설치용)
aws ec2 modify-vpc-endpoint --region ap-northeast-2 --vpc-endpoint-id vpce-00e4d5879e9739088 --add-route-table-ids {RTB_ID}

# VM EC2 서브넷 연결
aws ec2 associate-route-table --region ap-northeast-2 --route-table-id {RTB_ID} --subnet-id subnet-0b0f731c9fdb19c39

# Fargate 서브넷 연결
aws ec2 associate-route-table --region ap-northeast-2 --route-table-id {RTB_ID} --subnet-id subnet-06d9954c1d63e9407
```

---

### 5. NAT Instance 보안 그룹 인바운드 추가

Private 서브넷에서 오는 트래픽 허용:

```powershell
# VM EC2 서브넷 (10.0.3.0/24) 전체 허용
aws ec2 authorize-security-group-ingress --region ap-northeast-2 --group-id {NAT_SG_ID} --protocol all --cidr 10.0.3.0/24

# HTTPS (ECR, CloudWatch 등)
aws ec2 authorize-security-group-ingress --region ap-northeast-2 --group-id {NAT_SG_ID} --protocol tcp --port 443 --cidr 10.0.3.0/24

# HTTP (패키지 설치)
aws ec2 authorize-security-group-ingress --region ap-northeast-2 --group-id {NAT_SG_ID} --protocol tcp --port 80 --cidr 10.0.3.0/24
```

---

### 6. VPC Endpoint 3개 삭제

```powershell
aws ec2 delete-vpc-endpoints --region ap-northeast-2 --vpc-endpoint-ids vpce-0839fb4ce1407658e vpce-07484cf9b3662ff60 vpce-02498b54aebbd2838
```

| 삭제한 Endpoint | 서비스 |
|---|---|
| vpce-0839fb4ce1407658e | ECR DKR |
| vpce-07484cf9b3662ff60 | ECR API |
| vpce-02498b54aebbd2838 | CloudWatch Logs |

> ⚠️ S3 Gateway Endpoint(`vpce-00e4d5879e9739088`)는 무료라 유지

---

### 7. 새 VM EC2 생성

기존 VM EC2(`10.0.1.19`)를 종료하고 새 서브넷에 재생성.

```powershell
aws ec2 run-instances \
  --region ap-northeast-2 \
  --image-id ami-0bb8c0d387143b435 \
  --instance-type t3.small \
  --key-name syslab-key \
  --subnet-id subnet-0b0f731c9fdb19c39 \
  --security-group-ids sg-078c92da2537a47aa \
  --no-associate-public-ip-address \
  --iam-instance-profile Name=syslab-vm-ec2-profile \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=syslab-vm}]"
```

새 VM EC2 Private IP: `10.0.3.30`

---

### 8. 새 VM EC2 환경 세팅

```bash
# Docker 설치
sudo dnf update -y
sudo dnf install -y docker
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker ec2-user

# 재접속 후 Node.js 설치
curl -fsSL https://rpm.nodesource.com/setup_20.x | sudo bash -
sudo dnf install -y nodejs

# ws-server 설치
mkdir -p ~/ws-server && cd ~/ws-server
npm init -y
npm install ws

# server.js 작성 후 실행
nohup node ~/ws-server/server.js > ~/ws-server/ws-server.log 2>&1 &
echo $! > ~/ws-server/ws-server.pid
```

---

### 9. Nginx 설정 업데이트

API EC2에서 WebSocket 프록시 IP 변경:

```bash
sudo vi /etc/nginx/conf.d/syslab.conf
# proxy_pass http://10.0.1.19:8081 → http://10.0.3.30:8081 으로 변경

sudo nginx -t
sudo systemctl reload nginx
```

---

### 10. deploy.yml 업데이트

```yaml
# 변경 전
vm-ec2-host: http://10.0.1.19:2376

# 변경 후
vm-ec2-host: 10.0.3.30
```

---

## 최종 리소스 현황

| 리소스 | ID | 비고 |
|---|---|---|
| NAT Instance | i-08761f745077023e3 | 13.124.1.127 (공인) |
| VM EC2 (신규) | i-0c7a60ac58f999048 | 10.0.3.30 (사설) |
| Private 라우팅 테이블 | rtb-03816132535afaadc | VM EC2 + Fargate 서브넷 연결 |
| S3 Gateway Endpoint | vpce-00e4d5879e9739088 | 유지 (무료) |

---

## 비용 비교

| 항목 | 변경 전 | 변경 후 |
|---|---|---|
| VPC Interface Endpoint 3개 | $21/월 | $0 (삭제) |
| NAT Instance (t3.nano) | $0 | ~$3.8/월 |
| **합계** | **$21/월** | **~$3.8/월** |

> ⚠️ NAT Instance는 사용하지 않을 때 중지 가능 (비용 0). 시연/테스트 시에만 켜두기.

---

## 미완료 작업

- [ ] ws-server systemd 등록 (`ws-server/systemd-setup.sh` 참고)
- [ ] WebSocket 인증 방식 합의 (백엔드 미팅 필요)