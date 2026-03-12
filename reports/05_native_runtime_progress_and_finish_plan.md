# 네이티브 런타임 전환 진행상황 및 마무리 계획

## 1. 문서 목적
이 문서는 현재 프로젝트가 어디까지 진행되었는지, 왜 지금 구조로 바뀌었는지, 남은 작업이 무엇인지, 최종적으로 어떤 상태를 완료로 볼 것인지를 한 번에 정리하기 위한 상태 보고서다.

이 문서는 특히 아래 질문에 답하도록 작성했다.

- 지금 프로젝트가 어떤 구조로 바뀌고 있는가
- Python은 지금 무엇을 맡고 있는가
- C++ 네이티브 런타임은 어디까지 구현되었는가
- 현재 실제로 동작하는 것과 아직 안 되는 것은 무엇인가
- 마무리까지 어떤 순서로 진행해야 하는가

### 1.1 2026-03-10 재검증 메모

2026-03-10 기준으로 native runtime을 다시 실행해서 아래를 확인했다.

- `native-calibrate` 재실행 성공
  - 결과 homography 출력 해상도: `4710x2215`
  - `matches_count=175`
  - `inliers_count=103`
- 오전 초반 기존 release 바이너리에서는 큰 출력 writer failure가 재현됐다.
- 이후 current source로 release rebuild 후 다시 확인했다.
  - `no-homography 1920x1080` UDP + `h264_nvenc`: 유지
  - `4710x2215` UDP + auto `hevc_nvenc`: 15초 smoke test 유지
  - `4710x2215` file + auto `hevc_nvenc`: 15초 smoke test 유지
  - `4710x2215` file + `libx264`: odd height 문제를 writer pad로 보정한 뒤 유지
- 이어서 `scripts/run_native_runtime_soak.cmd`로 20초 short soak를 다시 돌렸다.
  - `1920x1080` UDP + `h264_nvenc`: `returncode=0`, `shutdown_forced=false`
  - `4710x2215` UDP + auto `hevc_nvenc`: `returncode=0`, `shutdown_forced=false`

즉 현재 결론은 업데이트됐다.

- 입력: 정상
- calibration: 정상
- stitch/GPU warp/GPU blend: 정상
- final output writer: **smoke test 기준 즉시 crash는 해소**
- control channel: `reload_config` subset은 동작
- 다음 리스크: longer soak test, graceful shutdown, manual/recalibration 같은 나머지 제어 경로

---

## 2. 최종 목표

현재 목표로 두고 있는 최종 큰 구조는 아래와 같다.

```text
입력/디코드 -> pair/sync -> stitching -> 인코드 -> 네트워크 전송
```

여기서 중요한 점은 최종 목표가 `mp4 파일 생성`이 아니라 **실시간 output stream 송출**이라는 것이다.
file/mp4 출력은 인코더 검증, 회귀 확인, 샘플 저장을 위한 보조 경로다.

이 중 **메인 hot path**는 C++ 네이티브 런타임이 담당한다.

Python은 최종적으로 아래 역할만 담당한다.

- 설정 입력
- 시작/중지
- 상태/로그 표시
- 초기 보정 및 재보정 트리거
- 최종 출력 스트림 viewer 실행

즉, Python은 **control plane**, C++ 런타임은 **data plane / video engine** 역할이다.

---

## 3. 지금까지의 핵심 의사결정

### 3.1 Python/OpenCV 중심 구조를 유지하지 않기로 한 이유
초기 구조는 Python + OpenCV 중심이었다.

이 구조는 다음에는 강점이 있었다.

- 빠른 실험
- 수동 보정 UI
- 매칭/스티칭 알고리즘 탐색
- 로그/모니터링 추가

하지만 실시간 성능 측면에서 구조적 한계가 확인되었다.

핵심 문제는 다음과 같았다.

1. RTSP 프레임이 결국 Python `numpy.ndarray`로 올라옴
2. Python 쪽 버퍼링/동기화/선택 로직이 hot path에 존재함
3. GPU를 써도 `host ndarray -> GpuMat upload` 경계가 계속 남음
4. FFmpeg direct를 붙여도 결국 raw frame이 Python으로 들어오면 병목이 남음

실제로 headless benchmark와 FFmpeg direct 경로 비교에서, 단순히 UI를 끈다고 60fps 방향으로 가는 것이 아님이 확인되었다.

### 3.2 FFmpeg direct를 붙여도 충분하지 않았던 이유
OpenCV 내부 경로 대신 FFmpeg를 직접 제어하는 입력 경로를 붙였지만, 초기 구현은 다음 구조였다.

```text
RTSP -> ffmpeg decode -> rawvideo stdout -> Python bytes -> numpy.ndarray -> GPU upload
```

즉 FFmpeg를 직접 썼지만 **Python raw frame 경계**는 여전히 남아 있었다.

그 결과:

- 입력 fps가 Python 경계에서 제한됨
- worker fps가 기대만큼 오르지 않음
- Python hot path 제거가 되지 않음

따라서 결론은 단순했다.

> FFmpeg를 직접 쓰는 것만으로는 부족하고, 프레임 소유권 자체를 Python 밖으로 옮겨야 한다.

### 3.3 네이티브 런타임 분리 결정
이후 핵심 결론은 다음이었다.

- Python이 큰 프레임을 직접 소유하면 안 된다
- 입력/디코드/buffer/sync/stitch/output은 네이티브 런타임이 가져가야 한다
- Python은 제어/UI/로그만 맡는다

그 결과 현재는 `native_runtime/` 프로젝트를 추가해서 C++ 엔진을 따로 만드는 방향으로 전환했다.

---

## 4. 현재 아키텍처

### 4.1 현재 목표 아키텍처

```text
Python UI / Control
  - 설정
  - 시작/중지
  - 상태/로그
  - 보정 트리거
  - final stream viewer 실행

C++ Native Runtime
  - RTSP 입력
  - ffmpeg 기반 디코드
  - pair / sync
  - stitching
  - ffmpeg 기반 인코드
  - output stream 송출

Viewer
  - 최종 output stream을 외부 클라이언트처럼 구독해서 재생
```

### 4.2 결과 확인 방식
Python이 full-rate raw stitched frame을 직접 받지 않는다.

대신:

- C++ 런타임이 최종 output stream을 송출하고
- Python 또는 `ffplay`가 그 스트림을 **외부 클라이언트처럼** 본다

이 방식은 메인 엔진에 간섭이 적고, 실제로 전송되는 최종 결과를 그대로 확인할 수 있다는 장점이 있다.

---

## 5. 현재 완료된 작업

### 5.1 Python 쪽 정리
현재 Python은 다음 요소를 갖고 있다.

- 기존 `stitching/` 코드베이스
- `runtime_contract.py`
- `runtime_launcher.py`
- `runtime_client.py`
- `native_runtime_cli.py`
- `final_stream_viewer.py`
- `native_calibration.py`

이 중 현재 active한 역할은 아래와 같다.

1. 네이티브 런타임 실행
2. JSON line 기반 이벤트 수신
3. 로그/메트릭 출력
4. 초기 calibration 후 homography 저장
5. final stream viewer 실행

### 5.2 Native runtime 프로젝트 생성
새 프로젝트가 생성되었다.

```text
native_runtime/
  CMakeLists.txt
  CMakePresets.json
  include/
  src/
```

빌드 산출물:

```text
native_runtime\build\windows-release\Release\stitch_runtime.exe
```

### 5.3 입력/디코드 네이티브화
네이티브 런타임이 직접 RTSP를 읽고, ffmpeg subprocess를 통해 디코드한다.

핵심 파일:

- `src/input/ffmpeg_rtsp_reader.*`
- `src/platform/win_process_pipe.*`

현재 확인된 상태:

- left/right input fps는 대략 30fps 유지
- stale drop, pair skew, worker fps가 네이티브 쪽에서 계산됨

### 5.4 pair/sync 네이티브화
좌/우 프레임 선택, stale drop, pair skew 계산이 Python이 아니라 C++ 엔진 안으로 들어갔다.

즉 현재는:

- Python deque 기반 sync가 아님
- native runtime 내부 pair/sync 로직 기준

### 5.5 최소 stitching 파이프라인 구현
네이티브 런타임 안에 최소 stitch path가 들어가 있다.

현재 상태:

- calibration homography 로드 가능
- GPU warp 사용
- feather blend 사용
- 현재 feather blend는 GPU 경로로 전환됨

실제로 로그에서 확인된 상태:

- `gpu_warp_count` 증가
- `gpu_blend_count` 증가
- `cpu_blend_count=0`

즉 현재 feather runtime blend는 GPU 경로가 정상이다.

### 5.6 검은 화면 문제 해결
중간에 full-overlap 케이스에서 `stitched_mean_luma=0.00`이 나오는 문제가 있었고, 이 문제는 수정되었다.

현재는 실제로:

- `stitched_mean_luma`가 70 이상
- 즉 stitched frame이 검은 프레임이 아님

### 5.7 output stream writer 연결
native runtime이 ffmpeg output writer를 통해 최종 스트림을 송출한다.

관련 파일:

- `src/output/ffmpeg_output_writer.*`
- `src/platform/win_process_sink.*`

현재 확인된 상태:

- `no-homography 1920x1080`에서는 `output_active=True` 유지
- `output_frames_written`가 지속 증가
- current source rebuild 후 큰 stitched output도 smoke test에서는 유지
- `libx264`는 odd height 조건에서 실패했지만 writer pad 적용 뒤 file 출력 유지

즉 **송출 경로 자체는 현재 smoke test 기준으로 동작하며, 다음 단계는 soak/운영 안정화다.**

### 5.8 final stream viewer 준비
`ffplay` 기반 최종 스트림 viewer 경로를 만들었다.

스크립트:

- `scripts/run_native_runtime.cmd`
- `scripts/run_native_runtime.cmd --no-viewer`

이 구조는:

- runtime 로그 확인
- 최종 스트림 확인
을 분리해서 검증할 수 있게 한다.

즉 Python은 이제 runtime 전용 control/launcher 역할을 실제로 수행하기 시작했다.

---

## 6. 현재 실제로 확인된 동작

최근 로그 기준으로 확인된 사실:

1. 입력 정상
- `left_fps ≈ 30`
- `right_fps ≈ 29~30`

2. calibration homography 적용 후 큰 stitched output 생성
- 최근 재검증 예: `output_width=4710`
- 최근 재검증 예: `output_height=2215`
- `calibrated=True`

3. stitched frame 자체는 정상
- `stitched_mean_luma ≈ 70`
- `left_mean_luma ≈ 117`
- `right_mean_luma ≈ 109`
- `warped_mean_luma ≈ 63`

4. GPU warp/blend 정상
- `gpu_warp_count` 증가
- `gpu_blend_count` 증가
- `cpu_blend_count=0`

즉 **실제 stitched frame 생성 자체는 현재 정상**이라고 판단할 수 있다.

5. writer 안정성은 current source rebuild 이후 smoke test에서 개선됨
- `1920x1080` 기본 출력은 송출 유지
- `4710x2215` 보정 출력도 UDP/file 경로에서 유지 확인
- `libx264`는 odd height 제약이 있었고, writer pad 적용 후 유지 확인

---

## 7. 현재 남아 있는 핵심 문제

현재 가장 중요한 미해결 이슈는 1개다.

### 7.1 smoke test 이후 남은 운영 안정화
current source rebuild와 writer pad 수정 이후, 기존의 immediate writer failure는 smoke test에서 재현되지 않았다.

현재 남은 문제는 다음 쪽으로 이동했다.

1. 더 긴 soak test에서 송출이 계속 유지되는지
2. 종료 시점 return code와 flush가 일관적인지
3. codec / mux / target 조합별 운영 기준을 어디까지 보장할지

즉 현재 엔진은 큰 경로까지 일단 돌아가기 시작했고,
이제는 **즉시 crash 디버깅**보다 **운영 안정화와 기준 확정**이 우선이다.

---

## 8. 진행률 평가

대략적으로 보면 현재 진행 상황은 아래 정도로 평가할 수 있다.

### 8.1 구조 전환
- Python hot path 제거 방향 결정: 완료
- native runtime skeleton: 완료
- 입력/디코드 native 이전: 완료
- pair/sync native 이전: 완료
- 최소 stitch path native 이전: 완료

=> 구조 전환 자체는 **상당 부분 완료**

### 8.2 품질/정렬
- calibration pipeline 연결: 1차 완료
- homography load: 완료
- full-overlap/black frame 문제: 해결

=> 품질 쪽은 **기본 경로 확보**

### 8.3 출력/운영
- output writer 코드: 구현
- final stream viewer helper: 구현
- end-to-end 안정 송출: 아직 미완료

=> 운영/마무리 단계는 **아직 진행 중**

실무적으로 평가하면:

> 구조 전환은 절반을 넘겼고, 지금부터는 “마무리 안정화” 단계로 들어간 상태다.

---

## 9. 남은 단계

아래 순서대로 진행하는 것이 맞다.

### 9.1 1순위: soak test와 송출 운영 기준 확정
목표:

- 긴 구간에서도 송출 유지 확인
- codec / mux / target 조합별 통과 기준 확정
- 종료 시 flush / return code / stderr 처리 정리

완료 조건:

- calibration된 large stitched output에서 장시간 `output_frames_written` 증가 유지
- `output_active=True` 유지
- viewer/file/udp 조합별 결과가 문서화됨

### 9.2 2순위: final stream viewer 경로 안정화
목표:

- `run_native_runtime.cmd`
경로를 안정적으로 정리

완료 조건:

- 사용자가 명령 1~2개로
  - 로그 확인
  - 최종 결과 확인
를 안정적으로 할 수 있음

### 9.3 3순위: Python 제어/UI 정리
목표:

- 기존 Python 경로 중 hot path 관련 부분은 더 이상 중심이 아니게 정리
- Python은 control plane으로 역할 고정

완료 조건:

- Python은 runtime launcher / metrics monitor / viewer helper / calibration tool 역할만 함

### 9.4 4순위: 수동 보정 / 재보정 경로
목표:

- manual calibration command
- recalibration trigger
- drift recovery flow

완료 조건:

- runtime을 재시작하지 않고도 재보정 flow 설계/연결 가능

### 9.5 5순위: 문서 / 실행 경로 정리
목표:

- 현재 구조에 맞는 실행법 문서화
- `reports/` 갱신
- setup/usage 문서 정리

완료 조건:

- 제3자가 보고도
  - 어떻게 실행하는지
  - 어디를 수정하는지
  - 무엇이 남았는지
를 이해할 수 있음

---

## 10. 완료 기준

이 프로젝트의 이번 전환 작업을 “일단 마무리되었다”고 볼 수 있는 기준은 아래와 같다.

### 필수 완료 조건
1. native runtime이 RTSP 두 개를 안정적으로 읽는다
2. calibration homography를 적용해 stitched frame을 만든다
3. GPU warp + GPU blend가 정상 동작한다
4. output writer가 중간에 죽지 않고 최종 스트림을 지속 송출한다
5. final viewer에서 실제 결과를 확인할 수 있다
6. Python은 control plane 역할만 수행한다

### 이후 확장 조건
1. manual calibration/recalibration
2. output stream 품질/지연 튜닝
3. 장기적으로는 더 강한 runtime 분리 또는 Linux/GStreamer 방향 검토

---

## 11. 지금 시점의 요약

지금까지의 상태를 한 문장으로 요약하면:

> Python 중심 실시간 프로토타입에서 C++ 네이티브 런타임 기반 구조로 실질적인 전환은 성공했고, 현재는 최종 output stream writer를 안정화하는 마무리 단계에 있다.

더 짧게 말하면:

- 입력: 됨
- 스티칭: 됨
- GPU warp/blend: 됨
- calibration 적용: 됨
- 최종 스트림 송출: 아직 불안정
- viewer/운영 정리: 진행 중

---

## 12. 바로 다음 액션

가장 먼저 해야 하는 것은 이것이다.

1. longer soak test 매트릭스 확정
2. 종료 시 return code / flush 동작 정리
3. control channel 확장

이 문제가 해결되면,
이번 구조 전환 작업은 사실상 핵심 경로가 끝났다고 볼 수 있다.
