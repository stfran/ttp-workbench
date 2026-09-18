########################################
# Dockerfile for SeqMask setup
# Based on Ubuntu 22.04 with CUDA 12.6 base image
########################################

FROM docker.io/nvidia/cuda:12.6.0-base-ubuntu22.04

ARG REPO_URL=https://github.com/MuscleFish/SeqMask.git
ARG COMMIT=f3686599065a58927562fbb5c4bf71c075a687ba

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8

# ----------1. Setup environment -------------------
RUN apt-get update && apt-get install -y \
    git build-essential pkg-config vim unzip p7zip-full \
    curl ca-certificates \
    software-properties-common \
  && add-apt-repository -y ppa:deadsnakes/ppa \
  && apt-get update && apt-get install -y --no-install-recommends \
    python3.9 python3.9-venv python3.9-distutils python3.9-dev python3-pip \
  && python3.9 -m pip install --no-cache-dir -U pip \
  && python3.9 -m pip install --no-cache-dir gdown \
  && ln -sf /usr/bin/python3.9 /usr/bin/python \
  && apt-get clean && rm -rf /var/lib/apt/lists/*

# ----------2. Clone repository at commit--------------
WORKDIR /opt/SeqMask
RUN git init . \
    && git remote add origin $REPO_URL \
    && git fetch --depth 1 origin $COMMIT \
    && git checkout --detach $COMMIT

# ----------3. Copy downloaded model artifacts and normalize layout ----------
# setup_seqmask.sh downloads the configured archive into the ignored build
# context so credentials never enter the image build.
ARG SEQMASK_ARTIFACT_SHA256="3bde481b5e32ee955d838f18b900c41ebc9fefdae658c49d30a1d750939d0132"
COPY seqmask_artifacts.zip /tmp/seqmask_artifacts.zip
RUN set -eux; \
    mkdir -p /tmp/seqmask_artifacts; \
    echo "${SEQMASK_ARTIFACT_SHA256}  /tmp/seqmask_artifacts.zip" | sha256sum -c -; \
    unzip -q /tmp/seqmask_artifacts.zip -d /tmp/seqmask_artifacts; \
    rm -f /tmp/seqmask_artifacts.zip

RUN set -eux; \
    cd /opt/SeqMask; \
    \
    # Copy all provided artifacts into the repository tree. This includes
    # split zip parts such as *.z01, *.z02, etc. next to their corresponding *.zip.
    cp -a /tmp/seqmask_artifacts/. /opt/SeqMask/; \
    \
    # Extract nested zip/7z archives using 7z. We intentionally extract only
    # the main archive files (*.zip, *.7z), not split parts (*.z01, *.z02, ...).
    find /opt/SeqMask -maxdepth 3 -type f \( -iname '*.zip' -o -iname '*.7z' \) -print | sort | while read -r archive; do \
        dest="$(dirname "$archive")"; \
        echo "[INFO] Extracting $archive -> $dest"; \
        7z x -y "$archive" "-o$dest"; \
        rm -f "$archive"; \
    done; \
    \
    # Remove split-volume files after extraction.
    find /opt/SeqMask -maxdepth 3 -type f \( \
        -iname '*.z[0-9][0-9]' -o \
        -iname '*.zip.[0-9][0-9][0-9]' \
    \) -print -delete; \
    \
    # SeqMask's DataDeal.py loads this exact relative path:
    #   ./FastTextModels/fastText_20210126.model
    # Normalize the downloaded/extracted artifact into that path.
    mkdir -p /opt/SeqMask/FastTextModels; \
    found="$(find /opt/SeqMask -type f -name 'fastText_20210126.model' | head -n 1)"; \
    if [ -z "$found" ]; then \
        echo "[ERROR] fastText_20210126.model not found under /opt/SeqMask"; \
        echo "[INFO] Model-like files found:"; \
        find /opt/SeqMask -maxdepth 6 -type f \( -iname '*fasttext*' -o -iname '*.model' -o -iname '*.pkl' -o -iname '*.sav' \) -print; \
        exit 1; \
    fi; \
    if [ "$found" != "/opt/SeqMask/FastTextModels/fastText_20210126.model" ]; then \
        cp "$found" /opt/SeqMask/FastTextModels/fastText_20210126.model; \
    fi; \
    test -s /opt/SeqMask/FastTextModels/fastText_20210126.model; \
    \
    rm -rf /tmp/seqmask_artifacts

# ----------4. Setup python virtual environment--------------
RUN python -m venv /opt/venv

RUN /opt/venv/bin/pip install --no-cache-dir \
    "numpy<2" \
    "scipy<1.11" \
    "joblib==1.3.2" \
    "nltk==3.6.7" \
    "Cython<3"

RUN /opt/venv/bin/pip install --no-cache-dir --no-build-isolation "gensim==3.8.3"

COPY requirements.txt /opt/SeqMask/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /opt/SeqMask/requirements.txt

WORKDIR /opt/SeqMask
RUN /opt/venv/bin/python -m nltk.downloader punkt_tab wordnet punkt omw-1.4

# ----------5. Verify setup with upstream test.py ----------
# This is intentionally blocking: if required model artifacts are missing,
# the image should not silently build.
RUN set -eux; \
    cd /opt/SeqMask; \
    ls -lh /opt/SeqMask/FastTextModels/fastText_20210126.model; \
    /opt/venv/bin/python test.py

# ----------6. Copy the CLI ----------
COPY seqmask_cli.py /opt/SeqMask/seqmask_cli.py
