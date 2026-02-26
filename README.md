# 듀얼 스마트폰 스티칭 PoC

이 프로젝트는 다음을 위한 최소 기능 PoC입니다.

- 이미지 스티칭: `left.jpg + right.jpg -> stitched_image.png`
- 영상 스티칭(오프라인): `left.mp4 + right.mp4 -> stitched.mp4`
- 백엔드 잡 처리: API + 큐 워커 + 스토리지

## 1) 폴더 구조(고정)

재현 가능한 테스트를 위해 아래 구조와 네이밍 규칙을 사용합니다.

```text
Stitching/
  input/
    images/
      pair01_left.jpg
      pair01_right.jpg
      pair02_left.jpg
      pair02_right.jpg
      ...
    videos/
      pair01_left.mp4
      pair01_right.mp4
      pair02_left.mp4
      pair02_right.mp4
      ...
  output/
    images/
    videos/
    debug/
  storage/
    raw/
    debug/
    out/
    report/
    jobs/
```

데이터 기준:

- 이미지 `10 pairs` = `20개 파일` (`left/right` x 10)
- 영상 `3 pairs` = `6개 파일` (`left/right` x 3)

## 2) 설치

```powershell
python -m pip install -r requirements.txt
```

## 3) 이미지 스티칭 실행

`pair01` 예시:

```powershell
python -m stitching image `
  --left .\input\images\pair01_left.jpg `
  --right .\input\images\pair01_right.jpg `
  --out .\output\images\pair01_stitched.png `
  --report .\output\images\pair01_report.json `
  --debug-dir .\output\debug\pair01
```

예상 산출물:

- `output/images/pair01_stitched.png`
- `output/images/pair01_report.json`
- 디버그: `matches.jpg`, `inliers.jpg`, `warp_overlay.png`

현재 1세트만 있으면 `pair01`부터 실행해서 아래를 확인하세요.

- `pair01_report.json`의 `status`가 `succeeded` 또는 기대한 실패 코드인지
- `metrics.matches_count`, `metrics.inliers_count`, `processing_time_sec`가 존재하는지

## 4) 영상 스티칭 실행(오프라인)

빠른 프리셋(권장):

```powershell
python -m stitching video-10s --pair video04
python -m stitching video-30s --pair video04
python -m stitching video-full --pair video04
```

`--pair`를 생략하면 `input/videos`에서 최신 유효 `*_left/*_right` 쌍을 자동 선택합니다.

출력 파일명은 자동 생성됩니다.

- `output/videos/{pair}_10s_stitched.mp4`
- `output/videos/{pair}_30s_stitched.mp4`
- `output/videos/{pair}_full_stitched.mp4`
- 대응하는 `*_report.json`
- 디버그 폴더: `output/debug/{pair}_{preset}/`

수동 모드(경로 직접 지정):

```powershell
python -m stitching video `
  --left .\input\videos\video04_left.mp4 `
  --right .\input\videos\video04_right.mp4 `
  --out .\output\videos\video04_manual.mp4 `
  --report .\output\videos\video04_manual_report.json `
  --debug-dir .\output\debug\video04_manual `
  --max-duration-sec 30 `
  --sync-sample-sec 8
```

예상 산출물:

- 스티칭 영상 + `report.json`

## 5) 백엔드 API + 워커

서버 실행:

```powershell
python -m stitching serve --host 127.0.0.1 --port 8080 --storage-dir .\storage
```

이미지 잡 요청:

```powershell
curl -X POST http://127.0.0.1:8080/jobs/image-stitch `
  -H "Content-Type: application/json" `
  -d "{\"left_path\":\"C:/path/to/pair01_left.jpg\",\"right_path\":\"C:/path/to/pair01_right.jpg\"}"
```

영상 잡 요청:

```powershell
curl -X POST http://127.0.0.1:8080/jobs/video-stitch `
  -H "Content-Type: application/json" `
  -d "{\"left_path\":\"C:/path/to/pair01_left.mp4\",\"right_path\":\"C:/path/to/pair01_right.mp4\",\"options\":{\"max_duration_sec\":20}}"
```

상태/리포트/아티팩트 조회:

```powershell
curl http://127.0.0.1:8080/jobs/{job_id}
curl http://127.0.0.1:8080/jobs/{job_id}/report
curl http://127.0.0.1:8080/jobs/{job_id}/artifact
```

## 6) 에러 코드

- `PROBE_FAIL`
- `OVERLAP_LOW`
- `HOMOGRAPHY_FAIL`
- `SYNC_FAIL`
- `ENCODE_FAIL`
- `INTERNAL_ERROR`

## 7) report.json (필수)

`report.json`은 성공/실패 모두 생성됩니다.

필수 필드:

- `status`: `succeeded` 또는 `failed`
- `error_code`
- `metrics.matches_count`
- `metrics.inliers_count`
- `metrics.processing_time_sec` (stage + total)
- `metrics.output_resolution`
- `metrics.estimated_sync_offset_ms` (video)

