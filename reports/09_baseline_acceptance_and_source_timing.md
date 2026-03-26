# Baseline Acceptance And Source Timing

이 문서는 현재 phase의 운영 기준을 고정하고, `strict fresh 30fps` 검증에서 무엇을 보고 pass / investigate / fail을 판단할지 정리한 문서다.

## Phase Goal

현재 단계의 목표는 아래 셋을 같이 닫는 것이다.

1. baseline을 `ffmpeg-cuda + nv12 + service + gpu-direct + strict fresh 30`으로 고정
2. input timestamp를 `arrival`과 `source`로 분리
3. 기본 pairing을 `stream_pts + auto offset`으로 옮기고, 실패 시 `fallback-arrival`로 유지

## Baseline Definition

현재 운영 기준 baseline은 다음이다.

- input runtime: `ffmpeg-cuda`
- input reader: in-process `libav` demux/decode
- input format: `nv12`
- input buffer: `8`
- pair mode: `service`
- allow frame reuse: baseline preset 기준
- transmit runtime: `gpu-direct`
- codec baseline: `h264_nvenc`
- target cadence: `30fps`

`25fps` profile은 계속 지원하지만, 이번 단계의 acceptance 주목표는 아니다.

## Timing Model

reader는 프레임마다 두 종류의 시간을 보존한다.

- `arrival`
  - 우리 프로그램이 decode 완료 frame을 reader buffer에 넣은 시각
  - 운영 age / queue health / arrival skew 해석에 사용
- `source`
  - 스트림 안의 frame timestamp를 바탕으로 보존한 시각
  - 기본 운영 경로에서는 `stream_pts + offset` 기준 pair selection에 사용
  - `wallclock`은 진단용/명시적 opt-in 기준으로만 유지

현재 monitor에서 보는 값의 의미는 아래와 같다.

- `left_age_ms`, `right_age_ms`
  - arrival 기준 최신 frame age
- `pair_skew_ms_mean`
  - arrival 기준 pair skew
- `left_source_age_ms`, `right_source_age_ms`
  - explicit `wallclock` 모드에서만 해석하는 source age
- `pair_source_skew_ms_mean`
  - `stream_pts_offset` 또는 `wallclock` 기준 source skew
- `source_time_mode`
  - `stream_pts_offset`: source PTS + offset 기준 pairing 사용
  - `wallclock`: explicit opt-in 시에만 wallclock 기준 pairing 사용
  - `fallback-arrival`: cross-camera source 비교가 안전하지 않아 arrival 기준 pairing 사용
- `sync_effective_offset_ms`
  - 현재 pair selection에 적용한 right-stream offset
- `sync_offset_source`
  - `auto`, `manual`, `recalibration`, `arrival-fallback`, `wallclock`
- `sync_offset_confidence`
  - auto/recalibration offset 신뢰도
- `sync_recalibration_count`
  - runtime 중 offset 재보정 횟수

이번 단계의 기본 운영 모드는 `pts-offset-auto`다. auto가 실패하면 `arrival`로 떨어지고,
운영자가 원하면 `pts-offset-manual` 또는 `pts-offset-hybrid`로 고정할 수 있다.

## Acceptance Checks

운영 기준 검증은 아래 명령으로 반복 가능하게 고정한다.

```cmd
python -m stitching.cli native-validate --duration-sec 10
python -m stitching.cli native-validate --duration-sec 600
python -m stitching.cli native-validate --duration-sec 1800
```

기본 report 경로:

```text
output/debug/native_validate_<label>_<timestamp>.json
```

report에서 우선 볼 값:

- `source_probe.result`
- `source_probe.cross_camera_wallclock_comparable`
- `runtime_validation.decision`
- `runtime_validation.bottleneck_guess`
- `runtime_validation.status_counts`
- `runtime_validation.source_mode_counts`
- `runtime_validation.final_metrics`

### Smoke

목적:
- 실행 경로, config load, metrics schema, reader startup, pair loop가 깨지지 않았는지 빠르게 확인

권장 실행:

```cmd
python -m stitching.cli native-runtime --no-output-ui --no-viewer --duration-sec 10 --monitor-mode compact
python -m stitching.cli native-validate --duration-sec 10
```

pass 기준:
- 프로세스가 정상 시작/종료
- metrics line에 `source_time_mode`, `pair_source_skew_ms_mean`, `left_source_age_ms`, `right_source_age_ms`가 포함됨
- reader restart storm 없이 좌/우 input이 살아 있음

investigate 기준:
- 실행은 되지만 `waiting sync pair`가 지속됨
- `pair_skew_ms_mean`이 크게 흔들림
- source metrics가 항상 0이거나, expected environment인데 source valid가 안 뜸

fail 기준:
- config load 실패
- native runtime startup 실패
- metrics consumer가 새 필드 때문에 깨짐
- reader startup 자체 실패

### 10-Minute Validation

목적:
- short-run이 아니라 실제 운영 baseline으로 이어질지 판단

권장 실행:

```cmd
python -m stitching.cli native-validate --duration-sec 600
```

기록할 것:
- `stitch_actual_fps`
- `transmit_fps`
- `pair_skew_ms_mean`
- `pair_source_skew_ms_mean`
- `source_time_mode`
- `wait_next_frame_count`
- `wait_sync_pair_count`
- `left_read_failures`, `right_read_failures`
- `left_reader_restarts`, `right_reader_restarts`

pass 기준:
- runtime가 안정적으로 유지
- input restart storm 없음
- `transmit_fps`와 `stitch_actual_fps` 해석이 가능함
- 기본 운영 경로에서 `source_time_mode=stream_pts_offset`이 유지되거나, 아니면 명확히 `fallback-arrival`로 유지됨
- `sync_effective_offset_ms`, `sync_offset_source`, `sync_offset_confidence`로 현재 offset 전략을 읽을 수 있음

investigate 기준:
- `waiting sync pair` 비율이 높음
- source mode가 자주 바뀌거나 confidence가 계속 낮음
- arrival skew와 source skew가 함께 크게 흔들림

fail 기준:
- 반복적인 reader failure / restart
- cadence 붕괴로 baseline 해석 자체가 어려움

### 30-Minute Validation

목적:
- `strict fresh 30fps`를 운영 baseline으로 둘 수 있는지 판정

권장 실행:

```cmd
python -m stitching.cli native-validate --duration-sec 1800
```

pass 기준:
- 30분 동안 종료/재시작 없이 지속
- `pts-offset-auto` 또는 `pts-offset-hybrid`가 실제 offset을 안정적으로 유지함
- source 기반 skew 개선이 확인되거나, 실패 시 `fallback-arrival`로 안전하게 동작하고 회귀가 없음
- 결과만 보고 source-limited인지 code-limited인지 구분 가능

investigate 기준:
- source metrics는 수집되지만 pair 개선과 연결되지 않음
- input/source 흔들림이 커서 baseline closure 판단이 애매함

fail 기준:
- runtime가 장시간 안정적으로 유지되지 못함
- strict fresh 30 해석이 불가능할 정도로 metrics가 불안정함

## Operator Notes

- `source_time_valid_left/right=true`는 source timestamp가 들어왔다는 뜻이지, auto offset이 항상 신뢰 가능하다는 뜻은 아니다.
- `source_time_mode=fallback-arrival`이면 현재 pair selection은 arrival 기준이다.
- `left_source_age_ms`, `right_source_age_ms`는 `wallclock` 진단 모드일 때만 적극적으로 본다.
- `sync_offset_source=auto` 또는 `recalibration`이면 motion correlation이 offset을 잡고 있다는 뜻이다.
- `sync_offset_source=manual`이면 operator가 넣은 offset으로 고정한 상태다.
- `source_probe.cross_camera_wallclock_comparable=true`여도 기본 운영 경로는 wallclock을 자동 사용하지 않는다.

## Next Decision

이번 단계가 끝나면 판단은 둘 중 하나다.

- `source-limited`
  - 카메라/network/source cadence가 병목
- `code-limited`
  - reader/pair/stitch path가 병목

이 문서의 목적은 그 둘을 감으로가 아니라 metrics로 구분하게 만드는 것이다.
