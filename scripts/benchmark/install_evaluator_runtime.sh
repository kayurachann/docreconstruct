#!/usr/bin/env bash
set -euo pipefail

runtime_root=${1:?runtime root is required}
requirements=${2:?hashed requirements lock is required}

tex_mirror="https://ftp.tu-chemnitz.de/pub/tug/historic/systems/texlive/2025"
tex_repository="$tex_mirror/tlnet-final"
tools="$runtime_root/tools"
downloads="$runtime_root/downloads"
mkdir -p "$tools/bin" "$downloads"

download_sha256() {
  local url=$1 destination=$2 digest=$3
  curl --fail --location --retry 5 "$url" -o "$destination"
  echo "$digest  $destination" | sha256sum --check
}

download_sha512() {
  local url=$1 destination=$2 digest=$3
  curl --fail --location --retry 5 "$url" -o "$destination"
  echo "$digest  $destination" | sha512sum --check
}

# The evaluator's documented native route requires these build/runtime delegates.
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  build-essential ca-certificates curl libjpeg-dev libpng-dev libtiff-dev \
  libwebp-dev pkg-config xz-utils

if [[ ! -x "$tools/texlive/2025/bin/x86_64-linux/pdflatex" ]]; then
  installer="$downloads/install-tl-unx-2025.tar.gz"
  database="$downloads/texlive-2025-final.tlpdb.xz"
  download_sha256 \
    "$tex_mirror/install-tl-unx.tar.gz" \
    "$installer" \
    "9938f192af75f792e84282580cce6eedac32969e0e07b33cb39ca1b699e948b6"
  download_sha256 \
    "$tex_repository/tlpkg/texlive.tlpdb.xz" \
    "$database" \
    "56df038e9070a3d587eeceb5def3f154b2cacccef36d4295bb49e0618977c34a"
  install_tree="$runtime_root/install-texlive"
  mkdir -p "$install_tree"
  tar -xzf "$installer" -C "$install_tree"
  installer_dir=$(find "$install_tree" -mindepth 1 -maxdepth 1 -type d -name 'install-tl-*' -print -quit)
  if [[ -z "$installer_dir" ]]; then
    echo "Pinned TeX Live installer archive has no install-tl directory" >&2
    exit 1
  fi
  profile="$runtime_root/texlive.profile"
  cat > "$profile" <<EOF
selected_scheme scheme-minimal
TEXDIR $tools/texlive/2025
TEXMFCONFIG $tools/texlive/texmf-config
TEXMFHOME $tools/texlive/texmf-home
TEXMFLOCAL $tools/texlive/texmf-local
TEXMFSYSCONFIG $tools/texlive/2025/texmf-config
TEXMFSYSVAR $tools/texlive/2025/texmf-var
TEXMFVAR $tools/texlive/texmf-var
binary_x86_64-linux 1
instopt_adjustpath 0
tlpdbopt_autobackup 0
tlpdbopt_install_docfiles 0
tlpdbopt_install_srcfiles 0
EOF
  perl "$installer_dir/install-tl" \
    -repository "$tex_repository" \
    -profile "$profile"
fi

tex_bin="$tools/texlive/2025/bin/x86_64-linux"
export PATH="$tex_bin:$tools/bin:$PATH"
"$tex_bin/tlmgr" option repository "$tex_repository"
"$tex_bin/tlmgr" install \
  amsfonts amsmath arphic booktabs cjk cjkutils geometry latex latex-bin \
  multirow was xcolor
test "$("$tex_bin/kpsewhich" CJK.sty)"
test "$("$tex_bin/kpsewhich" c70gkai.fd)"
test "$("$tex_bin/kpsewhich" upgreek.sty)"

if [[ ! -x "$tools/bin/gs" ]]; then
  ghostscript="$downloads/ghostscript-9.55.0-linux-x86_64.tgz"
  download_sha512 \
    "https://github.com/ArtifexSoftware/ghostpdl-downloads/releases/download/gs9550/ghostscript-9.55.0-linux-x86_64.tgz" \
    "$ghostscript" \
    "6c09c9056a311a5a2144ffe651660c2d2d748115c7f910ddeaa882385818d218d19066aed979179415f408ea5b6d14ace12e05f6ec6ab7c21126a3e2cbc0c596"
  ghostscript_tree="$runtime_root/ghostscript"
  mkdir -p "$ghostscript_tree"
  tar -xzf "$ghostscript" -C "$ghostscript_tree"
  ghostscript_bin=$(find "$ghostscript_tree" -type f -name 'gs-*-linux-x86_64' -print -quit)
  if [[ -z "$ghostscript_bin" ]]; then
    echo "Pinned Ghostscript archive has no x86_64 executable" >&2
    exit 1
  fi
  cp "$ghostscript_bin" "$tools/bin/gs"
  chmod +x "$tools/bin/gs"
fi
test "$("$tools/bin/gs" --version)" = "9.55.0"

if [[ ! -x "$tools/imagemagick/bin/magick" ]]; then
  imagemagick="$downloads/ImageMagick-7.1.1-47.tar.gz"
  download_sha256 \
    "https://github.com/ImageMagick/ImageMagick/archive/refs/tags/7.1.1-47.tar.gz" \
    "$imagemagick" \
    "818e21a248986f15a6ba0221ab3ccbaed3d3abee4a6feb4609c6f2432a30d7ed"
  imagemagick_tree="$runtime_root/imagemagick-source"
  mkdir -p "$imagemagick_tree"
  tar -xzf "$imagemagick" -C "$imagemagick_tree"
  source_dir=$(find "$imagemagick_tree" -mindepth 1 -maxdepth 1 -type d -name 'ImageMagick-*' -print -quit)
  if [[ -z "$source_dir" ]]; then
    echo "Pinned ImageMagick archive has no source directory" >&2
    exit 1
  fi
  (
    cd "$source_dir"
    ./configure \
      --prefix="$tools/imagemagick" \
      --disable-dependency-tracking \
      --disable-shared \
      --enable-static \
      --without-perl
    make -j2
    make install
  )
fi
test "$("$tools/imagemagick/bin/magick" -version | head -n 1 | awk '{print $3}')" = "7.1.1-47"

policy_dir="$tools/imagemagick-policy"
mkdir -p "$policy_dir"
cat > "$policy_dir/policy.xml" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policymap>
<policymap>
  <policy domain="coder" rights="read" pattern="PDF" />
</policymap>
EOF

if [[ ! -x "$runtime_root/venv/bin/python" ]]; then
  python -m venv "$runtime_root/venv"
fi
"$runtime_root/venv/bin/python" -m pip install --require-hashes -r "$requirements"
"$runtime_root/venv/bin/python" -m pip check

{
  echo "$tex_bin"
  echo "$tools/bin"
  echo "$tools/imagemagick/bin"
  echo "$runtime_root/venv/bin"
} >> "$GITHUB_PATH"
{
  echo "CDM_TEXLIVE_BIN=$tex_bin"
  echo "MAGICK_CONFIGURE_PATH=$policy_dir"
  echo "SOURCE_BENCHMARK_EVALUATOR_PYTHON=$runtime_root/venv/bin/python"
} >> "$GITHUB_ENV"
