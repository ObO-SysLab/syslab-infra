# VM 인프라 구성

## 개요

실습형 문제 풀이를 위한 VM 환경 구성 기록입니다.
사용자가 "VM 생성" 버튼을 누르면 VM EC2에 Docker 컨테이너가 생성되고,
브라우저에서 WebSocket으로 터미널에 접속하는 구조입니다.

---

## 전체 흐름

```
[출제자]
  웹에서 문제 작성 + Dockerfile zip 업로드
        ↓
[Backend API]
  S3에 업로드
  s3://syslab-practice-dockerfile/prob-{prob_id}/source.zip
        ↓
[EventBridge Rule 1] — S3 이벤트 감지
  → CodeBuild 자동 시작
        ↓
[CodeBuild] — buildspec.yml 실행
  → PROB_ID 추출 (CODEBUILD_SOURCE_REPO_URL 파싱)
  → docker build / docker push
  → ECR: syslab-practice:prob-{prob_id}
        ↓
[EventBridge Rule 2] — CodeBuild 완료 감지
  → SQS: syslab-practice-build-queue에 결과 메시지 적재
        ↓
[Backend Worker] — 5초마다 폴링
  → source.location에서 prob_id 추출
  → SUCCEEDED → image_status = READY, ecr_image_uri 저장
  → FAILED    → image_status = FAILED, build_log_url 저장

[사용자]
  "VM 생성" 클릭
        ↓
[Backend API]
  DB에서 ecr_image_uri 조회 (image_status = READY 검증)
        ↓
[VM EC2] — SSH(JSch) 경유 docker 명령 실행
  → docker pull (ECR에서 해당 prob_id 이미지)
  → docker run → 컨테이너 ID 반환
        ↓
[사용자]
  WebSocket(/ws/terminal?containerId=xxx)
  → Nginx → VM EC2:8081 (Node.js ws-server)
  → docker exec → 컨테이너 bash
```

---

## AWS 리소스 목록

| 리소스 | 이름 | 비고 |
|---|---|---|
| S3 버킷 | syslab-practice-dockerfile | Dockerfile zip 저장 |
| ECR 레포 | syslab-practice | 이미지 태그: prob-{prob_id} |
| CodeBuild | syslab-practice-build | buildspec.yml은 프로젝트에 설정 |
| SQS 큐 | syslab-practice-build-queue | 빌드 결과 메시지 |
| SQS DLQ | syslab-practice-build-dlq | 5회 실패 시 이동 |
| EventBridge Rule 1 | syslab-practice-s3-trigger | S3 → CodeBuild |
| EventBridge Rule 2 | syslab-practice-codebuild-trigger | CodeBuild → SQS |

---

## EC2 구성

| 구분 | IP | 역할 |
|---|---|---|
| API EC2 | 3.34.216.202 (공인) / 10.0.1.166 (내부) | Nginx + Spring Boot + Redis |
| VM EC2 | 10.0.1.19 (내부) | Docker 컨테이너 실행 전용 |

### API EC2 → VM EC2 연결 방식
- **Docker 명령 실행**: SSH(JSch) 경유 (Docker Remote API 2375/2376 미사용)
- **SSH 키 위치**: API EC2 `~/.ssh/syslab-key.pem`
- **WebSocket 프록시**: Nginx `/ws/` → VM EC2:8081

### VM EC2 설정
- Docker 25.0.14 설치 완료
- Node.js v20.20.2 설치 완료
- ws-server 포트 8081에서 실행 중 (현재 nohup 방식 → systemd 전환 필요)

---

## Nginx 설정 (API EC2)

```nginx
server {
    listen 80;
    server_name diveon.net;

    # API 요청 → Spring Boot (8080)
    location /api/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    # WebSocket 요청 → VM EC2 Node.js (8081)
    location /ws/ {
        proxy_pass http://10.0.1.19:8081;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

---

## VPC Endpoint 재생성 가이드

> 비용 절약을 위해 삭제했다가 Fargate 사용 시 다시 생성하는 설정 모음

**생성 위치:** AWS 콘솔 → VPC → 엔드포인트 → "엔드포인트 생성"

### ① ECR DKR (Interface 타입)

| 항목 | 값 |
|---|---|
| 서비스 | `com.amazonaws.ap-northeast-2.ecr.dkr` |
| VPC | `syslab-vpc` |
| 서브넷 | `ap-northeast-2a` / `subnet-06d9954c1d63e9407` (Private) |
| 보안 그룹 | `SG-Fargate-Grader` / `SG-API` |

### ② ECR API (Interface 타입)

| 항목 | 값 |
|---|---|
| 서비스 | `com.amazonaws.ap-northeast-2.ecr.api` |
| VPC | `syslab-vpc` |
| 서브넷 | `ap-northeast-2a` / `subnet-06d9954c1d63e9407` (Private) |
| 보안 그룹 | `SG-Fargate-Grader` / `SG-API` |

### ③ CloudWatch Logs (Interface 타입)

| 항목 | 값 |
|---|---|
| 서비스 | `com.amazonaws.ap-northeast-2.logs` |
| VPC | `syslab-vpc` |
| 서브넷 | `ap-northeast-2a` / `subnet-06d9954c1d63e9407` (Private) |
| 보안 그룹 | `SG-Fargate-Grader` |

### ④ S3 (Gateway 타입) — 무료라 삭제 불필요 ✅

| 항목 | 값 |
|---|---|
| 서비스 | `com.amazonaws.ap-northeast-2.s3` |
| VPC | `syslab-vpc` |
| 라우팅 테이블 | Private Subnet 라우팅 테이블 |

### 추가 보안 그룹 규칙

`SG-Fargate-Grader` 인바운드에 아래 규칙 필요:

| 유형 | 포트 | 소스 |
|---|---|---|
| HTTPS | 443 | `10.0.2.0/24` |

### 비용 참고

| Endpoint | 타입 | 비용 |
|---|---|---|
| ECR DKR | Interface | $0.01/시간 |
| ECR API | Interface | $0.01/시간 |
| CloudWatch Logs | Interface | $0.01/시간 |
| S3 | Gateway | 무료 |

> 3개 합산 → 약 **$21/월** → 안 쓸 때 삭제 권장

---

## DB 변경사항 (2026-05-01)

`problem_practice` 테이블에 컬럼 수동 추가:

```sql
ALTER TABLE problem_practice
ADD COLUMN ecr_image_uri VARCHAR(500),
ADD COLUMN image_status VARCHAR(20) DEFAULT 'PENDING';
```

테스트 데이터 INSERT (prob_id=1, READY 상태):

```sql
-- 1. 테스트 유저
INSERT INTO domain_user (score, tier, created_at, login_id, nickname, password)
VALUES (0, 1, NOW(), 'test_admin', '테스트관리자', 'hashed_password');

-- 2. problem_summary
INSERT INTO problem_summary (solved_count, submitted_count, author_id, created_at, updated_at, difficulty, visibility, type, category, title)
VALUES (0, 0, 1, NOW(), NOW(), 'LEVEL1', 'PUBLIC', 'PRACTICE', 'OS', '테스트 실습 문제');

-- 3. problem_practice
INSERT INTO problem_practice (prob_id, summary, description, os_image, cpu_limit, memory_limit, flag_hash, image_status, ecr_image_uri)
VALUES (
  1,
  '테스트 실습 문제 요약',
  '테스트 실습 문제 설명',
  'ubuntu:22.04',
  '1',
  '512m',
  'test_flag_hash',
  'READY',
  '290373175858.dkr.ecr.ap-northeast-2.amazonaws.com/syslab-practice:prob-1'
);
```

> ⚠️ 백엔드 마이그레이션 스크립트로 정식 반영 필요

---

## 핵심 결정사항

| 항목 | 결정 내용 |
|---|---|
| PROB_ID 추출 | `CODEBUILD_SOURCE_REPO_URL` sed 파싱 (`prob-\([0-9]*\)/`) |
| S3 경로 규칙 | `prob-{prob_id}/source.zip` (반드시 준수) |
| ECR 태그 규칙 | `prob-{prob_id}` |
| image_uri 형식 | `290373175858.dkr.ecr.ap-northeast-2.amazonaws.com/syslab-practice:prob-{prob_id}` |
| Docker 제어 방식 | Docker Remote API 미사용 → SSH(JSch) 채택 (보안) |
| buildspec.yml 위치 | CodeBuild 프로젝트에 직접 설정 (zip에 포함 불필요) |
| zip 업로드 주체 | 백엔드가 직접 S3 업로드 (프론트 URL 전달 방식 미사용) |

---

## 미완료 작업

- [ ] ws-server systemd 등록 (`ws-server/systemd-setup.sh` 참고)
- [ ] WebSocket 인증 방식 합의 (옵션 A 권장: 단기 토큰 방식)
- [ ] 백엔드 마이그레이션 스크립트로 `ecr_image_uri`, `image_status` 컬럼 정식 반영

---

## 디렉토리 구조

```
vm/
├── README.md                   ← 이 파일
├── codebuild/
│   ├── README.md               ← 파이프라인 상세 설명
│   ├── buildspec.yml           ← 확정된 빌드 스크립트
│   └── sqs-message-sample.json ← 실제 캡처한 SQS 메시지 샘플
└── ws-server/
    ├── README.md               ← ws-server 설명
    ├── server.js               ← 현재 구동 중인 ws-server 코드
    └── systemd-setup.sh        ← systemd 등록 스크립트 (미완료)
```
