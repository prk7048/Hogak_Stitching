# Dual Smartphone Video Stitching PoC

이 프로젝트는 **이미 시간 동기화가 끝난 두 영상**을 입력으로 받아
하나의 파노라마 영상으로 합성하는 오프라인 PoC입니다.

## 핵심 범위

- 입력: `left.mp4`, `right.mp4` (이미 동기화 완료 상태)
- 출력:
  - `stitched.mp4`
  - `report.json`
- 지원: 오프라인 비디오 스티칭 + Job API(비디오 전용)

## 폴더 구조

```text
Stitching/
  input/
    videos/
      video00_left.mp4
      video00_right.mp4
      ...
  output/
    videos/
    debug/
  storage/
    raw/
    debug/
    out/
    report/
    jobs/
```

## 설치

```powershell
python -m pip install -r requirements.txt
```

## 실행 방법

### 1) 프리셋 실행 (권장)

```powershell
python -m stitching video-10s --pair video10
python -m stitching video-30s --pair video10
python -m stitching video-full --pair video10
```

- `--pair` 생략 시 `input/videos`에서 최신 `*_left/*_right` 쌍을 자동 선택
- 출력 파일:
  - `output/videos/{pair}_10s_stitched.mp4`
  - `output/videos/{pair}_30s_stitched.mp4`
  - `output/videos/{pair}_full_stitched.mp4`
  - 대응 `*_report.json`
  - 디버그: `output/debug/{pair}_{preset}/video_inliers.jpg`

### 2) 수동 실행

```powershell
python -m stitching video `
  --left .\input\videos\video10_left.mp4 `
  --right .\input\videos\video10_right.mp4 `
  --out .\output\videos\video10_manual.mp4 `
  --report .\output\videos\video10_manual_report.json `
  --debug-dir .\output\debug\video10_manual `
  --max-duration-sec 30 `
  --calib-start-sec 0 `
  --calib-end-sec 10 `
  --calib-step-sec 1
```

## 현재 동작 원칙

1. 시간 동기화는 **파이프라인 내부에서 수행하지 않음**
2. 입력 두 영상은 이미 동기화되어 있다고 가정
3. 캘리브레이션 구간(`calib-start/end/step`)에서 여러 시점을 평가해
   가장 안정적인 호모그래피를 선택

## API 서버 (비디오 전용)

### 서버 실행

```powershell
python -m stitching serve --host 127.0.0.1 --port 8080 --storage-dir .\storage
```

### 비디오 작업 생성

```powershell
curl -X POST http://127.0.0.1:8080/jobs/video-stitch `
  -H "Content-Type: application/json" `
  -d "{\"left_path\":\"C:/path/to/video10_left.mp4\",\"right_path\":\"C:/path/to/video10_right.mp4\",\"options\":{\"max_duration_sec\":30}}"
```

### 상태/리포트/산출물 조회

```powershell
curl http://127.0.0.1:8080/jobs/{job_id}
curl http://127.0.0.1:8080/jobs/{job_id}/report
curl http://127.0.0.1:8080/jobs/{job_id}/artifact
```

## 에러 코드

- `PROBE_FAIL`
- `OVERLAP_LOW`
- `HOMOGRAPHY_FAIL`
- `ENCODE_FAIL`
- `INTERNAL_ERROR`

## report.json 핵심 필드

- `status`: `succeeded` / `failed`
- `error_code`
- `metrics.matches_count`
- `metrics.inliers_count`
- `metrics.processing_time_sec`
- `metrics.output_resolution`
- `metrics.processed_frames`
- `metrics.calib_used_time_sec`
- `metrics.overlap_diff_mean`
