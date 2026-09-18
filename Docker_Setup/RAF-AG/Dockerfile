########################################
# Dockerfile – builds a self-contained
# RAF-AG runtime (Ubuntu 22.04)
########################################

FROM docker.io/ubuntu:22.04

ARG REPO_URL=https://github.com/cyb3rlab/RAF-AG.git
ARG COMMIT=f2868edc1be6a09fc51b1d57907799602ddaa0eb

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHON_VERSION=3.9 \
    TZ=Etc/UTC

# ---------- 1. OS Packages ----------
RUN apt-get update && apt-get install -y \
        git build-essential wget curl vim unzip \
        software-properties-common \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y \
        git build-essential wget curl vim unzip \
        software-properties-common \
        python3.9 python3.9-venv python3.9-distutils \
        python3.9-dev g++ \
    && ln -sf /usr/bin/python3.9 /usr/bin/python \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# ---------- 2. Clone RAF-AG ----------
RUN mkdir /opt/RAF-AG
WORKDIR /opt/RAF-AG
RUN git init . \
    && git remote add origin $REPO_URL \
    && git fetch --depth 1 origin $COMMIT \
    && git checkout --detach $COMMIT

# ---------- 3. Python Virtual Environment ----------
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
ENV TFHUB_CACHE_DIR=/opt/RAF-AG/data/tf_hub

# Base setup
RUN pip install --upgrade pip "setuptools<81" wheel

# Main requirements
# Install PyMuPDF, whose import name is ``fitz``; the original ``fitz`` package is no longer available.
RUN sed -i 's/^fitz==0\.0\.1\.dev2$/pymupdf/' /opt/RAF-AG/requirements.txt \
    && pip install -r /opt/RAF-AG/requirements.txt

# TensorFlow 2.20's CUDA extra expects this package, but RAF-AG's released
# requirements leave it out while installing the other CUDA 12 libraries.
# XLA needs both ptxas and nvvm/libdevice/libdevice.10.bc at inference time.
RUN pip install --no-cache-dir "nvidia-cuda-nvcc-cu12==12.8.93"
ENV PATH="/opt/venv/lib/python3.9/site-packages/nvidia/cuda_nvcc/bin:${PATH}" \
    XLA_FLAGS="--xla_gpu_cuda_data_dir=/opt/venv/lib/python3.9/site-packages/nvidia/cuda_nvcc" \
    PYTHONHASHSEED=0

# Compatibility and bug fixes
RUN pip uninstall -y numpy && pip install "numpy<2.0"
RUN pip install --force-reinstall --no-deps "thinc==8.1.12" "spacy==3.5.0" fire

# spaCy models (large downloads!)
RUN python -m spacy download en_core_web_lg
RUN python -m spacy download en_core_web_trf

# coreferee support
RUN python -m pip install coreferee
RUN python -m coreferee install en

ENV PATH="/opt/venv/bin:${PATH}"

# ---------- 4. Unzip all .zip files under /opt/RAF-AG/data ----------
RUN find /opt/RAF-AG/data -name '*.zip' -exec unzip -o {} -d /opt/RAF-AG/data \;
# RAF-AG imports USE at module load. Materialize it during connected setup so
# both the image and the native checkout extracted from it are inference-ready.
RUN mkdir -p "$TFHUB_CACHE_DIR" \
    && python -c 'import tensorflow_hub as hub; hub.load("https://tfhub.dev/google/universal-sentence-encoder-large/5")'
COPY matcher.py /opt/RAF-AG/matcher.py
COPY patch_determinism.py /opt/RAF-AG/patch_determinism.py
RUN python /opt/RAF-AG/patch_determinism.py /opt/RAF-AG

WORKDIR /opt/RAF-AG

# ---------- 5. Remove demo reports -----------
RUN rm -f /opt/RAF-AG/data/campaign/input/Frankenstein\ Campaign.txt \
          /opt/RAF-AG/data/campaign/input/test_report.txt
