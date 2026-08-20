#!/usr/bin/env bash
set -euo pipefail

system=${1:?system is required}
report=${2:?pip install report path is required}

python -m pip install --upgrade "pip==25.2"

case "$system" in
  docreconstruct-tesseract)
    sudo apt-get update
    apt_version="5.3.4-1build5"
    candidate=$(apt-cache policy tesseract-ocr | awk '/Candidate:/ {print $2}')
    if [[ "$candidate" != "$apt_version" ]]; then
      echo "Expected tesseract-ocr $apt_version, repository offers $candidate" >&2
      exit 1
    fi
    sudo apt-get install -y --no-install-recommends "tesseract-ocr=$apt_version"
    tess_root="$RUNNER_TEMP/tessdata-fast"
    mkdir -p "$tess_root/configs"
    base="https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/87416418657359cb625c412a48b6e1d6d41c29bd"
    curl --fail --location --retry 5 "$base/eng.traineddata" -o "$tess_root/eng.traineddata"
    curl --fail --location --retry 5 "$base/chi_sim.traineddata" -o "$tess_root/chi_sim.traineddata"
    curl --fail --location --retry 5 "$base/chi_tra.traineddata" -o "$tess_root/chi_tra.traineddata"
    curl --fail --location --retry 5 "$base/configs/tsv" -o "$tess_root/configs/tsv"
    (
      cd "$tess_root"
      sha256sum --check <<'EOF'
7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2  eng.traineddata
a5fcb6f0db1e1d6d8522f39db4e848f05984669172e584e8d76b6b3141e1f730  chi_sim.traineddata
529c5b5797d64b126065cd55f2bb4c7fd7b15790798091b1ff259941a829330b  chi_tra.traineddata
59d079bb75d8b3d7c839a3564580cb559e362c93a9d70f234e421c0c3e767e04  configs/tsv
EOF
    )
    python -m pip install --report "$report" -e ".[pdf]"
    echo "BENCHMARK_TESSDATA=$tess_root" >> "$GITHUB_ENV"
    ;;
  docling)
    python -m pip install --report "$report" -e ".[pdf]" "docling==2.120.3"
    ;;
  mineru)
    python -m pip install --report "$report" -e ".[pdf]" "mineru==3.4.5" "six==1.17.0"
    ;;
  marker)
    python -m pip install --report "$report" -e ".[pdf]" "marker-pdf==2.0.0"
    llama_archive="$RUNNER_TEMP/llama-b10507-bin-ubuntu-x64.tar.gz"
    llama_root="$RUNNER_TEMP/llama-b10507"
    curl --fail --location --retry 5 \
      "https://github.com/ggml-org/llama.cpp/releases/download/b10507/llama-b10507-bin-ubuntu-x64.tar.gz" \
      -o "$llama_archive"
    echo "f7035274bb6a50c7eda05e21929ab9dbd5fdc1cc37915a9b510c34951491f3ae  $llama_archive" \
      | sha256sum --check
    mkdir -p "$llama_root"
    tar -xzf "$llama_archive" -C "$llama_root"
    llama_server=$(find "$llama_root" -type f -name llama-server -print -quit)
    if [[ -z "$llama_server" ]]; then
      echo "Pinned llama.cpp archive contains no llama-server" >&2
      exit 1
    fi
    chmod +x "$llama_server"
    dirname "$llama_server" >> "$GITHUB_PATH"
    echo "LLAMA_CPP_BINARY=$llama_server" >> "$GITHUB_ENV"
    ;;
  *)
    echo "Unknown system: $system" >&2
    exit 2
    ;;
esac
