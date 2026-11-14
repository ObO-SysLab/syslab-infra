# 남은 실행기 정리

조정기가 실행기 작업을 띄운 뒤 죽거나, 작업이 오래 PROVISIONING에 머물거나, StopTask가 실패하면 실행기 작업이 남을 수 있다. `grader/stale_runner_monitor.py`가 이런 작업을 찾아 멈춘다.

운영에서는 Lambda `syslab-runner-monitor`로 돌고, EventBridge 규칙 `syslab-runner-monitor-minute`가 1분마다 부른다. 실패한 호출은 두 번 더 시도하고, 그래도 실패하면 `syslab-runner-monitor-dlq`로 간다. 중지 모드가 켜져 있다(`RUNNER_MONITOR_DRY_RUN=false`).

## 대상 판단

조정기는 RunTask에 `group=diveon:isolated-runner`를 넣는다. 감시는 아래를 모두 만족하는 작업만 실행기로 본다.

- 클러스터 ARN이 정확히 같다.
- 작업 정의가 허용한 리비전 목록에 있다.
- 시작 유형이 FARGATE이고 group이 위 값이다.
- startedBy에 32자리 요청 ID가 있다.

group이 없는 작업은 멈추지 않는다.

## 동작

1. `ListTasks`(desiredStatus=RUNNING)를 끝까지 넘겨 가며 모은다. RUNNING에는 PENDING·PROVISIONING 작업도 들어 있다.
2. `DescribeTasks`로 `createdAt`을 보고, 기준 시간보다 오래된 작업만 고른다. 시작 전에 오래 기다린 작업도 여기서 잡힌다.
3. 고른 작업을 한 번 더 조회한 뒤 `StopTask`를 부른다.
4. 조회나 중지가 실패하면 호출 전체를 실패로 끝내서 다음 실행에서 다시 보게 한다. 남은 실행 시간이 20초보다 적어도 실패로 끝낸다.

결과의 `stopRequested`는 중지를 요청한 수다. 실제로 종료된 것을 확인한 수는 아니다.

## 설정

| 이름 | 내용 |
| --- | --- |
| `RUNNER_MONITOR_CLUSTER` | 실행기 클러스터 ARN |
| `RUNNER_MONITOR_DEFINITIONS` | 허용할 작업 정의 리비전 ARN, 쉼표로 구분. 실행기를 바꾸는 동안은 옛 리비전과 새 리비전을 둘 다 넣는다. |
| `RUNNER_MONITOR_MAX_AGE_SECONDS` | 기준 시간. 기본 900초, 900–3600만 받는다. 조정기 예산 780초보다 길다. |
| `RUNNER_MONITOR_DRY_RUN` | `true`면 찾기만 하고 멈추지 않는다. 기본값은 `true`다. |

실행 역할에는 해당 클러스터의 `ListTasks`, `DescribeTasks`, `StopTask`만 있으면 된다. DB·비밀 값·S3 권한은 필요 없다.
이 모듈은 교환 버킷의 S3 객체를 지우지 않는다.

## 테스트

```sh
python -B -m unittest discover -s grader/tests -p 'test_stale_runner_monitor.py'
```

정상·오래된·다른 작업 구분, 조회 사이에 끝난 작업, 중지 재시도, 페이지 넘김, 잘못된 설정, 실행 시간 부족을 확인한다. AWS는 부르지 않는다.

참고: [ListTasks](https://docs.aws.amazon.com/AmazonECS/latest/APIReference/API_ListTasks.html), [DescribeTasks](https://docs.aws.amazon.com/AmazonECS/latest/APIReference/API_DescribeTasks.html)
