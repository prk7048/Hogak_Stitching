# VLC TCP/UDP Preview Difference

> Note: this report preserves historical references to legacy `scripts/...` helpers that are no longer kept in the repo.

작성일: 2026-03-13

## 왜 이 문서를 남기나

이번 작업에서 `VLC`로 transmit 영상을 볼 때,
`UDP`로 직접 받는 경우와 `TCP preview`로 받는 경우의 차이가 매우 크게 나타났다.

이 문서는 "실제로 겪은 현상"과 "그래서 어떻게 바꿨는지"를 보존하기 위한 기록이다.

## 실제로 겪은 증상

### 1. UDP transmit를 VLC로 직접 열었을 때

대상 주소:

- `udp://@:24000`

겪은 현상:

- 첫 프레임만 보이고 사실상 멈춘 것처럼 보임
- 화면이 오랫동안 고정되어 있다가 숫자나 장면이 한 번에 점프함
- 어떤 시점에는 초록색 깜빡임도 있었음
- `ffplay`나 probe보다 VLC에서 훨씬 심하게 보였음

로그/진단에서 확인된 특징:

- MPEG-TS continuity 경고가 많이 나왔음
- `picture is too late`
- `buffer deadlock prevented`
- UDP datagram loss가 의심되는 패턴이 있었음

정리:

- "영상 생성이 완전히 멈춘 것"이라기보다
- `VLC가 UDP 본선 스트림을 안정적으로 소비하지 못해서 멈춘 것처럼 보이는 상태`였음

### 2. probe를 ffplay로 봤을 때

대상 주소:

- `udp://127.0.0.1:23000`

겪은 현상:

- transmit UDP를 VLC로 보는 것보다 덜 끊김
- 완전히 정상이라고 하긴 어려워도, VLC/UDP처럼 첫 프레임 뒤 장시간 고정되는 수준은 아니었음

정리:

- `ffplay`는 현재 스트림 조건에서 더 관대하게 재생함
- 그래서 같은 계열의 스트림이라도 VLC보다 덜 멈춰 보였음

### 3. TCP preview를 VLC로 열었을 때

대상 주소:

- `tcp://127.0.0.1:24001`

겪은 현상:

- VLC가 실제로 입력을 열 수 있었음
- UDP direct 경로에서 보이던 TS continuity flood가 없어짐
- 여전히 late picture 경고는 일부 남았지만, UDP direct보다 훨씬 작고 안정적이었음

정리:

- `VLC preview는 UDP보다 TCP가 훨씬 안정적`이라는 결론을 얻음

## 원인을 쉽게 설명하면

비유:

- `UDP`는 택배를 빨리 던져서 보내는 길
- `TCP`는 빠르진 않아도 빠진 상자 없이 순서대로 보내는 길
- `ffplay`는 상자가 조금 구겨져도 대충 열어 보는 편
- `VLC`는 상자가 빠지거나 순서가 흔들리면 더 쉽게 멈칫하는 편

즉 이번 문제는:

- stitch 자체가 완전히 죽은 것보다
- `VLC가 UDP 본선 스트림을 받는 방식`이 현재 환경에서 더 취약했던 것

## 그래서 프로젝트에서 정한 운영 원칙

### 본선과 확인 경로를 분리

본선:

- `udp://127.0.0.1:24000?pkt_size=1316`

probe:

- `udp://127.0.0.1:23000?pkt_size=1316`
- 기본 viewer는 `ffplay`

VLC 확인용 preview:

- `tcp://127.0.0.1:24001`

핵심 원칙:

- 서비스용 빠른 전송은 `UDP`
- `VLC` 확인용은 별도 `TCP preview`
- 즉 `UDP service transmit + local TCP preview leg`

## 코드에서 실제로 바뀐 점

핵심 변경:

- transmit output에 필요 시 `TCP preview leg`를 자동으로 추가
- VLC는 기본적으로 UDP 본선 대신 TCP preview를 보도록 변경
- 수동 VLC 접속도 가능하도록 `--vlc-target tcp://127.0.0.1:24001`만 줘도 preview leg가 생기게 수정

관련 위치:

- `stitching/native_runtime_cli.py`
- `stitching/final_stream_viewer.py`
- `scripts/open_vlc_low_latency.cmd`
- `scripts/diagnose_vlc_transmit.py`

## 중요한 사용 규칙

### VLC로 볼 때

올바른 주소:

- `tcp://127.0.0.1:24001`

잘못된 주소:

- `tcp://@:24001`

설명:

- `@` 형태는 여기서 쓰는 TCP client 주소가 아님
- 수동 VLC는 반드시 `tcp://127.0.0.1:24001`로 열어야 함

### 주의

`24001`은 runtime이 preview leg를 열고 있을 때만 접속 가능하다.

즉:

- runtime을 예전 방식으로 띄웠거나
- 수정 이전 스크립트로 띄웠거나
- runtime을 재시작하지 않았으면

VLC에서 "입력을 열 수 없습니다"가 날 수 있다.

## 최종 결론

이번 경험 기준으로는 아래처럼 기억하면 된다.

- `VLC + UDP direct transmit`는 현재 환경에서 불안정했다
- `VLC + TCP preview`는 훨씬 안정적이었다
- 따라서 `VLC는 TCP preview`, `서비스 transmit는 UDP`로 분리하는 것이 맞다

한 줄 요약:

`VLC는 24000 UDP 본선보다 24001 TCP preview에서 훨씬 안정적으로 동작했다.`
