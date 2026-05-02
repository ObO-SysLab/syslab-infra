# syslab-grader

> SQS 메시지를 트리거로 Fargate에서 실행되는 자동 채점 컨테이너

---

## 아키텍처

```
Spring Boot → SQS → Lambda → Fargate (grader.py) → RDS
                                   ↑
                                  S3 (코드 + 테스트케이스)
```

1. Spring Boot가 코드 제출을 받아 S3에 업로드하고 SQS에 메시지 전송
2. Lambda가 SQS 메시지를 감지해 Fargate 태스크 실행
3. Fargate가 S3에서 코드와 테스트케이스를 받아 채점
4. 채점 결과를 RDS `submissions` 테이블에 저장

---

## 지원 언어

| 언어 | `language` 값 | 파일명 |
|------|--------------|--------|
| Python 3.11 | `python` | `Main.py` |
| C | `c` | `Main.c` |
| C++ (C++17) | `cpp` | `Main.cpp` |

---

## 파일 구조

```
grader/
├── grader.py            # 채점 메인 로직
├── Dockerfile           # 컨테이너 이미지 빌드
├── requirements.txt     # Python 의존성
└── lambda_trigger.mjs   # SQS → Fargate 트리거 Lambda 함수
```

---

## 환경변수

### Fargate (grader.py)

Lambda가 RunTask 시 자동으로 주입합니다.

| 변수명 | 설명 | 예시 |
|--------|------|------|
| `SUBMISSION_ID` | 제출 UUID | `550e8400-...` |
| `CODE_S3_KEY` | S3 코드 경로 | `submissions/uuid/Main.py` |
| `LANGUAGE` | 언어 | `python` / `c` / `cpp` |
| `PROBLEM_ID` | 문제 ID | `1` |
| `TIME_LIMIT` | 시간 제한 (초) | `2` |
| `TESTCASE_COUNT` | 테스트케이스 수 | `5` |
| `DB_HOST` | RDS 호스트 | `syslab-rds.xxx.rds.amazonaws.com` |
| `DB_PASSWORD` | RDS 비밀번호 | - |

### Lambda (lambda_trigger.mjs)

Lambda 콘솔 환경변수에서 설정합니다.

| 변수명 | 설명 |
|--------|------|
| `ECS_CLUSTER` | ECS 클러스터 이름 |
| `TASK_DEFINITION` | 태스크 정의 이름:버전 |
| `SUBNET_ID` | Private Subnet ID |
| `SECURITY_GROUP` | 보안 그룹 ID |
| `DB_HOST` | RDS 호스트 |
| `DB_PASSWORD` | RDS 비밀번호 |

---

## S3 구조

```
syslab-code/
├── submissions/
│   └── {submissionId}/
│       └── Main.py (또는 Main.c, Main.cpp)
└── testcases/
    └── prob-{problemId}/
        ├── input_1.txt
        ├── output_1.txt
        ├── input_2.txt
        └── output_2.txt
```

---

## 채점 결과 상태값

| `result` | 의미 |
|----------|------|
| `ACCEPTED` | 정답 |
| `WRONG_ANSWER` | 오답 |
| `TIME_LIMIT_EXCEEDED` | 시간 초과 |
| `RUNTIME_ERROR` | 런타임 에러 |
| `COMPILE_ERROR` | 컴파일 에러 (C/C++) |
| `SYSTEM_ERROR` | 시스템 에러 |

---

## ECR 빌드 & 푸시

```bash
# ECR 로그인
aws ecr get-login-password --region ap-northeast-2 | \
  docker login --username AWS --password-stdin \
  290373175858.dkr.ecr.ap-northeast-2.amazonaws.com

# 빌드
docker build -t syslab-grader .

# 태그
docker tag syslab-grader:latest \
  290373175858.dkr.ecr.ap-northeast-2.amazonaws.com/syslab-grader:latest

# 푸시
docker push \
  290373175858.dkr.ecr.ap-northeast-2.amazonaws.com/syslab-grader:latest
```

---

## VPC Endpoint (비용 절약용)

Fargate가 Private Subnet에서 외부 서비스에 접근하려면 아래 Endpoint가 필요합니다.
안 쓸 때는 삭제하고 사용 시 재생성하세요. (Interface 타입 3개 = 약 $21/월)

| 서비스 | 타입 |
|--------|------|
| `com.amazonaws.ap-northeast-2.ecr.dkr` | Interface |
| `com.amazonaws.ap-northeast-2.ecr.api` | Interface |
| `com.amazonaws.ap-northeast-2.logs` | Interface |
| `com.amazonaws.ap-northeast-2.s3` | Gateway (무료) |
