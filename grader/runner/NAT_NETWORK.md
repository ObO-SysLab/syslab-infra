# 실행기 네트워크

실행기 Fargate 작업은 전용 사설 서브넷에서 뜨고, 밖으로 나가는 트래픽은 기존 NAT EC2(`syslab-nat`)를 거친다. 시간당 요금이 붙는 Interface 엔드포인트 대신 이미 있던 NAT를 다시 쓰기로 2026-11-04에 정했다.
자원 ID는 [network.nat.json](network.nat.json)에 있다. 실행기 서브넷·보안 그룹 ID는 조정기 환경 변수 `RUNNER_SUBNET_ID`, `RUNNER_SECURITY_GROUP`에 들어간다.

## 허용하는 길

| 대상 | 허용 | 막는 것 |
| --- | --- | --- |
| NAT EC2 | 사설 서브넷의 HTTPS, VM의 패키지 내려받기(HTTP) | 공개 인바운드 |
| 이미지·로그 전송 | NAT를 거친 HTTPS, 이미지 레이어는 S3 Gateway | 실행기 공인 IP 없음 |
| 실행기 워커 | 서명된 S3 GET/POST (리전 HTTPS, Gateway 경유) | 리다이렉트·프록시, 작업 역할, DB 비밀 값 |
| 컴파일러·제출 코드 | 표준 입출력과 작업 폴더 | seccomp가 소켓 생성을 막는다 |
| 실행기 서브넷 | 나가는 TCP 443, 돌아오는 포트 1024–65535 | 전용 NACL이 10/8, 172.16/12, 192.168/16을 양방향으로 먼저 막는다. IPv6 포함 나머지는 기본 차단 |

보안 그룹에는 허용 규칙만 쓸 수 있어서, `0.0.0.0/0`으로 HTTPS를 열면 사설 주소로 가는 HTTPS도 같이 열린다. 사설 주소 차단은 그래서 실행기 서브넷 전용 NACL에 두었다. VM·RDS 쪽 NACL은 건드리지 않았다.
AWS DNS와 메타데이터 주소는 NACL 적용 방식이 다르므로, NACL이 이것까지 막는다고 보면 안 된다. 제출 코드가 메타데이터에 닿지 못하는 이유는 seccomp가 소켓을 막고 작업 역할이 없어서다.

## 한계

실행기 워커 프로세스는 공개 HTTPS 주소라면 어디든 나갈 수 있다. NAT는 목적지 허용 목록이 아니다. 워커나 격리가 뚫리면 외부로 나가는 길이 있다는 뜻이고, Interface 엔드포인트만 쓰는 구성과 가장 크게 다른 점이다.
그래서 seccomp를 풀거나, 실패했을 때 격리 없는 채점기로 돌아가는 일은 하지 않는다.

교환 버킷 정책은 TLS와 비공개 접근을 유지하고, 워커가 쓰는 Gateway 엔드포인트를 허용해야 한다. 공유 Gateway 엔드포인트 정책을 바꿀 때는 코드·이미지 레이어·실습 버킷 접근이 깨지지 않는지 먼저 본다.

## 운영

- NAT EC2는 소스/대상 확인이 꺼져 있어야 한다. IP 포워딩과 MASQUERADE는 재부팅 뒤에도 `iptables-restore.service`가 `/etc/iptables/rules.v4`로 다시 건다.
- NAT와 실행기 서브넷은 같은 가용 영역(ap-northeast-2a)에 있다. VM은 다른 가용 영역이라 영역 간 전송은 남아 있다.
- NAT는 단일 장애 지점이고 t3.nano라 처리량이 작다. CPU 크레딧과 연결 상태를 본다.
- NAT를 껐다 켜면 공인 IPv4가 바뀔 수 있다. 라우팅은 ENI를 가리키므로 영향이 없지만, 관리 접속 전에는 주소와 호스트 키를 다시 확인한다.
- API 서버의 Elastic IP를 NAT로 옮기지 않는다.
- NAT가 꺼져 있으면 실행기는 이미지를 받지 못해 채점이 멈춘다. 이것을 이유로 실행기에 다른 우회 경로를 열지 않는다.

참고: [NAT 인스턴스](https://docs.aws.amazon.com/vpc/latest/userguide/VPC_NAT_Instance.html), [보안 그룹 규칙](https://docs.aws.amazon.com/vpc/latest/userguide/security-group-rules.html)
