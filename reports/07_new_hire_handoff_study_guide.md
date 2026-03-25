# Hogak Stitching 신입 인수인계 / 과외 문서

이 문서는 "컴퓨터과학과를 막 졸업했고, 실무 프로젝트 경험은 거의 없는 신입 개발자"를 기준으로 쓴다.

목표는 두 가지다.

1. 이 프로젝트가 무엇을 하는지 처음부터 이해하게 만들기
2. 코드 구조와 기술 용어를 겁먹지 않고 읽을 수 있게 만들기

이 문서는 공부용 문서다.
즉, 짧게 요약하는 문서가 아니라 "왜 이런 구조인지"까지 자세히 설명한다.

---

## 1. 이 프로젝트는 무엇을 하는가

이 프로젝트는 **두 대의 IP 카메라 영상**을 받아서, 그것을 **하나의 이어진 영상으로 합친 뒤 실시간으로 송출**하는 프로그램이다.

조금 더 쉬운 말로 하면:

- 왼쪽 카메라 영상이 들어온다
- 오른쪽 카메라 영상이 들어온다
- 둘을 한 화면처럼 붙인다
- 그 결과를 다시 네트워크로 보낸다

즉 이 프로젝트의 핵심은:

- **실시간 입력**
- **영상 정렬**
- **영상 합성**
- **실시간 출력**

이다.

---

## 2. 왜 이런 프로젝트가 어려운가

사진 두 장을 붙이는 건 생각보다 쉽다.
하지만 **실시간 영상 두 개를 계속 붙이는 것**은 훨씬 어렵다.

왜냐하면 다음 문제가 같이 있기 때문이다.

- 카메라 두 대의 시간이 정확히 똑같지 않다
- 네트워크로 영상이 오기 때문에 지연이 생긴다
- 영상 한 장 크기가 크다
- 매 프레임마다 연산량이 많다
- 결과를 다시 압축해서 보내야 한다
- 끊기면 안 된다

그래서 이 프로젝트는 단순히 "영상 처리"만 하는 게 아니다.
실제로는 아래를 동시에 다룬다.

- 네트워크 스트리밍
- 영상 디코딩
- 프레임 동기화
- 기하학적 변환
- 영상 합성
- GPU 가속
- 실시간 인코딩
- 운영 모니터링

---

## 3. 이 프로젝트의 큰 구조

이 프로젝트는 크게 두 부분으로 나뉜다.

### 3-1. Python 쪽

Python은 **지휘실**에 가깝다.

맡는 일:

- 설정 읽기
- calibration 실행
- runtime 실행
- 모니터 UI / 콘솔 로그
- 운영자가 보기 쉬운 흐름 정리

대표 파일:

- [cli.py](/c:/Users/Pixellot/Hogak_Stitching/stitching/cli.py)
- [native_runtime_cli.py](/c:/Users/Pixellot/Hogak_Stitching/stitching/native_runtime_cli.py)
- [native_calibration.py](/c:/Users/Pixellot/Hogak_Stitching/stitching/native_calibration.py)

### 3-2. C++ 쪽

C++는 **실제 엔진**이다.

맡는 일:

- RTSP 입력 받기
- frame pair 고르기
- stitch 계산하기
- encode 하기
- output 송출하기

대표 파일:

- [runtime_main.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/app/runtime_main.cpp)
- [stitch_engine.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/engine/stitch_engine.cpp)
- [ffmpeg_rtsp_reader.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/input/ffmpeg_rtsp_reader.cpp)
- [gpu_direct_output_writer.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/output/gpu_direct_output_writer.cpp)

짧게 기억하면:

- Python = orchestration
- C++ = performance-critical runtime

---

## 4. 이 프로젝트가 실제로 하는 일을 한 줄 흐름으로 쓰면

현재 메인 runtime 흐름은 대략 이렇다.

```text
RTSP 입력 -> ffmpeg reader -> frame pair/sync -> homography warp -> feather blend -> encode -> network output
```

이걸 한 단계씩 풀어보자.

1. 카메라 두 대가 RTSP로 영상을 보낸다
2. ffmpeg가 그 영상을 읽고 rawvideo로 풀어준다
3. runtime이 왼쪽/오른쪽에서 쓸 프레임 한 쌍을 고른다
4. calibration에서 구한 homography로 오른쪽 영상을 변형한다
5. 왼쪽/오른쪽을 겹치는 구간에서 부드럽게 섞는다
6. 결과 프레임을 H.264로 인코딩한다
7. UDP 같은 target으로 다시 송출한다

---

## 5. RTSP가 무엇인가

### 5-1. RTSP란

RTSP는 **Real Time Streaming Protocol**의 줄임말이다.

쉽게 말하면:

- 네트워크 카메라가
- "실시간 영상"을 보내기 위해 자주 쓰는 방식

이다.

예를 들면 카메라 주소가 이런 식이다.

```text
rtsp://admin:password@192.168.0.10:554/cam/realmonitor?channel=1&subtype=0
```

여기서 보통 뜻은 이렇다.

- `rtsp://` = RTSP 프로토콜
- `admin:password` = 카메라 로그인 정보
- `192.168.0.10` = 카메라 IP 주소
- `554` = RTSP 포트
- 뒤의 path/query = 카메라 제조사별 스트림 경로

### 5-2. RTSP는 왜 쓰는가

이 프로젝트는 카메라에서 **계속 들어오는 live 영상**이 필요하다.
파일을 읽는 게 아니라, 지금 이 순간의 카메라 영상을 받아야 한다.

그럴 때 RTSP가 적합하다.

### 5-3. RTSP는 영상 파일이 아니다

중요하다.

RTSP는 `.mp4` 같은 저장 파일이 아니라:

- "지금 흘러오는 영상"
- "네트워크로 주고받는 스트림"

이다.

그래서 다룰 때는:

- 네트워크 지연
- 패킷 손실
- 카메라 리듬 흔들림

같은 문제를 같이 생각해야 한다.

---

## 6. RTSP에도 transport가 있다: UDP와 TCP

RTSP는 실제로 내부에서 영상 데이터를 가져올 때 보통 `udp` 또는 `tcp`를 쓴다.

### UDP

장점:

- 빠르다
- 지연이 적다

단점:

- 패킷 손실이 나도 다시 챙겨주지 않는다
- Wi-Fi에서 흔들리면 frame cadence가 깨질 수 있다

### TCP

장점:

- 손실 복구가 상대적으로 낫다
- 안정성이 더 좋을 수 있다

단점:

- 지연이 늘 수 있다
- 실시간성은 UDP보다 나쁠 수 있다

이 프로젝트는 현재 baseline에서 보통:

- 입력 RTSP transport = `udp`

를 기본으로 잡고 있다.

---

## 7. config가 무엇인가

`config`는 쉽게 말하면 **설정 파일 모음**이다.

코드 안에 하드코딩하지 말고, 바깥 파일에 적어둔 값들이라고 보면 된다.

예를 들면:

- 카메라 주소
- 몇 fps로 보낼지
- 어디로 송출할지
- 어떤 preset을 쓸지

이런 걸 `config`에 둔다.

대표 파일:

- [runtime.json](/c:/Users/Pixellot/Hogak_Stitching/config/runtime.json)
- [config/README.md](/c:/Users/Pixellot/Hogak_Stitching/config/README.md)

### 왜 config가 중요한가

코드 안에 카메라 주소가 직접 적혀 있으면:

- 다른 현장으로 옮길 때 코드 수정 필요
- 다른 개발자 PC에서 바로 안 됨
- 운영자가 다루기 어려움

그래서 이 프로젝트는:

- 코드
- 설정

을 분리했다.

### runtime.json

기본 운영 설정이다.

여기엔 보통:

- `left_rtsp`
- `right_rtsp`
- `probe target`
- `transmit target`
- `input_buffer_frames`
- `output_cadence_fps`

같은 값이 들어 있다.

### runtime.local.json

로컬 PC 전용 override다.

즉:

- repo에는 안 올리고
- 내 PC에서만 쓸 값

을 여기에 둔다.

예를 들면 실제 카메라 IP 같은 값이다.

### profiles

`config/profiles/*.json`은 **기본 설정 위에 덮어쓰는 작은 모드 카드**라고 생각하면 된다.

예:

- `prod.json`
- `dev.json`
- `camera25.json`

예를 들어 `camera25.json`은 output cadence를 25fps로 맞추는 역할을 한다.

---

## 8. bin은 무엇인가

`bin`은 보통 **실행 파일(binary)** 를 뜻한다.

쉽게 말하면:

- 소스코드 `.cpp`, `.py`는 사람이 읽는 원본
- `bin`, `.exe`는 컴퓨터가 실행하는 결과물

이다.

이 프로젝트에서 실제 네이티브 실행파일은 보통 여기 있다.

```text
native_runtime\build\windows-release\Release\stitch_runtime.exe
```

즉:

- Python 코드는 스크립트로 바로 실행
- C++ 코드는 빌드 후 `.exe`가 됨

---

## 9. build와 run은 무엇이 다른가

신입이 많이 헷갈리는 부분이라 분리해서 설명한다.

### build

소스코드를 실행파일로 만드는 과정이다.

예:

- `.cpp`
- `.h`
- OpenCV
- FFmpeg
- CUDA

를 합쳐서 `stitch_runtime.exe`를 만드는 일

### run

이미 만들어진 프로그램을 실행하는 과정이다.

즉:

- build = 만들기
- run = 돌리기

이 프로젝트는 특히 C++ 네이티브 런타임이 있으므로 build가 중요하다.

---

## 10. CMake는 무엇인가

CMake는 **C++ 프로젝트를 빌드하기 위한 설정 도구**다.

이 프로젝트에서는:

- 어떤 파일들을 컴파일할지
- OpenCV를 어디서 찾을지
- FFmpeg dev files를 어디서 찾을지
- CUDA Toolkit을 어디서 찾을지

같은 것을 관리한다.

대표 파일:

- [CMakeLists.txt](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/CMakeLists.txt)
- [CMakePresets.json](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/CMakePresets.json)

### CMake Preset

Preset은 "이렇게 빌드해"라는 저장된 빌드 옵션 묶음이다.

예:

- `windows-release`
- `build-windows-release`

를 쓰면, 긴 빌드 명령을 매번 다 적지 않아도 된다.

---

## 11. OpenCV는 여기서 뭘 하는가

OpenCV는 컴퓨터 비전/영상 처리 라이브러리다.

이 프로젝트에서는 많이 쓴다.

예:

- 색 변환
- resize
- warpPerspective
- homography 관련 계산
- calibration preview
- 영상 합성 보조 처리

중요한 점:

이 프로젝트는 **OpenCV의 고수준 Stitcher를 쓰는 구조가 아니다.**
즉 `cv::Stitcher`가 알아서 다 붙여주는 구조가 아니라,

- homography 계산
- warp
- blend

를 직접 조합한 구조다.

---

## 12. CUDA는 무엇인가

CUDA는 NVIDIA GPU를 계산에 쓰기 위한 기술이다.

쉽게 말하면:

- CPU는 범용 두뇌
- GPU는 병렬 계산에 강한 두뇌

인데, CUDA는 그 GPU를 프로그래밍해서 쓰게 해주는 환경이다.

이 프로젝트는 영상 처리량이 많기 때문에 GPU를 적극 활용한다.

예:

- warp
- blend
- encode 쪽 일부 경로

### CUDA가 필요한 이유

영상 한 장 붙이는 것도 계산이 많은데,
그걸 실시간으로 초당 수십 번 해야 한다.

그래서 CPU만으로는 부담이 커지고,
GPU로 넘겨야 성능이 좋아진다.

---

## 13. "기본 CUDA"와 "ffmpeg-cuda"는 뭐가 다른가

이건 중요한 개념이다.

### 기본 CUDA

보통 "CUDA를 쓴다"고 하면:

- 우리가 직접 GPU 메모리를 쓰거나
- OpenCV CUDA 함수로 처리하거나
- custom kernel로 처리하는 것

을 뜻한다.

즉 **GPU 계산 자체**에 가까운 말이다.

### ffmpeg-cuda

`ffmpeg-cuda`는 "CUDA라는 기술을 ffmpeg가 활용하는 모드"라고 생각하면 된다.

즉 별도의 새로운 CUDA가 아니라:

- ffmpeg가
- NVIDIA 하드웨어 디코드/가속을 쓰면서
- 입력 영상을 다루는 방식

이다.

이 프로젝트에서 `input_runtime = ffmpeg-cuda`는 대략:

- RTSP 입력을 ffmpeg가 받음
- 가능한 경우 CUDA/NVDEC 경로를 사용해 decode 쪽 부담을 줄임

을 의미한다.

### 쉽게 구분하면

- CUDA = GPU 계산 기술 전체
- ffmpeg-cuda = ffmpeg가 CUDA/NVIDIA 가속을 활용하는 입력 경로

---

## 14. FFmpeg는 여기서 무슨 역할인가

FFmpeg는 영상/오디오 처리계의 스위스 군용 칼 같은 도구다.

이 프로젝트에서는 크게 두 군데에서 중요하다.

### 14-1. 입력

RTSP를 받아서 디코드한다.

즉:

- 카메라 스트림 받기
- 영상 압축 해제하기
- rawvideo로 내보내기

를 맡는다.

관련 코드:

- [ffmpeg_rtsp_reader.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/input/ffmpeg_rtsp_reader.cpp)

### 14-2. 출력

최종 stitched frame을 인코딩해서 네트워크로 보낸다.

즉:

- H.264/H.265 인코딩
- UDP/RTSP/RTMP 같은 target 송출

를 맡는다.

관련 코드:

- [ffmpeg_output_writer.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/output/ffmpeg_output_writer.cpp)
- [gpu_direct_output_writer.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/output/gpu_direct_output_writer.cpp)

---

## 15. libav는 무엇인가

FFmpeg를 라이브러리 형태로 쓰는 쪽이라고 생각하면 된다.

파일에 보면 이런 이름이 나온다.

- `libavcodec`
- `libavformat`
- `libavutil`
- `libswscale`

이건 FFmpeg의 내부 라이브러리들이다.

즉:

- `ffmpeg.exe`를 외부 프로세스로 띄워서 쓰는 방법도 있고
- C++ 코드 안에 `libav`를 직접 붙여서 쓰는 방법도 있다

이 프로젝트는 둘 다 쓴다.

### 입력 쪽

주로 외부 ffmpeg subprocess reader

### 출력 쪽

특히 `gpu-direct`는 libav API를 직접 붙여서 in-process로 돌린다

---

## 16. NVENC는 무엇인가

NVENC는 NVIDIA GPU에 있는 **하드웨어 인코더**다.

쉽게 말하면:

- 영상을 H.264/H.265로 압축하는 일을
- CPU 대신 GPU 쪽 하드웨어가 빠르게 해준다

이 프로젝트의 기본 인코딩은:

- `h264_nvenc`

즉:

- H.264 코덱
- NVIDIA 하드웨어 인코더 사용

이다.

왜 중요하냐면:

- 실시간 송출에서 인코딩 비용이 큼
- NVENC를 쓰면 CPU 부담을 줄일 수 있음

---

## 17. 입력 포맷 `nv12`와 `bgr24`는 무엇인가

영상이 메모리 안에 raw 형태로 들어올 때, 픽셀 저장 방식이 여러 가지가 있다.

### bgr24

- 파랑, 초록, 빨강 3채널
- 보통 8비트씩
- 사람이 이해하기 쉬운 편
- OpenCV에서 다루기 쉬움

단점:

- 데이터가 큼

### nv12

- YUV 계열 포맷
- luma(Y)와 chroma(UV)를 분리해서 저장
- 영상 시스템에서 흔히 쓰는 포맷
- `bgr24`보다 더 가벼운 편

장점:

- 입력 pipe 데이터 양을 줄이기 좋음

이 프로젝트는 성능 때문에 기본 입력 pipe를 `nv12`로 잡는다.

관련 코드:

- [ffmpeg_rtsp_reader.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/input/ffmpeg_rtsp_reader.cpp)

---

## 18. homography는 무엇인가

homography는 한 평면 영상을 다른 평면 기준으로 맞추는 3x3 변환 행렬이다.

쉽게 말하면:

- 오른쪽 카메라 영상을
- 왼쪽 카메라 좌표계로
- 기울이고, 밀고, 늘려서 맞추는 수학 공식

이다.

이 프로젝트에서는 calibration 단계에서 homography를 구하고,
runtime에서는 그것을 그대로 사용한다.

파일:

- [runtime_homography.json](/c:/Users/Pixellot/Hogak_Stitching/data/runtime_homography.json)

---

## 19. warpPerspective는 무엇인가

`warpPerspective`는 homography를 실제 이미지에 적용하는 연산이다.

즉:

- 수학 공식만 있는 상태에서
- 실제 픽셀 이미지를 변형해서
- "오른쪽 영상이 왼쪽 영상과 맞게 보이게" 만드는 과정

이다.

이 프로젝트에서는 오른쪽 영상을 주로 warp해서 왼쪽 기준에 맞춘다.

---

## 20. feather blending은 무엇인가

영상 두 장을 딱 잘라 붙이면 경계가 티가 난다.

그래서 겹치는 구간에서:

- 왼쪽을 100% 쓰다가
- 점점 섞고
- 오른쪽을 100% 쓰게

만드는 방법을 쓴다.

이게 feather blending이다.

쉽게 말하면:

- 딱 자르는 게 아니라
- 경계에서 부드럽게 섞는 방법

이다.

이 프로젝트의 stitch 결과가 자연스럽게 보이도록 하는 핵심 요소 중 하나다.

---

## 21. pair / sync는 무엇인가

카메라가 두 대라서 가장 중요한 문제 중 하나가:

"지금 어느 왼쪽 프레임과 어느 오른쪽 프레임을 같이 붙일 것인가?"

이다.

이걸 pair selection이라고 볼 수 있다.

### sync

좌우 프레임의 시간이 얼마나 맞는지를 보는 개념이다.

예를 들어:

- 왼쪽은 지금 시점 프레임
- 오른쪽은 100ms 전 프레임

이면 보기 이상해진다.

그래서 timestamp 차이를 보고:

- 서로 잘 맞는지
- 너무 늦지 않은지

를 판단한다.

### service pair mode

현재 baseline은 `service`다.

쉽게 말하면:

- 좌우 후보 프레임 여러 개를 보고
- 가장 괜찮은 짝을 골라서
- stitch에 넘기는 방식

이다.

관련 핵심:

- [stitch_engine.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/engine/stitch_engine.cpp)

---

## 22. stale은 무엇인가

`stale`은 아주 쉽게 말하면:

- "새로운 정보가 거의 없는 프레임"
- "이미 본 것과 사실상 같은 프레임"

에 가깝다.

왜 중요하냐면:

같은 프레임인데도 매번 다시

- decode
- resize
- upload
- warp
- blend

를 하면 낭비이기 때문이다.

그래서 이 프로젝트는 stale 상황에서:

- 이전 계산 결과를 재사용하고
- 불필요한 재연산을 줄이는 최적화

를 넣었다.

---

## 23. probe와 transmit는 무엇인가

이 프로젝트에서 출력은 둘로 나뉜다.

### probe

디버그/운영 확인용 local stream이다.

쉽게 말하면:

- 운영자가 현재 결과를 확인하기 위한 보조 출력

이다.

### transmit

실제로 밖으로 내보내는 본선 출력이다.

즉:

- 서비스용 진짜 송출

이다.

### 왜 둘을 나눴는가

운영자가 보는 화면과 본선 송출을 완전히 같은 선으로 묶으면,
확인 과정이 본선에 영향을 줄 수 있다.

그래서:

- 확인용
- 실제 송출용

을 분리한 것이다.

---

## 24. output cadence는 무엇인가

`output cadence`는 쉽게 말하면:

- 결과 영상을 몇 fps 박자로 내보낼지

를 뜻한다.

이 프로젝트는 현재:

- 25fps
- 30fps

를 선택할 수 있다.

### 중요한 점

입력이 25fps라고 해서 output을 꼭 30으로 할 필요는 없다.

예를 들어:

- 25fps 카메라면 25로 내보내는 게 자연스러울 수 있다
- 30fps 카메라면 30으로 내보내는 게 자연스러울 수 있다

그래서 `camera25` profile 같은 것이 있다.

---

## 25. fresh fps와 transmit fps는 다를 수 있다

이건 실시간 시스템에서 매우 중요하다.

### stitch_actual_fps

실제로 **새로운 stitched frame**이 초당 몇 개 나오는지

### transmit_fps

결과 스트림이 **몇 fps cadence로 송출되는지**

둘은 같을 수도 있고 다를 수도 있다.

예:

- fresh stitched frame은 25fps
- transmit는 30fps cadence

그러면 일부 프레임은 repeat될 수 있다.

즉 사용자 입장에서는 30fps처럼 보일 수 있어도,
실제 새 장면은 25fps일 수 있다.

---

## 26. 이 프로젝트의 현재 주요 성능 최적화

이 프로젝트는 성능 때문에 구조를 많이 바꿨다.

대표적으로:

### 26-1. Python에서 직접 stitch하지 않음

예전처럼 Python 중심이 아니라,
실제 본선 처리는 C++ runtime이 맡는다.

이유:

- Python보다 C++가 실시간 처리에 유리
- GPU/OpenCV/FFmpeg와 맞물리기 좋음

### 26-2. 입력 포맷을 `nv12`로 줄임

`bgr24`보다 더 가벼운 `nv12`를 써서:

- pipe 데이터 양 감소
- CPU read 부담 감소

효과를 노렸다.

### 26-3. `gpu-direct` 출력 경로

예전 구조는 GPU에서 만든 결과를 다시 CPU로 내려서,
외부 ffmpeg로 넘겨 인코딩하는 부담이 컸다.

지금은 가능한 경우:

- GPU 쪽 결과를
- libav/NVENC 기반 writer로
- 더 직접 연결한다

즉 CPU 왕복을 줄였다.

### 26-4. stale 재사용

같은 입력 seq면:

- decode 결과 재사용
- resize 결과 재사용
- GPU upload 재사용
- warp 결과 재사용

해서 낭비를 줄인다.

### 26-5. pair scheduler 정리

`service` pair mode를 중심으로:

- 좌우 시간차
- freshness
- reuse

를 함께 고려해서 더 좋은 pair를 고른다.

---

## 27. 지금 이 프로젝트의 현재 기본 설정

현재 baseline은 [runtime.json](/c:/Users/Pixellot/Hogak_Stitching/config/runtime.json) 기준으로 대략 이렇다.

- `input_runtime = ffmpeg-cuda`
- `input_pipe_format = nv12`
- `rtsp_transport = udp`
- `input_buffer_frames = 8`
- `output_standard = realtime_hq_1080p`
- `output_cadence_fps = 30`
- `probe.runtime = ffmpeg`
- `transmit.runtime = gpu-direct`
- `transmit.target = udp://127.0.0.1:24000?pkt_size=1316`
- `transmit.codec baseline = h264_nvenc`

중요:

repo 안 `runtime.json`의 RTSP 주소는 placeholder다.
실제 현장에서는 `runtime.local.json`로 덮어써야 한다.

---

## 28. 이 프로젝트는 어떤 환경을 전제로 하나

현재 이 프로젝트는 완전히 범용적인 앱이 아니다.

사실상 다음 전제를 가진다.

- Windows
- NVIDIA GPU
- CUDA 사용 가능
- NVENC 사용 가능

즉:

- "아무 컴퓨터에서나 똑같이 돌아간다"

가 아니라,

- "특정 실시간 영상 처리 환경에 맞춰 최적화된 시스템"

이라고 이해하는 게 맞다.

---

## 29. 후임이 처음 보면 좋은 파일 순서

처음부터 큰 파일을 무작정 다 읽으면 힘들다.
이 순서로 보는 걸 권장한다.

### 1단계: 입구 문서

1. [README.md](/c:/Users/Pixellot/Hogak_Stitching/README.md)
2. [config/README.md](/c:/Users/Pixellot/Hogak_Stitching/config/README.md)
3. [native_runtime/README.md](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/README.md)

### 2단계: Python control plane

4. [cli.py](/c:/Users/Pixellot/Hogak_Stitching/stitching/cli.py)
5. [native_runtime_cli.py](/c:/Users/Pixellot/Hogak_Stitching/stitching/native_runtime_cli.py)
6. [native_calibration.py](/c:/Users/Pixellot/Hogak_Stitching/stitching/native_calibration.py)
7. [runtime_launcher.py](/c:/Users/Pixellot/Hogak_Stitching/stitching/runtime_launcher.py)

### 3단계: C++ runtime core

8. [runtime_main.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/app/runtime_main.cpp)
9. [ffmpeg_rtsp_reader.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/input/ffmpeg_rtsp_reader.cpp)
10. [stitch_engine.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/engine/stitch_engine.cpp)
11. [gpu_direct_output_writer.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/output/gpu_direct_output_writer.cpp)

---

## 30. 신입이 자주 헷갈리는 질문

### Q1. 이 프로젝트는 Python 프로젝트인가, C++ 프로젝트인가?

둘 다다.

- 운영/제어는 Python
- 본선 처리 엔진은 C++

### Q2. RTSP는 파일인가?

아니다.
네트워크 live stream이다.

### Q3. CUDA를 쓴다는 건 무슨 뜻인가?

NVIDIA GPU를 계산에 쓰는 뜻이다.

### Q4. `ffmpeg-cuda`는 CUDA랑 다른 건가?

다른 기술이 아니라,
ffmpeg가 CUDA/NVIDIA 가속을 쓰는 입력 모드라고 이해하면 된다.

### Q5. 왜 output이 `probe`와 `transmit`로 나뉘어 있나?

디버그/확인용과 본선 송출을 분리하려고 그렇다.

### Q6. 왜 config를 쓰나?

카메라 주소나 포트 같은 현장값을 코드에서 분리하려고 쓴다.

### Q7. 왜 `runtime.local.json`이 필요한가?

repo에 올리면 안 되는 현장/로컬 전용 값이 있기 때문이다.

---

## 31. 현재 코드 기준으로 정말 중요한 현실

이 프로젝트는 이미 많이 정리됐지만,
여전히 실시간 시스템 특유의 현실이 있다.

예:

- 입력 source cadence가 흔들릴 수 있다
- Wi-Fi/UDP 환경이면 지터가 생길 수 있다
- GPU가 남아 보여도 입력이 못 따라오면 전체 fps는 낮을 수 있다
- output fps와 fresh fps는 다를 수 있다

즉:

성능을 볼 때 단순히 CPU%, GPU%만 보지 않고

- `stitch_actual_fps`
- `transmit_fps`
- `waits`
- `pair_skew_ms`
- `age_ms`

를 같이 봐야 한다.

---

## 32. 마지막 정리: 이 프로젝트를 한 문장으로 다시 말하면

이 프로젝트는

**"두 개의 RTSP 카메라 영상을 실시간으로 받아, calibration에서 구한 homography를 사용해 GPU 가속으로 stitch하고, 그 결과를 H.264/NVENC 기반으로 다시 네트워크 송출하는 Windows + NVIDIA 중심의 실시간 영상 시스템"**

이다.

---

## 33. 후임에게 실제로 해줄 첫 과제

이 문서를 읽은 뒤 바로 해야 할 첫 과제는 이 정도가 좋다.

1. [README.md](/c:/Users/Pixellot/Hogak_Stitching/README.md) 읽기
2. [config/runtime.json](/c:/Users/Pixellot/Hogak_Stitching/config/runtime.json) 구조 이해하기
3. [cli.py](/c:/Users/Pixellot/Hogak_Stitching/stitching/cli.py)에서 entrypoint 보기
4. [native_runtime_cli.py](/c:/Users/Pixellot/Hogak_Stitching/stitching/native_runtime_cli.py)에서 Python이 무엇을 넘기는지 보기
5. [runtime_main.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/app/runtime_main.cpp)에서 C++ entrypoint 보기
6. [stitch_engine.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/engine/stitch_engine.cpp)에서 stitch 흐름 보기

이 6개를 따라가면, 프로젝트의 80% 이상은 잡힌다.

---

## 34. 자주 틀리는 개념 Q&A

여기부터는 신입이 실제로 자주 헷갈리는 질문을 일부러 모아놓은 섹션이다.

### Q1. RTSP는 영상 파일인가요?

아니다.

RTSP는 `.mp4` 같은 저장 파일이 아니라 **실시간으로 흘러오는 네트워크 스트림**이다.

즉:

- 파일 = 이미 저장된 영상
- RTSP = 지금 카메라가 보내는 live 영상

차이가 있다.

그래서 RTSP는 파일처럼 "그냥 읽으면 끝"이 아니라,

- 네트워크 상태
- 지연
- 패킷 손실
- 카메라 리듬

까지 같이 생각해야 한다.

### Q2. RTSP랑 UDP는 같은 말인가요?

아니다.

- RTSP = 스트리밍을 다루는 프로토콜
- UDP = 데이터를 보내는 transport 방식 중 하나

쉽게 말하면:

- RTSP는 "영상 스트림을 주고받는 규칙"
- UDP는 "그 규칙 아래에서 데이터를 실어 나르는 방법"

이다.

RTSP는 내부 transport로 TCP나 UDP를 쓸 수 있다.

### Q3. `rtsp://...` 주소와 `udp://...` 주소는 왜 둘 다 있나요?

역할이 다르다.

- `rtsp://...` = 카메라에서 들어오는 입력
- `udp://...` = 우리가 stitched 결과를 내보내는 출력 target

즉:

- 입력 주소
- 출력 주소

가 따로 있는 것이다.

### Q4. 카메라가 25fps면 output도 무조건 25fps여야 하나요?

무조건은 아니다.

하지만 보통은 **그렇게 맞추는 쪽이 자연스럽다.**

이유:

- 입력이 25fps면 실제 새로운 정보도 초당 25장 정도만 생긴다
- output을 30fps로 고정하면 일부 프레임은 repeat될 수 있다

그래서:

- 25fps 카메라 -> 25fps cadence
- 30fps 카메라 -> 30fps cadence

가 운영상 이해하기 쉽고 자연스럽다.

### Q5. 그럼 `transmit_fps`가 30이면 진짜 30fps인 건가요?

아니다. 이건 아주 중요하다.

`transmit_fps`는 보통 **송출 cadence**를 뜻한다.

즉:

- 밖으로 몇 fps 박자로 보내는지

를 말한다.

하지만 실제로 매 프레임이 전부 새 장면이라는 뜻은 아니다.

진짜 새 frame 속도는 `stitch_actual_fps`에 더 가깝다.

즉:

- `stitch_actual_fps` = fresh stitched frame 속도
- `transmit_fps` = 송출 박자

둘은 같을 수도 있고 다를 수도 있다.

### Q6. GPU 사용률이 낮으면 성능 문제가 없는 건가요?

아니다.

이 프로젝트에서는 GPU가 한가해 보여도 전체 fps가 낮을 수 있다.

왜냐하면 병목이 항상 GPU 계산에만 있는 게 아니기 때문이다.

예:

- RTSP 입력 cadence 흔들림
- pair selection 대기
- input read/queue
- network jitter

이런 이유로 GPU가 "일감을 제때 못 받으면" GPU 사용률은 낮게 보일 수 있다.

즉:

- GPU 사용률 낮음 = 무조건 괜찮음 아님

이다.

### Q7. CPU 사용률이 30%면 여유가 많은 거 아닌가요?

평균 CPU 사용률만 보고 판단하면 위험하다.

실시간 시스템은:

- 평균 사용률
- 순간 지연
- 특정 단계의 대기 시간

이 다 다르다.

예를 들어 CPU 전체는 30%여도,
특정 입력 단계가 가끔 늦으면 전체 fps는 떨어질 수 있다.

### Q8. `ffmpeg-cuda`면 입력부터 출력까지 다 GPU에서만 도는 건가요?

아니다.

`ffmpeg-cuda`는 입력 쪽에서 ffmpeg가 CUDA/NVIDIA 가속을 활용하는 경로를 뜻한다.

하지만 프로젝트 전체가 완전 GPU-only라는 뜻은 아니다.

현재도 일부 구간은 CPU가 관여한다.

예:

- RTSP -> raw pipe -> CPU read
- pair selection
- 일부 fallback 변환

즉:

- `ffmpeg-cuda` = GPU 활용 입력 경로
- `all GPU pipeline` = 더 넓은 개념

으로 구분해야 한다.

### Q9. `gpu-direct`면 무조건 CPU를 안 쓰는 건가요?

아니다.

`gpu-direct`는 주로 **출력 쪽에서 CPU 왕복을 줄인 경로**라고 이해하면 된다.

즉:

- stitch 결과를
- 가능하면 GPU 쪽에서 더 직접 encode/send 하려는 구조

이지,

- 프로젝트 전체에서 CPU가 완전히 사라진다는 뜻은 아니다

### Q10. OpenCV를 쓰면 stitching을 다 자동으로 해주나요?

아니다.

OpenCV는 도구를 제공할 뿐이고,
이 프로젝트는 고수준 `cv::Stitcher`를 메인으로 쓰지 않는다.

현재는:

- calibration으로 homography를 얻고
- runtime에서 warpPerspective
- feather blend

를 직접 조합한 구조다.

즉 OpenCV는 **자동 완성기**라기보다 **부품 상자**에 가깝다.

### Q11. homography는 카메라가 바뀌어도 계속 재사용하면 되나요?

보통은 안 된다.

다음 중 하나라도 바뀌면 재보정이 필요할 수 있다.

- 카메라 위치
- 각도
- 줌
- 해상도
- 렌즈 상태

즉 homography는 "영원히 맞는 값"이 아니라,
**현재 카메라 배치에 맞는 보정값**이다.

### Q12. `runtime.json`만 바꾸면 되는데 왜 `runtime.local.json`이 따로 있나요?

`runtime.json`은 저장소 기본 설정이고,
`runtime.local.json`은 **내 PC/현장 전용 값**을 따로 두기 위한 장치다.

이걸 나누는 이유는:

- 실제 카메라 주소가 git에 올라가면 안 될 수 있고
- 사람마다 환경이 다를 수 있기 때문이다

쉽게 말하면:

- `runtime.json` = 공용 기본 설정
- `runtime.local.json` = 내 컴퓨터 전용 덮어쓰기

### Q13. `.exe`가 있는데 왜 Python도 필요한가요?

`.exe`는 C++ runtime 엔진이고,
Python은 그 엔진을 쉽게 다루는 control plane이다.

Python이 하는 일:

- 설정 로드
- calibration UX
- 실행 인자 구성
- monitor 출력
- 운영 흐름 정리

즉 `.exe`만 있다고 운영이 편해지는 건 아니고,
Python이 그 위에 운영 계층을 만들어주는 것이다.

### Q14. `probe`가 있으면 `transmit`는 없어도 되나요?

아니다. 역할이 다르다.

- `probe` = 로컬 확인용
- `transmit` = 실제 본선 송출

`probe`만 있어도 눈으로 보기엔 동작하는 것처럼 보일 수 있지만,
실제 서비스 송출이 잘 되는지는 `transmit`를 봐야 한다.

### Q15. viewer가 보이면 송출도 잘 되는 건가요?

항상 그렇지는 않다.

viewer는 보통 `probe`를 본다.

즉:

- viewer가 잘 보인다
- 하지만 transmit target이 문제일 수 있다

는 상황이 가능하다.

그래서 운영할 때는:

- viewer 상태
- transmit_fps
- transmit_active
- dropped / written

를 같이 봐야 한다.

### Q16. `waits`가 많으면 무조건 버그인가요?

꼭 그렇진 않다.

실시간 시스템에서는 "기다림" 자체가 어느 정도 자연스럽다.

예를 들어 카메라가 30fps면 대략 33ms마다 새 frame이 온다.
그 사이엔 당연히 다음 frame을 기다릴 수 있다.

중요한 건:

- 얼마나 자주 기다리는지
- 기다림 때문에 실제 fps가 떨어지는지
- 어떤 종류의 wait가 많은지

를 보는 것이다.

즉 `wait_next_frame`이 있다는 사실 자체보다,
그것이 실제 성능 저하로 이어지는지가 중요하다.

### Q17. `pair_skew_ms`는 낮을수록 무조건 좋은가요?

대체로는 그렇다.

좌우 카메라 시간차가 작을수록 같은 장면을 붙일 가능성이 높기 때문이다.

하지만 이것도 단독으로 보면 안 된다.

예:

- skew는 작은데 input age가 크다
- skew는 좋은데 fresh fps가 낮다

이런 경우도 있다.

즉 `pair_skew_ms`는 중요한 지표지만,
항상 다른 지표와 같이 봐야 한다.

### Q18. `config`를 바꾸면 코드를 안 건드려도 되나요?

운영값 변경은 보통 그렇다.

예:

- 카메라 주소
- cadence
- target
- profile

이런 건 config로 바꾸는 게 맞다.

하지만 알고리즘이나 runtime 동작 자체를 바꾸는 건 코드 수정이 필요하다.

즉:

- 운영값 = config
- 동작 로직 = code

로 구분하면 된다.

### Q19. "동작한다"와 "서비스 가능하다"는 같은 말인가요?

아니다.

실행만 된다고 서비스 가능한 건 아니다.

서비스 가능하려면 보통:

- 오래 켜도 안 죽고
- fps가 기준 안에 있고
- 송출이 안정적이고
- 장애 시 원인 파악이 가능해야 한다

즉:

- 동작한다 = 켜진다
- 서비스 가능하다 = 운영 가능한 수준으로 안정적이다

### Q20. 이 프로젝트를 처음 만진 신입이 가장 조심해야 할 것은 무엇인가요?

세 가지다.

1. **용어를 대충 섞어 쓰지 말 것**  
   RTSP, UDP, codec, fps, cadence, fresh fps는 다 다른 말이다.

2. **평균 리소스만 보고 성능을 판단하지 말 것**  
   CPU%, GPU%만 보면 실시간 병목을 놓치기 쉽다.

3. **config와 code를 구분할 것**  
   운영값은 config에서, 동작 로직은 code에서 바뀐다.

---

## 35. 이 문서를 읽고도 아직 헷갈릴 때 추천 질문 순서

신입이 혼자 공부하다가 막히면 질문도 순서가 중요하다.

이 순서로 질문하면 훨씬 빨리 풀린다.

1. 이건 입력 문제인가, 출력 문제인가?
2. 이건 설정 문제인가, 코드 문제인가?
3. 이건 fresh fps 문제인가, transmit cadence 문제인가?
4. 이건 Python control plane 문제인가, C++ runtime 문제인가?
5. 이건 source/네트워크 문제인가, 엔진 계산 문제인가?

이 다섯 줄만 머리에 넣어도, 문제를 훨씬 덜 헤맨다.

---

## 36. 이 프로젝트가 해결하려는 진짜 기술 문제들

앞에서 "이 프로젝트는 어렵다"라고 했는데, 여기서는 그걸 더 실무적으로 풀어본다.

즉:

- 왜 이런 문제가 생기는지
- 업계에서는 보통 어떻게 푸는지
- 이 프로젝트는 실제로 어떤 선택을 했는지
- 다른 선택지도 무엇이 있는지

를 하나씩 적는다.

이 섹션은 신입이 "기술 선택의 이유"를 이해하는 데 핵심이다.

---

## 37. 문제 1: 카메라 두 대의 시간이 정확히 똑같지 않다

### 문제 설명

왼쪽 카메라와 오른쪽 카메라는 동시에 같은 장면을 찍고 있어도,
실제로는 프레임이 완전히 같은 시각에 도착하지 않는다.

왜냐하면:

- 카메라 내부 처리 시간 차이
- 네트워크 전송 시간 차이
- 디코더 처리 차이

가 있기 때문이다.

그러면 이런 상황이 생긴다.

- 왼쪽은 이미 다음 장면인데
- 오른쪽은 아직 이전 장면

이걸 그냥 붙이면 stitch 결과가 어색해진다.

### 일반적으로 어떻게 하나

업계에서는 보통 세 가지 중 하나를 쓴다.

#### 1. 최신 프레임끼리 그냥 붙이기

가장 단순하다.

장점:

- 구현이 쉽다
- 지연이 짧다

단점:

- 좌우 시간차가 커질 수 있다
- 실시간은 빠르지만 품질이 흔들릴 수 있다

#### 2. timestamp 기반으로 가장 가까운 pair를 고르기

좌우 프레임에 timestamp가 있다고 가정하고,
시간 차이가 가장 작은 pair를 고른다.

장점:

- 품질이 더 안정적
- 좌우 mismatch를 줄일 수 있다

단점:

- 후보를 비교해야 해서 구현이 조금 복잡하다
- 기다리는 시간이 생길 수 있다

#### 3. 하드웨어 genlock / 외부 동기화

방송 장비처럼 매우 정확한 환경에서는 카메라를 물리적으로 동기화하기도 한다.

장점:

- 가장 정확하다

단점:

- 장비 비용이 커짐
- 일반 IP 카메라 환경에서는 어렵다

### 이 프로젝트는 어떻게 했나

이 프로젝트는 **software pair scheduler**를 쓴다.

핵심은:

- 좌우 최근 프레임들을 버퍼에 잠깐 쌓아두고
- timestamp 기준으로
- 가장 적당한 pair를 고르는 방식

현재 baseline pair mode는 `service`다.

관련 핵심:

- [stitch_engine.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/engine/stitch_engine.cpp)

### 왜 이 방식을 택했나

이 프로젝트는:

- 일반 RTSP 카메라
- 네트워크 기반
- 하드웨어 동기화 장비 없음

환경이기 때문이다.

즉 방송용 genlock보다 훨씬 현실적인 구조여서,
software pair selection이 맞는 선택이었다.

### 장단점

장점:

- 일반 IP 카메라 환경에 맞음
- 비용이 적음
- 운영 설정으로 조정 가능

단점:

- 완전한 물리 동기화만큼 정확하진 않음
- source/network 흔들림 영향을 받음

---

## 38. 문제 2: 네트워크로 영상이 오기 때문에 지연이 생긴다

### 문제 설명

카메라 영상은 파일처럼 디스크에서 읽는 게 아니라,
네트워크를 통해 들어온다.

그래서:

- 패킷이 늦게 오거나
- 순서가 흔들리거나
- 순간적으로 끊기거나
- Wi-Fi에서 버스트 지연이 생기거나

할 수 있다.

### 일반적으로 어떻게 하나

#### 1. TCP 사용

장점:

- 손실 복구가 나음
- 안정적일 수 있음

단점:

- 지연 증가 가능
- 실시간성 저하 가능

#### 2. UDP 사용

장점:

- 지연이 낮음
- 실시간 스트리밍에 유리

단점:

- 손실 복구를 transport가 해주지 않음
- jitter에 취약

#### 3. 큰 jitter buffer 사용

일부 시스템은 버퍼를 크게 두고 안정성을 높인다.

장점:

- 흐름이 더 안정적일 수 있음

단점:

- latency가 커짐

### 이 프로젝트는 어떻게 했나

기본 baseline은:

- 입력 RTSP transport = `udp`
- input buffer = 최근 프레임 몇 장 유지

로 갔다.

관련 설정:

- [runtime.json](/c:/Users/Pixellot/Hogak_Stitching/config/runtime.json)
- [ffmpeg_rtsp_reader.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/input/ffmpeg_rtsp_reader.cpp)

### 왜 이 방식을 택했나

이 프로젝트의 우선순위는 보통:

- **낮은 지연**
- **실시간성**

이다.

그래서 완전 안정성보다, 낮은 latency를 더 우선하는 쪽으로 갔다.

### 장단점

장점:

- 실시간성이 좋다
- 라이브 보기 체감이 낫다

단점:

- Wi-Fi나 불안정한 네트워크에서 흔들릴 수 있다
- source jitter가 곧바로 stitch 품질에 영향 준다

---

## 39. 문제 3: 영상 한 장 크기가 크다

### 문제 설명

프레임 한 장은 생각보다 크다.

예를 들어 1920x1080 BGR 프레임이면,
매 프레임마다 다뤄야 할 데이터 양이 상당하다.

이걸 초당 수십 장씩:

- 읽고
- 복사하고
- 변환하고
- 보내면

엄청 무거워진다.

### 일반적으로 어떻게 하나

#### 1. RGB/BGR 계열 raw로 그대로 처리

장점:

- 이해하기 쉽다
- 라이브러리와 호환성이 좋다

단점:

- 데이터가 큼

#### 2. YUV 계열 포맷 사용

예:

- `nv12`
- `yuv420p`

장점:

- 더 가볍다
- 영상 처리 파이프라인에서 흔하다

단점:

- 사람이 바로 이해하기 어렵다
- 필요 시 색변환 비용이 생긴다

### 이 프로젝트는 어떻게 했나

입력 pipe baseline을 `bgr24`보다 **`nv12`** 쪽으로 가져갔다.

관련 코드:

- [ffmpeg_rtsp_reader.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/input/ffmpeg_rtsp_reader.cpp)

### 왜 이 방식을 택했나

입력 경계에서 가장 큰 문제 중 하나가:

- rawvideo 데이터량

이었기 때문이다.

`nv12`는 `bgr24`보다 가벼워서:

- pipe 부담 감소
- read 부담 감소
- queue 부담 감소

효과가 있었다.

### 장단점

장점:

- 데이터양 감소
- 입력 경계 비용 감소

단점:

- stitch용 BGR로 바꾸는 과정이 필요할 수 있음
- GPU 변환 지원 여부에 따라 fallback이 생길 수 있음

---

## 40. 문제 4: 매 프레임마다 연산량이 많다

### 문제 설명

실시간 스티칭은 매 프레임마다 할 일이 많다.

예:

- decode
- resize
- pair 선택
- warpPerspective
- blend
- metric 계산

이걸 매번 다 새로 하면 무겁다.

### 일반적으로 어떻게 하나

#### 1. CPU만으로 처리

장점:

- 구현이 단순할 수 있음
- 디버깅이 쉬움

단점:

- 속도가 부족해질 수 있음

#### 2. GPU 사용

장점:

- 병렬 연산에 강함
- warp/blend 같이 픽셀 단위 연산에 유리

단점:

- 구현 복잡도 증가
- GPU 메모리/전송 관리 필요

#### 3. 재사용 / skip

같은 입력이면 매번 full recompute를 하지 않는다.

장점:

- 낭비 감소

단점:

- 상태 관리가 복잡해짐

### 이 프로젝트는 어떻게 했나

두 가지를 같이 했다.

#### GPU 가속

- OpenCV CUDA 기반 warp/blend
- [stitch_engine.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/engine/stitch_engine.cpp)

#### stale reuse

같은 seq 입력이면:

- decode 결과 재사용
- resize 재사용
- upload 재사용
- warp 재사용

### 왜 이 방식을 택했나

실시간 스티칭에서는:

- "모든 걸 CPU로"는 비효율적이고
- "매 프레임 모든 단계 full recompute"도 낭비

이기 때문이다.

### 장단점

장점:

- 실제 처리량 개선
- GPU 자원 활용
- 반복 입력 낭비 감소

단점:

- 코드 복잡도 증가
- stale/reuse 조건을 잘못 잡으면 디버깅이 어려움

---

## 41. 문제 5: 결과를 다시 압축해서 보내야 한다

### 문제 설명

stitched 결과는 raw 상태로 보내기엔 너무 크다.

그래서 결과를 다시:

- H.264
- H.265

같은 형식으로 인코딩해서 보내야 한다.

### 일반적으로 어떻게 하나

#### 1. CPU 인코딩

예:

- `libx264`

장점:

- 호환성이 좋음

단점:

- CPU 부담 큼
- 실시간에서 무거울 수 있음

#### 2. 하드웨어 인코딩

예:

- `h264_nvenc`
- `hevc_nvenc`

장점:

- 빠름
- CPU 부담 감소

단점:

- 특정 GPU 의존성

### 이 프로젝트는 어떻게 했나

기본 baseline은:

- `h264_nvenc`

즉 NVIDIA NVENC 하드웨어 인코더를 쓴다.

관련 코드:

- [gpu_direct_output_writer.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/output/gpu_direct_output_writer.cpp)
- [ffmpeg_output_writer.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/output/ffmpeg_output_writer.cpp)

### 왜 이 방식을 택했나

실시간 송출에서 인코딩은 굉장히 무거운 작업이기 때문이다.
CPU 인코딩보다 NVENC가 훨씬 실용적이었다.

### 장단점

장점:

- 실시간에 유리
- CPU 부담 감소

단점:

- NVIDIA 환경 의존
- 카드/드라이버/세대 차이에 영향 받음

---

## 42. 문제 6: 끊기면 안 된다

### 문제 설명

실시간 서비스에서는 순간적으로라도:

- 멈춤
- 버벅임
- 출력 중단

이 보이면 사용자가 바로 느낀다.

### 일반적으로 어떻게 하나

#### 1. 무조건 fresh frame만 출력

장점:

- 가장 정직함

단점:

- 입력이 잠깐 늦어도 화면이 바로 멈춰 보일 수 있음

#### 2. output cadence 유지 + repeat 허용

장점:

- viewer는 일정한 박자로 받음
- 체감이 더 안정적

단점:

- fresh fps와 output fps가 다를 수 있음

### 이 프로젝트는 어떻게 했나

기본적으로:

- output cadence를 25/30fps로 맞추고
- 입력이 부족한 순간에는 repeat 가능성이 있는 구조

로 운영한다.

즉:

- 사용자는 일정한 송출 박자를 받게 하고
- 내부에서는 fresh fps를 따로 모니터링한다

### 왜 이 방식을 택했나

실무에서는 보통:

- "무조건 fresh만"보다
- "일정한 송출 cadence"가 더 중요할 때가 많기 때문이다

### 장단점

장점:

- 플레이어/수신기 입장에서 안정적
- 운영 체감이 더 좋음

단점:

- 실제 fresh fps를 별도 지표로 봐야 함

---

## 43. 기술 1: 네트워크 스트리밍

이 프로젝트에서 네트워크 스트리밍은 두 방향이다.

### 입력 스트리밍

- 카메라 -> runtime
- 프로토콜: RTSP

### 출력 스트리밍

- runtime -> 외부 target
- 현재 기본 target: UDP

### 대체 기술

- RTMP
- SRT
- file output
- tee muxer

### 이 프로젝트의 선택

입력:

- RTSP

출력:

- UDP baseline

이유:

- live camera와 낮은 지연이 중요했기 때문

---

## 44. 기술 2: 영상 디코딩

입력 영상은 보통 이미 압축되어 들어온다.

예:

- H.264
- H.265

이걸 처리하려면 먼저 디코딩해야 한다.

### 일반적인 방법

#### 1. OpenCV VideoCapture

장점:

- 코드가 단순함

단점:

- 세밀한 제어가 어렵다
- 성능/안정성 한계가 있을 수 있다

#### 2. FFmpeg 직접 사용

장점:

- 제어력이 좋다
- 실무에서 많이 씀

단점:

- 구현이 복잡하다

### 이 프로젝트의 선택

입력은 FFmpeg subprocess reader를 사용한다.

즉:

- ffmpeg가 RTSP를 받고
- rawvideo를 pipe로 내보내고
- runtime이 읽는다

이유:

- OpenCV 단순 경로보다 더 직접 제어하기 좋았기 때문이다

---

## 45. 기술 3: 프레임 동기화

프레임 동기화는 좌우에서 "같이 붙일 프레임"을 고르는 문제다.

### 일반적인 방법

- latest pair
- oldest pair
- timestamp closest pair
- hardware sync

### 이 프로젝트의 선택

- `service` pair scheduler

특징:

- 최신성
- 시간차
- reuse

를 함께 고려한다.

장점:

- 일반 RTSP 카메라 환경에 현실적

단점:

- 코드가 단순하진 않다

---

## 46. 기술 4: 기하학적 변환

카메라가 서로 다른 위치에 있으니 영상을 그냥 이어붙일 수는 없다.

그래서 한쪽 영상을 다른 기준으로 변형해야 한다.

### 일반적인 방법

- translation only
- affine transform
- homography
- camera model / 3D reprojection

### 이 프로젝트의 선택

- homography 기반 warpPerspective

이유:

- 현재 장면과 카메라 배치에서 가장 현실적이고 구현 가능한 수준

장점:

- 평면 장면에서 잘 맞는다
- 구현 복잡도가 3D 재구성보다 낮다

단점:

- 장면이 완전히 평면이 아니면 한계가 있다

---

## 47. 기술 5: 영상 합성

정렬만 해도 경계가 티날 수 있어서,
겹치는 구간을 어떻게 섞을지가 중요하다.

### 일반적인 방법

#### 1. hard cut

장점:

- 빠르다

단점:

- 경계가 티난다

#### 2. feather blending

장점:

- 단순하고 빠르다
- 경계를 부드럽게 만든다

단점:

- 고급 seam finding보다 정교하진 않다

#### 3. multi-band blending

장점:

- 품질이 좋을 수 있다

단점:

- 무겁다

### 이 프로젝트의 선택

- feather blending

이유:

- 실시간성과 구현 난이도 균형이 좋기 때문

---

## 48. 기술 6: GPU 가속

영상 처리에서 GPU를 쓰는 이유는 같은 계산을 픽셀 단위로 많이 반복하기 때문이다.

### 일반적인 방법

#### 1. CPU only

장점:

- 단순

단점:

- 느릴 수 있음

#### 2. OpenCV CUDA

장점:

- 기존 OpenCV 흐름과 연결하기 좋음

단점:

- 모든 변환을 지원하진 않음

#### 3. custom CUDA kernel / NPP

장점:

- 더 세밀한 최적화 가능

단점:

- 구현 난이도 큼

### 이 프로젝트의 선택

- OpenCV CUDA 기반 warp/blend

이유:

- 기존 OpenCV 코드와 연결하기 좋고
- 실시간에 필요한 주요 계산을 GPU로 보낼 수 있었기 때문

### 한계

입력 변환 일부는 현재 OpenCV CUDA 지원 한계 때문에 fallback이 남아 있다.

즉:

- GPU를 많이 쓰지만
- 완전 GPU-only는 아님

---

## 49. 기술 7: 실시간 인코딩

실시간 인코딩은 stitched 결과를 즉시 네트워크로 내보내는 단계다.

### 일반적인 방법

#### 1. 외부 ffmpeg 프로세스 사용

장점:

- 구현이 쉬움

단점:

- 프로세스 경계 비용
- CPU 왕복 비용

#### 2. libav in-process writer

장점:

- 더 세밀한 제어
- 프로세스 경계 감소

단점:

- 구현 복잡도 상승

### 이 프로젝트의 선택

- `gpu-direct`는 libav/NVENC 기반 in-process 경로
- fallback/보조로 ffmpeg writer도 존재

이유:

- output 쪽 CPU 왕복을 줄이고
- 본선 송출을 더 효율적으로 만들기 위해

---

## 50. 기술 8: 운영 모니터링

실시간 시스템은 "돌아간다"보다 "어디서 막히는지 보인다"가 중요하다.

### 일반적인 방법

- 단순 로그만 남김
- FPS만 표시
- 대시보드 구축
- 이벤트/메트릭 분리

### 이 프로젝트의 선택

Python monitor/dashboard + JSON Lines metrics

즉:

- C++가 메트릭을 내보내고
- Python이 읽어서 운영자가 보기 좋게 만든다

주로 보는 값:

- `stitch_actual_fps`
- `transmit_fps`
- `pair_skew_ms`
- `left_age_ms`, `right_age_ms`
- `waits`
- `gpu_errors`

### 왜 중요한가

실시간 시스템은 단순히 "느리다"가 아니라

- 입력이 늦은 건지
- pair가 문제인지
- output이 문제인지

를 빨리 나눠야 한다.

이 프로젝트는 그걸 위해 메트릭을 꽤 많이 갖고 있다.

---

## 51. 현재 프로젝트의 기술 선택을 한 문장씩 요약하면

- **네트워크 입력**: RTSP를 쓴다. 일반 IP 카메라 환경에 맞기 때문이다.
- **입력 처리**: OpenCV 단순 capture보다 FFmpeg reader가 더 제어하기 좋았다.
- **동기화**: 하드웨어 sync 대신 software pair scheduler를 쓴다.
- **정렬**: 3D reconstruction 대신 homography warp를 쓴다.
- **합성**: 고급 multi-band보다 실시간성에 맞는 feather blending을 택했다.
- **가속**: CPU-only 대신 OpenCV CUDA를 적극 쓴다.
- **인코딩**: CPU 인코딩보다 `h264_nvenc`를 baseline으로 쓴다.
- **출력**: 외부 ffmpeg 프로세스만 쓰지 않고 `gpu-direct`를 도입했다.
- **운영**: 단순 로그 대신 metrics/dashboard를 같이 둔다.

---

## 52. 후임이 기술 선택을 평가할 때 가져야 하는 태도

이건 신입에게 꼭 말해주고 싶은 부분이다.

프로젝트를 볼 때:

- "왜 이렇게 복잡하지?"
- "왜 그냥 OpenCV로 다 안 하지?"
- "왜 Python만 쓰지 않지?"

같은 생각이 들 수 있다.

그런데 실시간 영상 시스템은 보통

- 단순한 코드
- 높은 성능
- 낮은 지연
- 쉬운 유지보수

를 동시에 다 얻기 어렵다.

즉 항상 trade-off가 있다.

이 프로젝트의 현재 선택은 대체로:

- 실시간성
- 운영 가능성
- 현재 장비 조건

을 우선한 결과라고 보면 된다.

후임은 코드를 볼 때

"이게 이상하다"를 바로 말하기보다,
"이 문제를 해결하려고 어떤 trade-off를 한 걸까?"를 먼저 생각하는 게 좋다.

---

## 53. 읽기 순서별 실습 과제

여기부터는 "문서를 읽는 것"만으로 끝나지 않게, 단계별로 직접 해보는 과제를 적는다.

목표는:

- 프로젝트를 겁먹지 않고 만져보기
- 코드와 실제 동작을 연결하기
- 용어를 머리로만 아는 게 아니라 손으로 확인하기

이다.

과제는 쉬운 것부터 어려운 것 순서로 배치한다.

---

## 54. 1단계: 전체 그림 잡기

### 읽을 것

1. [README.md](/c:/Users/Pixellot/Hogak_Stitching/README.md)
2. [config/README.md](/c:/Users/Pixellot/Hogak_Stitching/config/README.md)
3. [native_runtime/README.md](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/README.md)

### 실습 과제

#### 과제 1

아래 질문에 답을 문장으로 적어본다.

- 이 프로젝트의 입력은 무엇인가?
- 출력은 무엇인가?
- Python과 C++는 각각 무슨 역할인가?
- `probe`와 `transmit`는 어떻게 다른가?

#### 과제 2

[runtime.json](/c:/Users/Pixellot/Hogak_Stitching/config/runtime.json)을 열어서 아래 키를 직접 찾아본다.

- `left_rtsp`
- `right_rtsp`
- `input_runtime`
- `input_pipe_format`
- `output_cadence_fps`
- `probe`
- `transmit`

그리고 각 항목이 "입력", "출력", "운영값" 중 무엇인지 스스로 분류해본다.

### 이 단계의 목표

이 단계가 끝나면 최소한:

- 이 프로젝트가 뭘 하는지
- 설정 파일이 어디 있는지
- 어디가 입력이고 어디가 출력인지

를 말할 수 있어야 한다.

---

## 55. 2단계: Python control plane 이해하기

### 읽을 것

1. [cli.py](/c:/Users/Pixellot/Hogak_Stitching/stitching/cli.py)
2. [native_runtime_cli.py](/c:/Users/Pixellot/Hogak_Stitching/stitching/native_runtime_cli.py)
3. [runtime_launcher.py](/c:/Users/Pixellot/Hogak_Stitching/stitching/runtime_launcher.py)
4. [runtime_site_config.py](/c:/Users/Pixellot/Hogak_Stitching/stitching/runtime_site_config.py)

### 실습 과제

#### 과제 1

[cli.py](/c:/Users/Pixellot/Hogak_Stitching/stitching/cli.py)를 보고,

- `native-calibrate`
- `native-runtime`

가 어디로 연결되는지 함수 이름까지 적어본다.

#### 과제 2

[runtime_site_config.py](/c:/Users/Pixellot/Hogak_Stitching/stitching/runtime_site_config.py)를 보고,
설정 적용 순서를 직접 적어본다.

정답 예시는 이런 형태여야 한다.

1. `runtime.json`
2. `runtime.local.json`
3. `profile override`

#### 과제 3

[native_runtime_cli.py](/c:/Users/Pixellot/Hogak_Stitching/stitching/native_runtime_cli.py)에서 아래를 찾아본다.

- output standard 선택 UI
- output cadence 25/30 선택
- `probe_output_*`
- `transmit_output_*`
- monitor 출력

그리고 "Python이 최종적으로 어떤 값들을 runtime에 넘겨주는지"를 5개 이상 적어본다.

### 이 단계의 목표

이 단계가 끝나면:

- Python은 엔진이 아니라 orchestration이라는 점
- config를 읽고 runtime spec을 만든다는 점
- 사용자가 보는 UI/monitor는 Python 쪽이라는 점

을 이해해야 한다.

---

## 56. 3단계: calibration 흐름 이해하기

### 읽을 것

1. [native_calibration.py](/c:/Users/Pixellot/Hogak_Stitching/stitching/native_calibration.py)
2. [02_calibration_and_matching_strategy.md](/c:/Users/Pixellot/Hogak_Stitching/reports/02_calibration_and_matching_strategy.md)

### 실습 과제

#### 과제 1

calibration이 끝난 뒤 생성되는 결과 파일이 무엇인지 적는다.

- 파일명
- 저장 위치
- 역할

#### 과제 2

문서와 코드를 보고 아래를 구분해서 적는다.

- auto calibration
- assisted calibration
- manual point의 역할

#### 과제 3

왜 runtime이 calibration을 매번 다시 계산하지 않고,
파일로 저장된 homography를 읽는 구조인지 이유를 2가지 이상 적는다.

### 이 단계의 목표

이 단계가 끝나면:

- calibration은 runtime 이전 준비 단계라는 것
- homography가 왜 중요한지
- 왜 data 파일로 저장하는지

를 이해해야 한다.

---

## 57. 4단계: C++ runtime 입구 이해하기

### 읽을 것

1. [runtime_main.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/app/runtime_main.cpp)
2. [engine_config.h](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/include/engine/engine_config.h)

### 실습 과제

#### 과제 1

`runtime_main.cpp`에서 아래 인자가 어디로 들어가는지 찾아본다.

- `--probe-output-*`
- `--transmit-output-*`
- `--sync-pair-mode`
- `--input-buffer-frames`

#### 과제 2

이 프로젝트에서 "Python -> C++"로 넘어갈 때 실제로 어떤 정보가 넘어가는지 10개 이상 적어본다.

예:

- left RTSP
- right RTSP
- output runtime
- target

같은 식으로.

### 이 단계의 목표

이 단계가 끝나면:

- C++ runtime은 Python이 준 설정으로 움직인다는 것
- entrypoint가 어디인지
- 설정이 native config 구조에 어떻게 들어가는지

를 이해해야 한다.

---

## 58. 5단계: 입력 파이프라인 이해하기

### 읽을 것

1. [ffmpeg_rtsp_reader.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/input/ffmpeg_rtsp_reader.cpp)
2. [ffmpeg_rtsp_reader.h](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/input/ffmpeg_rtsp_reader.h)

### 실습 과제

#### 과제 1

입력 reader가 실제로 하는 일을 순서대로 적어본다.

힌트:

- ffmpeg 실행
- rawvideo 읽기
- frame buffer 저장
- snapshot/metrics 갱신

#### 과제 2

`nv12`와 `bgr24`가 코드에서 어떻게 구분되는지 찾아본다.

아래를 적는다.

- frame rows 계산
- frame bytes 계산
- frame type 계산

#### 과제 3

reader가 왜 단순히 최신 프레임 하나만 들고 있지 않고,
buffer를 유지하는지 이유를 적어본다.

### 이 단계의 목표

이 단계가 끝나면:

- RTSP 입력이 어떻게 raw frame으로 바뀌는지
- 왜 input buffer가 필요한지
- 왜 `nv12`가 baseline인지

를 설명할 수 있어야 한다.

---

## 59. 6단계: pair / sync 이해하기

### 읽을 것

1. [stitch_engine.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/engine/stitch_engine.cpp)
2. [03_current_status_and_roadmap.md](/c:/Users/Pixellot/Hogak_Stitching/reports/03_current_status_and_roadmap.md)

### 실습 과제

#### 과제 1

코드에서 아래 개념을 찾아서 한 문장으로 설명해본다.

- `service pair`
- `pair_skew_ms`
- `wait_next_frame`
- `wait_paired_fresh`

#### 과제 2

왜 "가장 최신 left"와 "가장 최신 right"를 무조건 붙이지 않는지 이유를 적는다.

#### 과제 3

실제 runtime 로그를 보고,

- input fps
- pair skew
- waits

가 무엇을 의미하는지 설명하는 연습을 한다.

### 이 단계의 목표

이 단계가 끝나면:

- 이 프로젝트의 가장 중요한 실시간 문제 중 하나가 pair/sync라는 것
- stitch 전에 이미 많은 판단이 들어간다는 것

을 이해해야 한다.

---

## 60. 7단계: stitch 알고리즘 이해하기

### 읽을 것

1. [stitch_engine.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/engine/stitch_engine.cpp)
2. [stitch_engine.h](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/include/engine/stitch_engine.h)

### 실습 과제

#### 과제 1

stitch가 실제로 어떤 단계로 이루어지는지 순서대로 적어본다.

예시 형식:

1. input decode
2. resize
3. warp
4. overlap 계산
5. blend

#### 과제 2

코드에서 다음을 찾아 역할을 적는다.

- `load_homography_from_file`
- `prepare_warp_plan`
- `build_seam_blend_weights`

#### 과제 3

왜 이 프로젝트가 OpenCV `Stitcher` 대신 직접 warp/blend 구조를 택했을지 스스로 이유를 적어본다.

### 이 단계의 목표

이 단계가 끝나면:

- 이 프로젝트의 stitch는 "자동 black box"가 아니라
- 우리가 직접 조립한 파이프라인이라는 점

을 이해해야 한다.

---

## 61. 8단계: GPU 가속 이해하기

### 읽을 것

1. [stitch_engine.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/engine/stitch_engine.cpp)
2. [gpu_direct_output_writer.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/output/gpu_direct_output_writer.cpp)

### 실습 과제

#### 과제 1

코드에서 GPU가 실제로 쓰이는 구간을 찾아본다.

예:

- input-side 시도
- warp
- blend
- output encode

#### 과제 2

CPU fallback이 남아 있는 구간이 무엇인지 적는다.

#### 과제 3

왜 "GPU 사용률이 낮다"와 "병목이 없다"가 같은 말이 아닌지,
이 프로젝트 기준으로 설명해본다.

### 이 단계의 목표

이 단계가 끝나면:

- CUDA를 쓴다고 해서 모든 게 GPU-only는 아니라는 점
- 어디는 GPU고 어디는 CPU인지

를 구분할 수 있어야 한다.

---

## 62. 9단계: 출력 파이프라인 이해하기

### 읽을 것

1. [gpu_direct_output_writer.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/output/gpu_direct_output_writer.cpp)
2. [ffmpeg_output_writer.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/output/ffmpeg_output_writer.cpp)

### 실습 과제

#### 과제 1

왜 출력 경로가 두 개 이상 있는지 적는다.

- `ffmpeg output writer`
- `gpu-direct output writer`

#### 과제 2

`gpu-direct`가 해결하려는 문제를 한 문장으로 적는다.

#### 과제 3

`probe`와 `transmit`가 각각 어떤 상황에서 쓰이는지 예를 들어 설명한다.

### 이 단계의 목표

이 단계가 끝나면:

- 출력은 단순히 "파일 저장"이 아니라
- 실시간 encode + network transmit 문제라는 점

을 이해해야 한다.

---

## 63. 10단계: 운영자 시점으로 로그 읽기

### 읽을 것

1. [native_runtime_cli.py](/c:/Users/Pixellot/Hogak_Stitching/stitching/native_runtime_cli.py)
2. 이 문서의 Q&A 섹션

### 실습 과제

#### 과제 1

실행해서 compact monitor를 띄운다.

```cmd
python -m stitching.cli native-runtime --no-output-ui --no-viewer --duration-sec 10 --monitor-mode compact
```

#### 과제 2

출력된 로그에서 아래 항목을 찾아 의미를 적는다.

- `stitch_actual_fps`
- `transmit_fps`
- `pair_skew_ms`
- `left_age_ms`, `right_age_ms`
- `waits`

#### 과제 3

스스로 아래 질문에 답해본다.

- 지금 병목은 입력인가 출력인가?
- fresh fps 문제인가 transmit cadence 문제인가?
- pair가 문제인가 source가 문제인가?

### 이 단계의 목표

이 단계가 끝나면:

- 단순히 실행만 하는 사람이 아니라
- 로그를 보고 상태를 해석하는 사람

이 되어야 한다.

---

## 64. 11단계: 아주 작은 수정 해보기

### 실습 과제

#### 과제 1

`config/profiles/camera25.json`을 읽고,
25fps profile이 실제로 어떤 값을 바꾸는지 확인한다.

#### 과제 2

`runtime.json`에서 `output_cadence_fps`를 읽고,
25와 30이 운영상 어떤 차이를 만드는지 글로 적는다.

#### 과제 3

`README.md`에서 한 줄 문구를 고쳐보는 아주 작은 문서 수정부터 해본다.

### 왜 이런 과제를 주는가

신입은 처음부터 성능 튜닝보다,

- 설정 읽기
- 작은 수정
- 영향 범위 파악

을 먼저 익히는 게 훨씬 중요하기 때문이다.

---

## 65. 12단계: 최종 목표

이 실습 과제를 다 끝낸 뒤의 목표는 아래와 같다.

후임이 다음 질문에 스스로 대답할 수 있으면 성공이다.

- 이 프로젝트의 입력과 출력은 무엇인가?
- Python과 C++는 무엇이 다른가?
- 왜 RTSP / FFmpeg / CUDA / NVENC를 같이 쓰는가?
- 왜 pair/sync가 중요한가?
- 왜 output fps와 fresh fps가 다를 수 있는가?
- 문제가 생기면 config를 볼지, 코드를 볼지 어떻게 판단하는가?

이 여섯 질문에 답할 수 있으면,
이미 프로젝트를 "처음 보는 사람" 단계는 벗어난 것이다.

---

## 66. 실전형 미니 프로젝트 과제

앞의 과제가 "읽고 이해하는 과제"였다면,
여기부터는 **직접 손으로 만들어보는 과제**다.

이 섹션의 목표는:

- 실시간 영상 시스템을 작은 조각부터 직접 만들어보게 하고
- 이 프로젝트가 왜 지금 구조를 갖게 됐는지 몸으로 느끼게 하는 것

이다.

중요한 원칙은 이렇다.

- 처음부터 이 프로젝트 전체를 다시 만들려고 하지 않는다
- 아주 작은 기능부터 하나씩 만든다
- 매 과제마다 "배운 점"을 적는다

---

## 67. 미니 과제 1: RTSP 한 개 받아서 화면에 출력하기

### 목표

가장 먼저 해야 할 일은:

**"실시간 네트워크 영상을 받아서 화면에 보여주는 최소 프로그램"**을 직접 만드는 것이다.

### 구현 목표

- RTSP 주소 하나를 입력으로 받기
- 프레임을 계속 읽기
- 화면에 보여주기
- 종료 키를 누르면 멈추기

### 추천 구현 방식

가장 쉬운 방법:

- Python
- OpenCV `VideoCapture`

예를 들어 후임이 직접 만들어볼 대상은 이런 수준이다.

```text
rtsp://... -> OpenCV VideoCapture -> cv2.imshow()
```

### 이 과제로 배우는 것

- RTSP가 파일이 아니라 live stream이라는 것
- 영상이 "계속 들어오는 것"이라는 것
- 입력이 늦거나 끊길 수 있다는 것

### 확장 과제

- 현재 fps를 화면에 표시해보기
- 프레임이 안 오면 timeout 메시지 띄우기
- TCP/UDP transport 차이 조사해보기

### 왜 중요한가

이 프로젝트의 제일 바닥은 결국 "RTSP live input"이다.
이걸 직접 안 만들어보면 나머지 pair/sync/stitch도 추상적으로만 이해하게 된다.

---

## 68. 미니 과제 2: RTSP 한 개 받아서 파일로 저장하기

### 목표

입력만 보는 게 아니라,
받은 영상을 다시 **출력 저장**하는 경험을 해보는 것이다.

### 구현 목표

- RTSP 한 개 읽기
- 일정 시간 동안 프레임 저장
- `.mp4` 또는 `.avi`로 기록

### 추천 구현 방식

- Python
- OpenCV `VideoCapture`
- OpenCV `VideoWriter`

### 이 과제로 배우는 것

- 입력과 출력은 별개라는 것
- 디코딩과 인코딩은 서로 다른 단계라는 것
- "받는 것"과 "다시 쓰는 것" 둘 다 비용이 있다는 것

### 확장 과제

- 입력 fps와 저장 fps를 각각 표시해보기
- 해상도 바꿔서 저장해보기

### 왜 중요한가

실시간 시스템은 입력만 되는 게 아니라,
**받은 걸 다시 내보내는 시스템**이라는 점을 몸으로 익히게 한다.

---

## 69. 미니 과제 3: RTSP 두 개 받아서 나란히 붙여보기

### 목표

본격적인 stitch 전 단계로,
두 입력을 동시에 다루는 감각을 익힌다.

### 구현 목표

- RTSP 두 개 받기
- 최신 프레임 각각 읽기
- 좌우로 그냥 붙이기
- 하나의 창에 출력하기

### 추천 구현 방식

- Python
- OpenCV
- `np.hstack()` 또는 OpenCV concat

### 이 과제로 배우는 것

- 카메라 두 개를 동시에 다루는 기본기
- 좌/우 fps가 실제로 다를 수 있다는 점
- 단순히 두 개를 이어붙이는 것과 stitch는 다르다는 점

### 확장 과제

- 각 영상 위에 `LEFT`, `RIGHT` 글씨 넣기
- 각 영상의 timestamp/fps를 표시해보기
- 한쪽이 늦으면 어떻게 보이는지 관찰하기

### 왜 중요한가

후임이 가장 먼저 착각하는 게
"두 개 영상이면 그냥 붙이면 되지 않나요?"이다.

이 과제를 해보면
**그건 stitch가 아니라 단순 concat**이라는 걸 스스로 깨닫게 된다.

---

## 70. 미니 과제 4: 두 입력의 시간차 측정기 만들기

### 목표

pair/sync 개념을 몸으로 익힌다.

### 구현 목표

- 좌/우 프레임이 들어온 시간을 각각 기록
- 가장 최신 프레임 기준 시간차 계산
- 콘솔에 `pair_skew_ms`처럼 보여주기

### 추천 구현 방식

- Python
- `time.time()` 또는 monotonic clock
- 간단한 ring buffer

### 이 과제로 배우는 것

- 왜 두 카메라 시간이 항상 안 맞는지
- sync가 왜 필요한지
- "가장 최신 두 프레임"이 항상 좋은 pair가 아니라는 것

### 확장 과제

- time difference가 30ms 이하일 때만 pair라고 가정해보기
- 그 이상이면 "skew too large" 출력하기

### 왜 중요한가

이 과제를 해보면
이 프로젝트의 `service pair mode`가 왜 필요한지 훨씬 잘 이해하게 된다.

---

## 71. 미니 과제 5: 설정 파일 읽어서 RTSP 열기

### 목표

config가 왜 필요한지 손으로 느끼게 한다.

### 구현 목표

- `config.json` 파일 만들기
- 그 안에 `left_rtsp`, `right_rtsp` 넣기
- 프로그램이 config를 읽어서 카메라 열기

### 추천 구현 방식

- Python
- `json` 모듈

### 이 과제로 배우는 것

- 설정과 코드 분리
- 운영값을 코드에 박아두면 왜 불편한지

### 확장 과제

- `runtime.local.json` 같은 local override 흉내내기
- profile 개념 흉내내기

### 왜 중요한가

이 프로젝트를 이해하려면
config가 단순한 파일이 아니라 **운영 분리 장치**라는 감각이 꼭 필요하다.

---

## 72. 미니 과제 6: homography 적용해서 한쪽 영상 warp해보기

### 목표

이 프로젝트 stitch의 핵심 수학을 직접 만져본다.

### 구현 목표

- 왼쪽 이미지 1장
- 오른쪽 이미지 1장
- 임의 또는 기존 homography
- 오른쪽 이미지를 `warpPerspective`

### 추천 구현 방식

- Python
- OpenCV

### 이 과제로 배우는 것

- homography가 실제 이미지에서 어떤 효과를 내는지
- 숫자 행렬이 실제 그림 변형으로 이어진다는 것

### 확장 과제

- identity homography와 비교
- translation만 줄 때와 full homography일 때 비교

### 왜 중요한가

stitch를 이해하려면,
"보정값 파일"이 실제로 뭘 하는지 눈으로 봐야 한다.

---

## 73. 미니 과제 7: feather blending 직접 구현해보기

### 목표

영상 합성이 왜 필요한지 느끼게 한다.

### 구현 목표

- 왼쪽 이미지
- 오른쪽 이미지
- overlap 구간 지정
- 선형 가중치로 섞기

### 추천 구현 방식

- Python
- NumPy
- OpenCV

### 이 과제로 배우는 것

- 그냥 붙이는 것과 blending의 차이
- 경계가 왜 티나는지
- feather가 왜 실시간에 적당한지

### 확장 과제

- hard cut와 feather 결과 비교하기
- feather width 바꿔보기

### 왜 중요한가

stitch는 "정렬"만이 아니라 "합성"까지 포함한다는 걸 직접 느낄 수 있다.

---

## 74. 미니 과제 8: "작은 stitcher" 만들기

### 목표

지금까지 배운 것들을 작은 수준에서 합친다.

### 구현 목표

- 좌우 이미지 입력
- homography 적용
- warp
- overlap blend
- 최종 결과 출력

### 추천 구현 방식

- Python
- 정적 이미지 먼저
- 그 다음 영상 1쌍으로 확장

### 이 과제로 배우는 것

- stitch의 핵심 알고리즘 구조
- 왜 실제 runtime이 복잡한지

### 왜 중요한가

이 과제를 해보면
이 프로젝트의 [stitch_engine.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/engine/stitch_engine.cpp)가
"괴물 같은 큰 파일"이 아니라
"작은 stitcher를 실시간 버전으로 키운 것"처럼 보이기 시작한다.

---

## 75. 미니 과제 9: ffmpeg 명령으로 RTSP 읽기 실습

### 목표

OpenCV 말고 ffmpeg가 왜 필요한지 감각을 익힌다.

### 구현 목표

직접 ffmpeg 명령을 써서:

- RTSP 입력 읽기
- rawvideo 출력
- 또는 파일 저장

을 해본다.

예:

```cmd
ffmpeg -rtsp_transport udp -i "rtsp://..." -f null -
```

또는

```cmd
ffmpeg -rtsp_transport udp -i "rtsp://..." -t 5 out.mp4
```

### 이 과제로 배우는 것

- ffmpeg가 실제로 무엇을 해주는지
- OpenCV보다 왜 더 세밀한 제어가 가능한지

### 확장 과제

- `-rtsp_transport tcp`와 비교
- `-hwaccel cuda` 차이 조사

### 왜 중요한가

이 프로젝트 입력 reader는 결국 ffmpeg를 적극 활용하기 때문이다.

---

## 76. 미니 과제 10: 실시간 출력 파이프라인 흉내내기

### 목표

입력만이 아니라 "출력 송출" 개념을 직접 느끼게 한다.

### 구현 목표

- 카메라 또는 파일 입력
- 화면에 표시
- 동시에 UDP/file로도 출력

### 추천 구현 방식

- ffmpeg subprocess
- Python에서 command build

### 이 과제로 배우는 것

- 왜 output writer가 따로 필요한지
- 출력 target, codec, muxer가 왜 나뉘는지

### 확장 과제

- `h264_nvenc`와 `libx264` 차이 조사
- bitrate를 바꿔보기

### 왜 중요한가

실시간 시스템은 "처리"로 끝나지 않고
"실제 서비스를 위해 다시 내보내는 것"까지가 핵심이기 때문이다.

---

## 77. 미니 과제 11: 간단한 실시간 모니터 만들기

### 목표

운영 모니터링이 왜 필요한지 직접 경험한다.

### 구현 목표

- 콘솔에 초당 1번
- input fps
- output fps
- delay
- dropped count

같은 값을 보여주기

### 추천 구현 방식

- Python
- 간단한 counter + timestamp

### 이 과제로 배우는 것

- "돌아간다"와 "상태가 보인다"의 차이
- 운영 시스템은 관측 가능성이 중요하다는 점

### 왜 중요한가

이 프로젝트는 단순 영상 코드가 아니라 운영 가능한 시스템이기 때문에,
모니터링 감각이 매우 중요하다.

---

## 78. 미니 과제 12: stale reuse 아이디어 체험해보기

### 목표

왜 반복 계산을 줄여야 하는지 체험한다.

### 구현 목표

- 같은 프레임이 반복되면
- 매번 full processing 하지 않고
- 이전 결과를 재사용하는 간단한 예제를 만들어보기

예:

- 이전 frame hash 저장
- 같으면 "reuse"
- 다르면 "recompute"

### 이 과제로 배우는 것

- stale 최적화의 의미
- 실시간 시스템에서 "새 정보가 없을 때 뭘 줄일 수 있는가"

### 왜 중요한가

이 프로젝트의 실제 성능 최적화 중 하나가 stale reuse이기 때문이다.

---

## 79. 미니 과제 13: 25fps / 30fps cadence 차이 실험하기

### 목표

output cadence 개념을 직접 이해한다.

### 구현 목표

- 같은 입력 영상에 대해
- output loop를 25fps로 한 번
- 30fps로 한 번

돌려본다.

### 실험 포인트

- 입력이 25fps일 때 30fps output은 어떤 느낌인지
- repeat가 생기는지
- 로그로 fresh fps와 output fps를 어떻게 구분할지

### 이 과제로 배우는 것

- 왜 input fps와 output cadence가 다를 수 있는지
- 왜 운영 profile이 필요한지

---

## 80. 미니 과제 14: "이 프로젝트를 흉내내는 작은 설계서" 써보기

### 목표

읽고, 만들고, 이해한 것을 자기 언어로 정리하게 한다.

### 과제 내용

후임이 아래를 A4 1~2장 정도로 직접 정리해본다.

- 입력은 무엇인가
- pair/sync는 왜 필요한가
- homography는 무엇인가
- warp/blend는 왜 필요한가
- 왜 GPU/NVENC를 쓰는가
- 왜 probe/transmit를 나눴는가

### 왜 중요한가

설명할 수 있어야 진짜 이해한 것이다.

---

## 81. 추천 진행 순서

실습 과제는 이 순서가 좋다.

1. RTSP 한 개 출력하기
2. RTSP 한 개 저장하기
3. RTSP 두 개 나란히 붙이기
4. 시간차 측정기 만들기
5. config 읽기
6. homography warp
7. feather blending
8. 작은 stitcher 만들기
9. ffmpeg 명령 실습
10. output 송출 흉내내기
11. 모니터 만들기
12. stale reuse 실험
13. 25/30 cadence 실험
14. 작은 설계서 쓰기

이 순서를 따라가면,
후임은 "문서만 읽은 사람"이 아니라
"실제로 시스템 구성요소를 작게나마 만들어본 사람"이 된다.

---

## 82. 마지막 조언

이 프로젝트를 이해할 때 중요한 건
"처음부터 전체를 이해하려고 하지 않는 것"이다.

대신 이렇게 접근하면 된다.

- 먼저 입력 하나
- 그다음 입력 둘
- 그다음 시간 맞추기
- 그다음 정렬하기
- 그다음 섞기
- 그다음 보내기
- 마지막에 운영/성능 보기

즉,
이 프로젝트 전체는 거대한 마법이 아니라
**작은 문제 여러 개를 차례대로 해결한 결과**라고 생각하면 훨씬 이해하기 쉽다.

---

## 83. 실전형 미니 프로젝트 예시 코드

이 섹션의 코드는 "정답 코드"가 아니라 **시작점(starter code)** 이다.

즉:

- 일단 돌려본다
- 동작을 확인한다
- 그 다음 스스로 고친다

가 목표다.

---

## 84. 예시 코드 1: RTSP 한 개 받아서 화면에 출력하기

대상 과제:

- 미니 과제 1

```python
import cv2
import time

RTSP_URL = "rtsp://admin:password@192.168.0.10:554/cam/realmonitor?channel=1&subtype=0"

cap = cv2.VideoCapture(RTSP_URL)
if not cap.isOpened():
    raise RuntimeError("RTSP stream open failed")

frame_count = 0
start = time.time()

while True:
    ok, frame = cap.read()
    if not ok or frame is None:
        print("frame read failed")
        break

    frame_count += 1
    elapsed = max(time.time() - start, 1e-6)
    fps = frame_count / elapsed

    cv2.putText(
        frame,
        f"fps={fps:.2f}",
        (20, 40),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        (0, 255, 0),
        2,
        cv2.LINE_AA,
    )

    cv2.imshow("RTSP Viewer", frame)
    key = cv2.waitKey(1) & 0xFF
    if key == 27 or key == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()
```

직접 해볼 확장:

- transport를 바꾸기
- 프레임 읽기 실패 시 재시도 넣기
- 해상도 출력하기

---

## 85. 예시 코드 2: RTSP 한 개 받아서 파일로 저장하기

대상 과제:

- 미니 과제 2

```python
import cv2
import time

RTSP_URL = "rtsp://admin:password@192.168.0.10:554/cam/realmonitor?channel=1&subtype=0"
OUT_PATH = "sample_record.mp4"
RECORD_SEC = 10

cap = cv2.VideoCapture(RTSP_URL)
if not cap.isOpened():
    raise RuntimeError("RTSP stream open failed")

width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920)
height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080)
fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)

fourcc = cv2.VideoWriter_fourcc(*"mp4v")
writer = cv2.VideoWriter(OUT_PATH, fourcc, fps, (width, height))
if not writer.isOpened():
    raise RuntimeError("VideoWriter open failed")

deadline = time.time() + RECORD_SEC
frames_written = 0

while time.time() < deadline:
    ok, frame = cap.read()
    if not ok or frame is None:
        print("frame read failed")
        break
    writer.write(frame)
    frames_written += 1

print(f"written_frames={frames_written}")
writer.release()
cap.release()
```

직접 해볼 확장:

- 저장 해상도를 절반으로 줄이기
- 저장 fps를 25로 강제해보기

---

## 86. 예시 코드 3: RTSP 두 개 받아서 나란히 붙여보기

대상 과제:

- 미니 과제 3

```python
import cv2
import numpy as np

LEFT_RTSP = "rtsp://admin:password@192.168.0.10:554/cam/realmonitor?channel=1&subtype=0"
RIGHT_RTSP = "rtsp://admin:password@192.168.0.11:554/cam/realmonitor?channel=1&subtype=0"

left_cap = cv2.VideoCapture(LEFT_RTSP)
right_cap = cv2.VideoCapture(RIGHT_RTSP)

if not left_cap.isOpened() or not right_cap.isOpened():
    raise RuntimeError("one or both RTSP streams failed to open")

while True:
    ok_l, left = left_cap.read()
    ok_r, right = right_cap.read()
    if not ok_l or not ok_r or left is None or right is None:
        print("frame read failed")
        break

    h = min(left.shape[0], right.shape[0])
    left_resized = cv2.resize(left, (int(left.shape[1] * h / left.shape[0]), h))
    right_resized = cv2.resize(right, (int(right.shape[1] * h / right.shape[0]), h))

    canvas = np.hstack([left_resized, right_resized])
    cv2.putText(canvas, "LEFT", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
    cv2.putText(canvas, "RIGHT", (left_resized.shape[1] + 20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)

    cv2.imshow("Dual RTSP Side-by-Side", canvas)
    key = cv2.waitKey(1) & 0xFF
    if key == 27 or key == ord("q"):
        break

left_cap.release()
right_cap.release()
cv2.destroyAllWindows()
```

직접 해볼 확장:

- 각 화면 위에 timestamp 넣기
- 한쪽이 끊겼을 때 placeholder 띄우기

---

## 87. 예시 코드 4: 두 입력의 시간차 측정기

대상 과제:

- 미니 과제 4

```python
import cv2
import time

LEFT_RTSP = "rtsp://admin:password@192.168.0.10:554/cam/realmonitor?channel=1&subtype=0"
RIGHT_RTSP = "rtsp://admin:password@192.168.0.11:554/cam/realmonitor?channel=1&subtype=0"

left_cap = cv2.VideoCapture(LEFT_RTSP)
right_cap = cv2.VideoCapture(RIGHT_RTSP)

if not left_cap.isOpened() or not right_cap.isOpened():
    raise RuntimeError("one or both RTSP streams failed to open")

while True:
    ok_l, left = left_cap.read()
    ts_l = time.monotonic_ns()
    ok_r, right = right_cap.read()
    ts_r = time.monotonic_ns()

    if not ok_l or not ok_r:
        print("frame read failed")
        break

    skew_ms = abs(ts_l - ts_r) / 1_000_000.0
    print(f"pair_skew_ms={skew_ms:.2f}")

    if skew_ms > 50.0:
        print("warning: skew too large")

    if cv2.waitKey(1) & 0xFF in (27, ord("q")):
        break

left_cap.release()
right_cap.release()
```

직접 해볼 확장:

- 최근 30개 skew 평균 구하기
- `service pair` 흉내로 가장 가까운 ts끼리 맞춰보기

---

## 88. 예시 코드 5: config 파일 읽어서 RTSP 열기

대상 과제:

- 미니 과제 5

예시 `config.json`

```json
{
  "left_rtsp": "rtsp://admin:password@192.168.0.10:554/cam/realmonitor?channel=1&subtype=0",
  "right_rtsp": "rtsp://admin:password@192.168.0.11:554/cam/realmonitor?channel=1&subtype=0"
}
```

예시 코드:

```python
import json
from pathlib import Path
import cv2

config = json.loads(Path("config.json").read_text(encoding="utf-8"))

left_rtsp = config["left_rtsp"]
right_rtsp = config["right_rtsp"]

left_cap = cv2.VideoCapture(left_rtsp)
right_cap = cv2.VideoCapture(right_rtsp)

print("left opened:", left_cap.isOpened())
print("right opened:", right_cap.isOpened())

left_cap.release()
right_cap.release()
```

직접 해볼 확장:

- `config.local.json`이 있으면 덮어쓰기
- `profile` 개념 흉내내기

---

## 89. 예시 코드 6: homography 적용해서 warp해보기

대상 과제:

- 미니 과제 6

```python
import cv2
import numpy as np

left = cv2.imread("left.jpg")
right = cv2.imread("right.jpg")

if left is None or right is None:
    raise RuntimeError("left.jpg or right.jpg not found")

H = np.array([
    [1.0, 0.0, 120.0],
    [0.0, 1.0, 10.0],
    [0.0, 0.0, 1.0],
], dtype=np.float64)

canvas_w = max(left.shape[1], right.shape[1] + 200)
canvas_h = max(left.shape[0], right.shape[0] + 50)

warped_right = cv2.warpPerspective(right, H, (canvas_w, canvas_h))

cv2.imshow("left", left)
cv2.imshow("warped_right", warped_right)
cv2.waitKey(0)
cv2.destroyAllWindows()
```

직접 해볼 확장:

- identity 행렬로 비교
- 직접 여러 translation 값을 바꿔보기

---

## 90. 예시 코드 7: feather blending 직접 구현하기

대상 과제:

- 미니 과제 7

```python
import cv2
import numpy as np

left = cv2.imread("left.jpg")
right = cv2.imread("right.jpg")

if left is None or right is None:
    raise RuntimeError("left.jpg or right.jpg not found")

height = min(left.shape[0], right.shape[0])
left = cv2.resize(left, (left.shape[1], height))
right = cv2.resize(right, (right.shape[1], height))

overlap = 200
canvas_w = left.shape[1] + right.shape[1] - overlap
canvas = np.zeros((height, canvas_w, 3), dtype=np.uint8)

canvas[:, :left.shape[1]] = left

for x in range(right.shape[1]):
    dst_x = left.shape[1] - overlap + x
    if dst_x < left.shape[1]:
        alpha = (x / max(overlap - 1, 1))
        blended = (1.0 - alpha) * canvas[:, dst_x].astype(np.float32) + alpha * right[:, x].astype(np.float32)
        canvas[:, dst_x] = blended.astype(np.uint8)
    else:
        canvas[:, dst_x] = right[:, x]

cv2.imshow("feather_blend", canvas)
cv2.waitKey(0)
cv2.destroyAllWindows()
```

직접 해볼 확장:

- `overlap` 크기 바꾸기
- hard cut 버전과 비교하기

---

## 91. 예시 코드 8: 작은 stitcher 만들기

대상 과제:

- 미니 과제 8

```python
import cv2
import numpy as np

left = cv2.imread("left.jpg")
right = cv2.imread("right.jpg")

if left is None or right is None:
    raise RuntimeError("left.jpg or right.jpg not found")

H = np.array([
    [1.0, 0.0, 120.0],
    [0.0, 1.0, 10.0],
    [0.0, 0.0, 1.0],
], dtype=np.float64)

canvas_w = left.shape[1] + right.shape[1]
canvas_h = max(left.shape[0], right.shape[0]) + 100

warped_right = cv2.warpPerspective(right, H, (canvas_w, canvas_h))

canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
canvas[:left.shape[0], :left.shape[1]] = left

mask_right = (warped_right.sum(axis=2) > 0)
mask_left = (canvas.sum(axis=2) > 0)
overlap_mask = mask_left & mask_right

result = canvas.copy()
result[mask_right & ~overlap_mask] = warped_right[mask_right & ~overlap_mask]

ys, xs = np.where(overlap_mask)
if len(xs) > 0:
    x_min, x_max = xs.min(), xs.max()
    for x in range(x_min, x_max + 1):
        alpha = (x - x_min) / max(x_max - x_min, 1)
        col_mask = overlap_mask[:, x]
        result[col_mask, x] = (
            (1.0 - alpha) * canvas[col_mask, x].astype(np.float32)
            + alpha * warped_right[col_mask, x].astype(np.float32)
        ).astype(np.uint8)

cv2.imshow("small_stitcher_result", result)
cv2.waitKey(0)
cv2.destroyAllWindows()
```

이 코드는 아주 단순한 버전이다.
실제 프로젝트와 차이는 많지만,
"warp + overlap + blend"라는 핵심 구조를 몸으로 느끼기엔 충분하다.

---

## 92. 예시 코드 9: ffmpeg 명령을 Python에서 만들어 실행하기

대상 과제:

- 미니 과제 9
- 미니 과제 10

```python
import subprocess

rtsp = "rtsp://admin:password@192.168.0.10:554/cam/realmonitor?channel=1&subtype=0"

command = [
    "ffmpeg",
    "-rtsp_transport", "udp",
    "-i", rtsp,
    "-t", "5",
    "-c:v", "libx264",
    "-pix_fmt", "yuv420p",
    "sample_out.mp4",
]

print("running:", " ".join(command))
completed = subprocess.run(command, check=False)
print("returncode:", completed.returncode)
```

직접 해볼 확장:

- `libx264`를 `h264_nvenc`로 바꿔보기
- `udp`와 `tcp` transport 비교
- target을 file 대신 UDP로 바꿔보기

---

## 93. 예시 코드 10: 간단한 실시간 모니터

대상 과제:

- 미니 과제 11

```python
import time

frame_count = 0
drop_count = 0
start = time.time()
last_report = start

while True:
    # 여기에 실제 frame read 또는 처리 코드가 들어간다고 가정
    frame_count += 1
    now = time.time()

    if now - last_report >= 1.0:
        elapsed = now - start
        fps = frame_count / max(elapsed, 1e-6)
        print(f"fps={fps:.2f} frames={frame_count} drops={drop_count}")
        last_report = now

    if frame_count >= 300:
        break
```

이 코드는 아주 단순하지만,
"상태를 주기적으로 보여주는 습관"의 출발점으로는 좋다.

---

## 94. 예시 코드 사용법

이 예시 코드를 볼 때는 아래 순서로 접근하는 걸 추천한다.

1. 먼저 그대로 실행해본다
2. 입력/출력이 실제로 뭔지 확인한다
3. 한 줄만 바꿔본다
4. 로그나 결과 차이를 관찰한다
5. 왜 차이가 났는지 스스로 설명해본다

즉 중요한 건:

- 복붙 그 자체가 아니라
- "작게 바꾸고, 관찰하고, 이유를 설명하는 것"

이다.
