# GPU Direct Encode Dependency Options

작성일: 2026-03-13

## 왜 이 문서를 남기나

`05_60fps_service_pipeline_plan.md` 기준 다음 큰 단계는
`GpuMat -> GPU processing -> NVENC -> transmit` 경로를 실제로 만드는 것이다.

그런데 이 단계에 들어가려면,
"GPU에서 바로 인코딩하는 엔진을 무엇으로 붙일지"를 먼저 정해야 한다.

현재 유력 후보는 아래 두 가지다.

1. `FFmpeg/libavcodec + NVENC`
2. `NVIDIA Video Codec SDK direct`

이 문서는 두 후보의 특징, 장단점, 현재 프로젝트와의 궁합을
쉽게 다시 꺼내볼 수 있도록 영구 보존용으로 남긴 기록이다.

## 현재 프로젝트 기준 전제

현재 본선 경로는 대략 아래와 같다.

`RTSP -> external ffmpeg rawvideo -> CPU frame -> GPU stitch -> CPU download -> external ffmpeg rawvideo stdin -> NVENC -> UDP`

즉 문제는 GPU stitch 이후에도
CPU 왕복과 외부 `ffmpeg` 프로세스 경계가 너무 많다는 점이다.

우리가 원하는 다음 구조는 아래다.

`GPU stitch -> GPU frame 유지 -> in-process encode -> transmit`

## 후보 1. FFmpeg/libavcodec + NVENC

### 한 줄 설명

`ffmpeg.exe` 외부 프로세스를 계속 쓰는 대신,
프로젝트 안에서 `libavcodec/libavformat` API를 직접 호출해서
`NVENC` 인코딩과 `TS/UDP/TCP` 출력까지 처리하는 방법이다.

### 쉽게 비유하면

- 지금: 다른 공장에 물건을 보내서 포장 맡김
- 이 방식: 우리 공장 안에 포장 라인을 들여옴

### 장점

- 현재 프로젝트가 이미 `FFmpeg` 개념과 출력 포맷을 많이 쓰고 있어서 방향이 자연스럽다.
- `UDP`, `MPEG-TS`, 이후 `SRT`나 다른 muxer/transport 확장도 비교적 익숙한 도구 안에서 갈 수 있다.
- `libavformat`이 muxing과 packet writing을 맡아주므로,
  인코딩뿐 아니라 "어떻게 보내는지"까지 한 경로 안에서 다루기 쉽다.
- 현재 `FfmpegOutputWriter`와 개념적으로 가까워서,
  완전 신설보다 점진적 교체가 쉽다.
- 장기적으로는 `NVDEC`, `hw_frames_ctx`, hardware frame path 같은 확장도 같은 생태계 안에서 묶기 좋다.

### 단점

- `libavcodec` API는 복잡하다.
- `AVFrame`, `AVCodecContext`, `hw_device_ctx`, `hw_frames_ctx` 설정이 까다롭다.
- `cv::cuda::GpuMat`을 `FFmpeg` 하드웨어 프레임으로 자연스럽게 넘기는 작업이 쉽지 않다.
- Windows 빌드와 링크 구성이 복잡해질 수 있다.
- 문서와 예제가 제각각이라 초기 진입 비용이 크다.

### 현재 프로젝트와 잘 맞는 점

- 이미 `output` 개념이 `muxer`, `transport`, `target URL` 중심으로 짜여 있다.
- 우리가 원하는 건 "인코더만 바꾸는 것"이 아니라
  "본선 transmit 전체를 CPU rawvideo pipe 밖으로 꺼내는 것"이기 때문에,
  `libavcodec + libavformat` 경로가 전체 파이프라인 관점에서 더 자연스럽다.

### 현재 프로젝트와 안 맞을 수 있는 점

- OpenCV `GpuMat`에서 FFmpeg hardware frame으로 바로 연결하는 부분은 별도 브리지 설계가 필요하다.
- 잘못 붙이면 "in-process로 옮겼지만 내부 복사가 여전히 많은" 반쪽짜리 최적화가 될 수 있다.

### 이런 경우 유리하다

- `NVENC`뿐 아니라 `UDP/TS/TCP/SRT` 같은 출력 경로도 같이 정리하고 싶을 때
- 기존 `ffmpeg` 기반 지식을 최대한 재사용하고 싶을 때
- fallback/debug 경로와 production 경로를 같은 family 안에서 관리하고 싶을 때

## 후보 2. NVIDIA Video Codec SDK direct

### 한 줄 설명

`libavcodec`를 거치지 않고,
`NVIDIA Video Codec SDK`를 직접 붙여서
GPU 프레임을 바로 `NVENC`로 인코딩하는 방법이다.

### 쉽게 비유하면

- 지금: 포장 공정을 다른 회사 규격에 맞춰서 씀
- 이 방식: 우리 공장에 맞는 전용 포장기를 직접 설계해서 붙임

### 장점

- 제어권이 가장 크다.
- 가장 낮은 지연과 가장 공격적인 최적화를 노리기 좋다.
- GPU frame 관리, encoder session 설정, bitrate/GOP/latency 정책을 세밀하게 만질 수 있다.
- 잘 붙이면 "가장 빠른 길"이 될 가능성이 크다.

### 단점

- 개발 비용이 가장 크다.
- 인코딩은 해결돼도, 그 뒤 `MPEG-TS` muxing과 `UDP/TCP/SRT` 전송은 별도로 만들어야 한다.
- 즉 "인코더만 붙이면 끝"이 아니라, 나머지 packetization/transport 계층도 같이 책임져야 한다.
- 유지보수 난이도가 높다.
- NVIDIA 전용 의존성이 강해진다.
- 디버깅과 회귀 대응이 어려워질 수 있다.

### 현재 프로젝트와 잘 맞는 점

- 최종 목표가 정말 `2x60 -> 60fps service`라면,
  가장 높은 성능 잠재력을 가진 후보라는 점은 분명하다.
- `GpuMat` 중심 사고방식과도 궁합이 좋다.

### 현재 프로젝트와 안 맞을 수 있는 점

- 현재 프로젝트는 아직 "송출 전체를 자체 구현"하는 쪽으로 준비가 다 끝난 상태가 아니다.
- 직접 SDK 경로로 가면, encoder뿐 아니라 mux/network도 상당 부분 새로 짜야 할 수 있다.
- 즉 지금 당장 바로 붙이기엔 공사 범위가 너무 커질 위험이 있다.

### 이런 경우 유리하다

- 정말 최종 성능이 가장 중요하고,
  중간 레이어 추상화 비용도 아깝다고 판단할 때
- FFmpeg 계층의 제약 없이 encoder를 완전히 직접 다루고 싶을 때
- 장기적으로 NVIDIA 중심 서비스 경로를 확정할 때

## 둘을 쉽게 비교하면

### FFmpeg/libavcodec + NVENC

- 장점: 기존 구조와 이어 붙이기 쉽다
- 장점: muxing/transport까지 같이 다루기 좋다
- 단점: API가 복잡하고 hardware frame 연결이 까다롭다
- 성격: 현실적인 1차 본선 재구성 후보

### NVIDIA Video Codec SDK direct

- 장점: 성능 잠재력이 가장 높다
- 장점: 지연/제어 측면에서 가장 공격적이다
- 단점: 공사 범위와 유지보수 부담이 가장 크다
- 성격: 최종 최고 성능 후보, 하지만 초기 진입비용이 큼

## 현재 판단

현재 프로젝트 기준으로는
`FFmpeg/libavcodec + NVENC`를 먼저 검토하는 쪽이 더 현실적이다.

이유:

1. 이미 프로젝트가 `FFmpeg` 중심 출력 개념을 많이 쓰고 있다.
2. 우리는 encoder만 필요한 게 아니라 `transmit path 전체`를 다시 묶어야 한다.
3. `UDP/TS/TCP`를 포함한 송출 계층까지 생각하면 `libavformat`의 도움을 받는 편이 유리하다.
4. `Video Codec SDK direct`는 최종 성능 잠재력은 높지만, 지금 단계에서는 공사 범위가 너무 커질 가능성이 높다.

즉 현재 권장 순서는 아래다.

1. 1차 목표: `FFmpeg/libavcodec + NVENC` 기반 in-process writer 검토
2. 2차 목표: 필요 시 `NVIDIA Video Codec SDK direct`를 더 낮은 지연/더 높은 fps용 최종 카드로 검토

## 단, 주의할 점

이 문서는 "무조건 1번이 정답"이라는 뜻은 아니다.

핵심은:

- `1번`은 더 현실적인 첫 구현 경로
- `2번`은 더 공격적인 최종 고성능 경로

즉 지금 단계에서는
"빨리, 그러나 구조적으로 맞는 방향"으로 가려면 1번 쪽이 낫고,
"최종 끝판왕 성능"만 보면 2번이 더 매력적일 수 있다.

## 다음 액션

현재 기준 다음 실제 확인 순서는 아래다.

1. `libavcodec/libavformat`를 현재 Windows/native build에 안정적으로 붙일 수 있는지 확인
2. `NVENC hw frames` 경로와 `GpuMat bridge`가 어디까지 가능한지 작은 prototype로 확인
3. 성공하면 `gpu_direct_output_writer`의 1차 실제 구현을 `libavcodec + NVENC`로 진행
4. 막히는 지점이 너무 크면 `Video Codec SDK direct`를 대안 경로로 재검토

## 한 줄 결론

현재 프로젝트 기준으로는
`FFmpeg/libavcodec + NVENC`가 더 현실적인 첫 번째 본선 재구성 후보이고,
`NVIDIA Video Codec SDK direct`는 더 큰 공사비를 감수하는 대신 더 높은 최종 성능을 노리는 후보다.
