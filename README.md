# SysLab 인프라

운영 인프라 구성·운영 정보의 저장소다. 현재 배포 경로가 사용하는
`grader/`, `vm/`, `ops/` 실행 코드와 CI 회귀 테스트도 함께 유지한다.
AWS 자원의 테라폼 코드는 별도 저장소 `syslab-terraform`에 있다.

## 현재 네트워크

2026-11-04 합의한 방식은 **기존 NAT EC2 + S3 Gateway**다. 유료 Interface Endpoint를
새로 생성하지 않는다. [네트워크 구성](grader/runner/NAT_NETWORK.md),
[리소스 ID](grader/runner/network.nat.json), [NAT 운영 정보](vm/nat-migration.md)를 확인한다.
리소스 ID 기록은 실시간 상태나 전체 서비스 배포 완료를 뜻하지 않는다.

기존 Endpoint 생성 문서는 [과거 구성 기록](docs/history/interface-endpoints.md)에 보존했다.
현재 운영 명령으로 실행하지 않는다.

## 보관 기준

| 위치 | 내용 |
| --- | --- |
| `grader/`, `vm/` | 배포에 필요한 실행 코드·설정·운영 문서 |
| `ops/`, `.github/workflows/` | 릴리스 포장 및 지속적인 회귀 검증 |
| 작업 공간의 `../obo-harness/experiments/` | 임시 실험 코드·검증 전용 Dockerfile·예전 Fargate 예제 |
| `../obo-harness/evidence/` | AWS 조회 결과·검증 보고서·종료/정리 근거 |
| `../obo-harness/rollback/` | 변경 전 백업·임시 빌드 산출물; 배포 소스와 구분 |
| 작업 공간의 `../syslab-test/` | CI·빌드·릴리스가 쓰지 않는 테스트·예제 파일(원래 경로 구조 유지) |

새 테스트용 서버 스크립트·로그·ZIP을 이 저장소에 추가하지 않는다. CI가 사용하는
회귀 테스트는 제품 코드와 버전을 맞춰 유지하며, 릴리스 ZIP에는 넣지 않는다.
`obo-harness`는 별도 로컬 Git 저장소이므로 아래 자료 링크는 두 저장소가 나란히 있는
작업 공간 기준이다. 원격 CI는 `obo-harness`에 의존하지 않는다.

- [이번 파일 이동 목록](../obo-harness/evidence/2026-11-04-infra-layout/moves.json)
- [실제 Fargate 검증](../obo-harness/evidence/2026-11-04-fargate-isolation/REPORT.md)
- [릴리스 구성](ops/RELEASE.md)
