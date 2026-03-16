# Deployment And Support Guide

이 문서는 환경의존성 제거 작업을 `1차~5차`로 나눠 현재 어디까지 닫혔는지와, 실제 배포/운영 시 무엇을 기준으로 삼아야 하는지 정리한다.

## 1차. 코드 하드코딩 제거

완료 기준:

- 카메라 RTSP 주소
- homography 경로
- probe/transmit target
- output cadence 같은 운영값

이 값들이 코드가 아니라 [config/runtime.json](/c:/Users/Pixellot/Hogak_Stitching/config/runtime.json)에서 오도록 정리됐다.

## 2차. 실행/설정 프로파일화

완료 기준:

- 기본 site config 위에 운영 profile을 덧씌울 수 있어야 한다
- Python CLI와 `.cmd` 스크립트가 같은 profile을 읽어야 한다

현재 구조:

- base config: [config/runtime.json](/c:/Users/Pixellot/Hogak_Stitching/config/runtime.json)
- override profile: [config/profiles](/c:/Users/Pixellot/Hogak_Stitching/config/profiles)

예:

- `camera25`: 25fps cadence
- `prod`: 운영용 기본값
- `dev`: 짧은 상태 확인용

## 3차. 빌드 환경 정리

완료 기준:

- 공용 preset이 개발자 PC 경로를 직접 들고 있지 않아야 한다
- 로컬 경로는 user preset 또는 env로 주입해야 한다

현재 구조:

- 공용 preset: [native_runtime/CMakePresets.json](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/CMakePresets.json)
- 로컬 예시: [native_runtime/CMakeUserPresets.example.json](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/CMakeUserPresets.example.json)
- 빌드 명령: `cmake --preset windows-release` + `cmake --build --preset build-windows-release`

## 4차. 배포 패키지 구조

완료 기준:

- repo 전체를 들고 가지 않아도 runtime bundle을 만들 수 있어야 한다
- bundle 안에 필요한 실행 파일, config, data, launch script가 같이 들어 있어야 한다

현재는 별도 패키징 스크립트를 유지하지 않는다.

생성 결과 기본 위치:

- `dist/runtime-bundle/`

포함 내용:

- `stitching/`
- `config/`
- `data/`
- `output/` placeholder
- `native_runtime/build/windows-release/Release/stitch_runtime.exe`
- `.third_party/ffmpeg/current/bin/*`

예:

```cmd
python -m stitching.cli native-runtime --no-output-ui --no-viewer --duration-sec 3 --monitor-mode compact
```

## 5차. 지원 환경/운영 문서

완료 기준:

- 어떤 환경이 지원 대상인지 짧고 명확하게 말할 수 있어야 한다
- 현재 머신이 지원 대상인지 바로 점검할 수 있어야 한다

현재는 별도 support checker 대신 headless runtime smoke를 지원 여부 기준으로 본다.

예:

```cmd
python -m stitching.cli native-runtime --no-output-ui --no-viewer --duration-sec 3 --monitor-mode compact
```

## 현재 지원 기준

현재 기준에서 이 프로젝트는 아래를 지원 대상으로 본다.

- Windows
- NVIDIA GPU
- 최신 드라이버가 잡히는 `nvidia-smi`
- `stitch_runtime.exe` 존재
- `ffmpeg.exe` 존재

추가로 `gpu-direct`까지 바로 쓰려면:

- OpenCV/CUDA 런타임
- FFmpeg/NVENC 경로

가 현재 머신에서 같이 잡혀야 한다.

## 현재 남은 것

환경의존성 제거 작업은 대부분 끝났고, 남은 건 지원 기준을 실제 운영 절차에 붙이는 일이다.

남은 실무 작업:

1. bundle을 다른 Windows 머신에 복사해서 실제 실행 검증
2. `prod` profile을 현장 값으로 확정
3. 운영자가 보는 “장비 점검 순서”를 한 장짜리 체크리스트로 더 축약
