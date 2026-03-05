# Dual Smartphone Video Stitching PoC

오프라인으로 좌/우 스마트폰 영상을 받아 파노라마 영상을 생성하는 프로젝트입니다.
현재 기준은 **비디오/라이브 스티칭 MVP**이며, 결과물(`.mp4`)과 `report.json`을 함께 생성합니다.

## 1) 설치
```powershell
python -m pip install -r requirements.txt
```

## 2) 빠른 실행
```powershell
python -m stitching video-10s --pair video10
python -m stitching video-30s --pair video10
python -m stitching video-full --pair video10
```

## 3) GUI 실행(권장)
오프라인/프리셋/라이브/서비스 기능을 한 화면에서 제어합니다.

```powershell
python -m stitching gui --host 127.0.0.1 --port 7860
```

- 브라우저에서 `http://127.0.0.1:7860` 접속
- 라이브 탭에서 `Left RTSP`, `Right RTSP` 입력 후 시작
- 좌/우/스티칭 프레임을 같은 화면에서 실시간 확인 가능
- 라이브 중지 시 출력 mp4/report를 바로 확인 가능

## 4) 수동 실행
```powershell
python -m stitching video `
  --left .\input\videos\video10_left.mp4 `
  --right .\input\videos\video10_right.mp4 `
  --out .\output\videos\video10_manual.mp4 `
  --report .\output\videos\video10_manual_report.json `
  --debug-dir .\output\debug\video10_manual `
  --max-duration-sec 30
```

## 5) RTSP 실시간 스티칭(CLI)
RTSP 2개 주소를 받아 실시간으로 붙여서 mp4와 report를 남깁니다.

```powershell
python -m stitching live `
  --left-rtsp 'rtsp://admin:***@192.168.0.10/cam/realmonitor?channel=1&subtype=0' `
  --right-rtsp 'rtsp://admin:***@192.168.0.11/cam/realmonitor?channel=1&subtype=0' `
  --out .\output\videos\live_stitched.mp4 `
  --report .\output\videos\live_report.json `
  --debug-dir .\output\debug\live `
  --max-duration-sec 30 `
  --output-fps 20 `
  --perf-mode balanced
```

- PowerShell에서는 RTSP URL에 `&`가 포함되므로 **반드시 작은따옴표**로 감싸세요.
- `--max-duration-sec N`은 **실행 시간**이 아니라 **최종 출력 영상 길이 N초**를 의미합니다.
- `--max-duration-sec 0`이면 수동 종료까지 계속 실행합니다.
- `--preview`를 주면 창에서 확인 가능하며 `q`로 종료합니다.
- CPU가 느리면 중간 프레임을 버리고 최신으로 따라붙는 정책을 사용합니다.
  - `--sync-pair-mode latest|oldest`
  - `--max-live-lag-sec 1.0`

## 6) 주요 옵션
- H 저장/재사용
  - `--homography-mode off|auto|reuse|refresh`
  - `--homography-file .\output\videos\video10_h.json`
- 성능 모드
  - `--perf-mode quality|balanced|fast`
  - `--process-scale 0.5` (필요 시 수동 지정)
- seam 실험 옵션(기본 비활성)
  - `--adaptive-seam on|off` (기본 `off`)
  - `--seam-update-interval 12`
  - `--seam-temporal-penalty 1.5`
  - `--seam-motion-weight 1.5`

## 7) API 서버(선택)
```powershell
python -m stitching serve --host 127.0.0.1 --port 8080 --storage-dir .\storage
```

작업 생성:
```powershell
curl -X POST http://127.0.0.1:8080/jobs/video-stitch `
  -H "Content-Type: application/json" `
  -d "{\"left_path\":\"C:/path/to/video_left.mp4\",\"right_path\":\"C:/path/to/video_right.mp4\",\"options\":{\"max_duration_sec\":30,\"perf_mode\":\"balanced\",\"homography_mode\":\"auto\"}}"
```

작업 조회:
- `GET /jobs/{job_id}`
- `GET /jobs/{job_id}/report`
- `GET /jobs/{job_id}/artifact`

## 8) 에러 코드
- `PROBE_FAIL`
- `OVERLAP_LOW`
- `HOMOGRAPHY_FAIL`
- `ENCODE_FAIL`
- `INTERNAL_ERROR`

## 9) report.json 키 형식
- 저장 시 모든 키는 `한글(영어)` 형식으로 기록됩니다.
- 예: `상태(status)`, `오류코드(error_code)`, `메트릭(metrics)`.
