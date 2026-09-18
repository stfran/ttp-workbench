########################################
# Dockerfile – builds a self-contained
# TTP-LLM (Ubuntu 22.04 with CUDA)
# Be sure to check CUDA version of host
########################################

FROM docker.io/nvidia/cuda:12.6.0-base-ubuntu22.04

ARG REPO_URL=https://github.com/RezzFayyazi/TTP-LLM.git
ARG COMMIT=7b8ce19aa608769e86ecf7bddb6dc089e023ffa9

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8

# ---------- 1. OS packages ----------
RUN apt-get update && apt-get install -y \
    git build-essential pkg-config wget curl vim unzip \
 && apt-get install -y software-properties-common \
 && add-apt-repository -y ppa:deadsnakes/ppa \
 && apt-get update \
 && apt-get install -y --no-install-recommends \ 
        python3.10 \
        python3.10-venv \
        python3.10-distutils \
 && ln -sf /usr/bin/python3.10 /usr/bin/python \
 && apt-get clean && rm -rf /var/lib/apt/lists/* 

# ---------- 2. Clone & prepare TTP-LLM ----------
RUN mkdir -p /opt/TTP-LLM
WORKDIR /opt/TTP-LLM
RUN git init . \
    && git remote add origin $REPO_URL \
    && git fetch --depth 1 origin $COMMIT \
    && git checkout --detach $COMMIT

WORKDIR /opt/TTP-LLM
COPY requirements_updated.txt /opt/TTP-LLM/
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
 && /opt/venv/bin/pip install torch torchvision torchaudio \
 && /opt/venv/bin/pip install -r requirements_updated.txt 

 # Targeted update to postprocess.py to fix key error
RUN sed -i '42 a\    df.columns = ["result"]' \
  /opt/TTP-LLM/decoder_only/postprocess.py

# Remove the upstream 20-row cap and emit token counts for cost accounting.
# The usage marker never contains prompts, responses, or API credentials.
COPY patch_prompt_only.py /tmp/patch_prompt_only.py
RUN /opt/venv/bin/python /tmp/patch_prompt_only.py \
      /opt/TTP-LLM/decoder_only/prompt_only.py \
 && rm -f /tmp/patch_prompt_only.py
