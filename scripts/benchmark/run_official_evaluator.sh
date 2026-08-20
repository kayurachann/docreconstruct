#!/usr/bin/env bash
set -euo pipefail

evaluator=${1:?evaluator checkout is required}
ground_truth=${2:?ground truth JSON is required}
predictions=${3:?prediction directory is required}
run_root=${4:?result root is required}
system=${5:?system name is required}

if [[ ! "$system" =~ ^[A-Za-z0-9_.-]+$ ]]; then
  echo "Unsafe system name: $system" >&2
  exit 2
fi

mkdir -p "$run_root"
config="$run_root/end2end.yaml"
cat > "$config" <<EOF
end2end_eval:
  metrics:
    text_block:
      metric: [Edit_dist]
    display_formula:
      metric: [Edit_dist, CDM]
      cdm_workers: 1
    table:
      metric: [TEDS, Edit_dist]
      teds_workers: 1
    reading_order:
      metric: [Edit_dist]
  dataset:
    dataset_name: end2end_dataset
    ground_truth:
      data_path: $ground_truth
    prediction:
      data_path: $predictions
    match_method: quick_match
    match_workers: 1
    quick_match_truncated_timeout_sec: 300
    match_timeout_sec: 420
    timeout_fallback_max_chunk_span: 10
    timeout_fallback_order_penalty: 0.10
EOF

set +e
(
  cd "$run_root"
  PYTHONPATH="$evaluator" timeout --signal=KILL 18000s \
    "${SOURCE_BENCHMARK_EVALUATOR_PYTHON:?native evaluator Python is required}" \
    "$evaluator/pdf_validation.py" --config "$config"
) > "$run_root/evaluator.stdout.log" 2> "$run_root/evaluator.stderr.log"
status=$?
set -e
printf '%s\n' "$status" > "$run_root/evaluator-exit-code.txt"
exit "$status"
