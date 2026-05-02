# CodeBuild 파이프라인

## 개요

출제자가 Dockerfile을 업로드하면 자동으로 Docker 이미지를 빌드하고
ECR에 푸시한 뒤 SQS로 결과를 전달하는 파이프라인입니다.

---

## 파이프라인 흐름

```
Backend → S3 업로드
  s3://syslab-practice-dockerfile/prob-{prob_id}/source.zip
        ↓
EventBridge Rule 1 (syslab-practice-s3-trigger)
  S3 ObjectCreated 이벤트 감지
  → CodeBuild 자동 시작
        ↓
CodeBuild (syslab-practice-build)
  buildspec.yml 실행
  → PROB_ID 추출
  → docker build
  → docker push → ECR (syslab-practice:prob-{prob_id})
        ↓
EventBridge Rule 2 (syslab-practice-codebuild-trigger)
  CodeBuild 완료 이벤트 감지
  → SQS (syslab-practice-build-queue) 메시지 적재
        ↓
Backend Worker (5초마다 폴링)
  SQS 메시지 수신
  → SUCCEEDED: image_status = READY, ecr_image_uri 저장
  → FAILED:    image_status = FAILED, build_log_url 저장
```

---

## PROB_ID 추출 방식

### 문제 상황
`CODEBUILD_SOURCE_VERSION` 환경변수는 S3 트리거 방식에서 **비어있음** (AWS 버그)

### 해결책
`CODEBUILD_SOURCE_REPO_URL` 환경변수 사용

```
CODEBUILD_SOURCE_REPO_URL = syslab-practice-dockerfile/prob-1/source.zip
                                                        ↑
                                                    sed로 숫자 추출
```

```bash
PROB_ID=$(echo $CODEBUILD_SOURCE_REPO_URL | sed 's|.*prob-\([0-9]*\)/.*|\1|')
# → 1
```

### 삽질 기록 (2026-05-01)

| 시도 | 결과 | 이유 |
|---|---|---|
| `CODEBUILD_SOURCE_VERSION` 파싱 | ❌ 실패 | S3 트리거에서 값이 비어있음 |
| `aws codebuild batch-get-builds` | ❌ 실패 | CodeBuild Role에 `BatchGetBuilds` 권한 없음 |
| `aws s3api list-objects-v2` | ✅ 성공 | 최신 업로드 파일 조회 가능 |
| `CODEBUILD_SOURCE_REPO_URL` 파싱 | ✅ 성공 | 가장 깔끔한 방식으로 최종 확정 |

---

## buildspec.yml 설명

```yaml
version: 0.2
env:
  variables:
    ECR_REPOSITORY: syslab-practice   # CodeBuild 환경변수로 주입

phases:
  pre_build:
    commands:
      # PROB_ID 추출 (CODEBUILD_SOURCE_REPO_URL 파싱)
      - PROB_ID=$(echo $CODEBUILD_SOURCE_REPO_URL | sed 's|.*prob-\([0-9]*\)/.*|\1|')
      - echo "PROB_ID=$PROB_ID"
      # ECR 로그인
      - aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com
  build:
    commands:
      # 이미지 빌드 및 태깅
      - docker build -t $ECR_REPOSITORY:prob-$PROB_ID .
      - docker tag $ECR_REPOSITORY:prob-$PROB_ID $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPOSITORY:prob-$PROB_ID
  post_build:
    commands:
      # ECR 푸시
      - docker push $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPOSITORY:prob-$PROB_ID
      - echo "BUILD_COMPLETE prob_id=$PROB_ID"
```

### CodeBuild 프로젝트 환경변수 (AWS 콘솔에 설정)

| 변수명 | 값 |
|---|---|
| `ECR_REPOSITORY` | `syslab-practice` |
| `AWS_REGION` | `ap-northeast-2` |
| `ACCOUNT_ID` | `290373175858` |

> ⚠️ buildspec.yml은 CodeBuild 프로젝트에 직접 설정되어 있습니다.
> S3에 올리는 zip 안에 buildspec.yml을 포함할 필요 없습니다.
> zip 안에는 **Dockerfile만** 있으면 됩니다.

---

## S3 경로 규칙

```
s3://syslab-practice-dockerfile/prob-{prob_id}/source.zip
```

이 규칙을 반드시 지켜야 합니다. 다른 경로를 사용하면 PROB_ID 파싱이 실패합니다.

---

## SQS 메시지 처리 (백엔드)

### 파싱할 필드 3개

| 필드 경로 | 용도 |
|---|---|
| `detail.build-status` | `SUCCEEDED` / `FAILED` 판단 |
| `detail.additional-information.source.location` | prob_id 추출 |
| `detail.additional-information.logs.deep-link` | 실패 시 CloudWatch 로그 URL |

### prob_id 추출 (Java)

```java
String location = body.at("/detail/additional-information/source/location").asText();
// "syslab-practice-dockerfile/prob-1/source.zip"

Pattern pattern = Pattern.compile("prob-(\\d+)/");
Matcher matcher = pattern.matcher(location);
if (matcher.find()) {
    Long probId = Long.parseLong(matcher.group(1)); // → 1
}
```

### image_uri 규칙

```
290373175858.dkr.ecr.ap-northeast-2.amazonaws.com/syslab-practice:prob-{prob_id}
```

---

## 파일 목록

| 파일 | 설명 |
|---|---|
| `buildspec.yml` | CodeBuild 빌드 스크립트 확정본 |
| `sqs-message-sample.json` | 2026-05-01 실제 캡처한 SQS 메시지 샘플 |