# Dual Smartphone Stitching PoC

This project provides a minimal PoC for:

- image stitching: `left.jpg + right.jpg -> stitched_image.png`
- video stitching (offline): `left.mp4 + right.mp4 -> stitched.mp4`
- backend job flow: API + queue worker + storage

## 1) Folder Structure (Fixed)

Use this structure and naming convention for reproducible tests:

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

Data policy:

- image `10 pairs` means `20 files` (`left/right` x 10)
- video `3 pairs` means `6 files` (`left/right` x 3)

## 2) Install

```powershell
python -m pip install -r requirements.txt
```

## 3) Run Image Stitching

Example for `pair01`:

```powershell
python -m stitching image `
  --left .\input\images\pair01_left.jpg `
  --right .\input\images\pair01_right.jpg `
  --out .\output\images\pair01_stitched.png `
  --report .\output\images\pair01_report.json `
  --debug-dir .\output\debug\pair01
```

Expected outputs:

- `output/images/pair01_stitched.png`
- `output/images/pair01_report.json`
- debug: `matches.jpg`, `inliers.jpg`, `warp_overlay.png`

If you currently have only one pair, start with `pair01` only and validate:

- `status` in `pair01_report.json` is `succeeded` or expected failure code
- `metrics.matches_count`, `metrics.inliers_count`, and `processing_time_sec` exist

## 4) Run Video Stitching (Offline)

Quick presets (recommended):

```powershell
python -m stitching video-10s --pair video04
python -m stitching video-30s --pair video04
python -m stitching video-full --pair video04
```

If `--pair` is omitted, the latest valid `*_left/*_right` pair in `input/videos` is selected automatically.

Generated filenames are automatic:

- `output/videos/{pair}_10s_stitched.mp4`
- `output/videos/{pair}_30s_stitched.mp4`
- `output/videos/{pair}_full_stitched.mp4`
- corresponding `*_report.json`
- debug folder: `output/debug/{pair}_{preset}/`

Advanced manual mode (explicit paths):

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

Expected output:

- stitched video + `report.json`

## 5) Backend API + Worker

Run server:

```powershell
python -m stitching serve --host 127.0.0.1 --port 8080 --storage-dir .\storage
```

Submit image job:

```powershell
curl -X POST http://127.0.0.1:8080/jobs/image-stitch `
  -H "Content-Type: application/json" `
  -d "{\"left_path\":\"C:/path/to/pair01_left.jpg\",\"right_path\":\"C:/path/to/pair01_right.jpg\"}"
```

Submit video job:

```powershell
curl -X POST http://127.0.0.1:8080/jobs/video-stitch `
  -H "Content-Type: application/json" `
  -d "{\"left_path\":\"C:/path/to/pair01_left.mp4\",\"right_path\":\"C:/path/to/pair01_right.mp4\",\"options\":{\"max_duration_sec\":20}}"
```

Check status/report/artifact:

```powershell
curl http://127.0.0.1:8080/jobs/{job_id}
curl http://127.0.0.1:8080/jobs/{job_id}/report
curl http://127.0.0.1:8080/jobs/{job_id}/artifact
```

## 6) Error Codes

- `PROBE_FAIL`
- `OVERLAP_LOW`
- `HOMOGRAPHY_FAIL`
- `SYNC_FAIL`
- `ENCODE_FAIL`
- `INTERNAL_ERROR`

## 7) report.json (Required)

`report.json` is generated for both success and failure.

Required fields:

- `status`: `succeeded` or `failed`
- `error_code`
- `metrics.matches_count`
- `metrics.inliers_count`
- `metrics.processing_time_sec` (stage + total)
- `metrics.output_resolution`
- `metrics.estimated_sync_offset_ms` (video)
