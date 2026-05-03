# VPC Endpoint 재생성 가이드

> 비용 절약을 위해 삭제했다가 Fargate 사용 시 다시 생성하는 설정 모음


```powershell
aws ec2 describe-security-groups --filters "Name=group-name,Values=SG-Fargate-Grader" --query "SecurityGroups[0].GroupId" --output text --region ap-northeast-2
```

`sg-0ac1a1862a849e01f` 형태로 나오면 그 값으로 아래 3개 실행해줘요!

**ECR DKR:**
```powershell
aws ec2 create-vpc-endpoint --vpc-id vpc-059c175e86a99bd3b --service-name com.amazonaws.ap-northeast-2.ecr.dkr --vpc-endpoint-type Interface --subnet-ids subnet-06d9954c1d63e9407 --security-group-ids sg-0ac1a1862a849e01f --region ap-northeast-2
```

**ECR API:**
```powershell
aws ec2 create-vpc-endpoint --vpc-id vpc-059c175e86a99bd3b --service-name com.amazonaws.ap-northeast-2.ecr.api --vpc-endpoint-type Interface --subnet-ids subnet-06d9954c1d63e9407 --security-group-ids sg-0ac1a1862a849e01f --region ap-northeast-2
```

**CloudWatch Logs:**
```powershell
aws ec2 create-vpc-endpoint --vpc-id vpc-059c175e86a99bd3b --service-name com.amazonaws.ap-northeast-2.logs --vpc-endpoint-type Interface --subnet-ids subnet-06d9954c1d63e9407 --security-group-ids sg-0ac1a1862a849e01f --region ap-northeast-2
```

SG ID 알려줘요!

---

## 생성 위치
AWS 콘솔 → VPC → 엔드포인트 → "엔드포인트 생성"

---

## ① ECR DKR (Interface 타입)

| 항목 | 값 |
|---|---|
| 서비스 검색 | `com.amazonaws.ap-northeast-2.ecr.dkr` |
| VPC | `syslab-vpc` |
| 서브넷 | `ap-northeast-2a` / `subnet-06d9954c1d63e9407` (Private) |
| 보안 그룹 | `SG-Fargate-Grader` |

---

## ② ECR API (Interface 타입)

| 항목 | 값 |
|---|---|
| 서비스 검색 | `com.amazonaws.ap-northeast-2.ecr.api` |
| VPC | `syslab-vpc` |
| 서브넷 | `ap-northeast-2a` / `subnet-06d9954c1d63e9407` (Private) |
| 보안 그룹 | `SG-Fargate-Grader` |

---

## ③ CloudWatch Logs (Interface 타입)

| 항목 | 값 |
|---|---|
| 서비스 검색 | `com.amazonaws.ap-northeast-2.logs` |
| VPC | `syslab-vpc` |
| 서브넷 | `ap-northeast-2a` / `subnet-06d9954c1d63e9407` (Private) |
| 보안 그룹 | `SG-Fargate-Grader` |

---

## ④ S3 (Gateway 타입) — 무료라 삭제 불필요 ✅

| 항목 | 값 |
|---|---|
| 서비스 검색 | `com.amazonaws.ap-northeast-2.s3` |
| VPC | `syslab-vpc` |
| 라우팅 테이블 | Private Subnet 라우팅 테이블 |

---

## 추가 보안 그룹 규칙 확인

`SG-Fargate-Grader` 인바운드에 아래 규칙 있어야 해요:

| 유형 | 포트 | 소스 |
|---|---|---|
| HTTPS | 443 | `10.0.2.0/24` |

---

## 비용 참고

| Endpoint | 타입 | 비용 |
|---|---|---|
| ECR DKR | Interface | $0.01/시간 |
| ECR API | Interface | $0.01/시간 |
| CloudWatch Logs | Interface | $0.01/시간 |
| S3 | Gateway | 무료 |

3개 합산 → 약 **$21/월** → 안 쓸 때 삭제 권장!
