########################################
# Dockerfile – builds a self-contained
# TRAM-like container (Ubuntu 22.04 with CUDA)
# Be sure to check CUDA version of host
########################################

FROM docker.io/nvidia/cuda:12.6.0-base-ubuntu22.04

ARG REPO_URL=https://github.com/center-for-threat-informed-defense/tram.git
ARG COMMIT=f29793d8d665f7f552898696e00065ef24a29a20

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8

#----------1. Setup environment -------------------
RUN apt-get update && apt-get install -y \
    git build-essential pkg-config wget curl vim unzip \
 && apt-get update \
 && apt-get install -y software-properties-common \
 && add-apt-repository -y ppa:deadsnakes/ppa \ 
 && apt-get update \
 && apt-get install -y --no-install-recommends python3.11 python3.11-venv python3.11-distutils \
 && ln -sf /usr/bin/python3.11 /usr/bin/python \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

#-----------2. Setup python virtual environment--------------
RUN mkdir /opt/TRAM
WORKDIR /opt/TRAM
COPY requirements.txt /tmp/tram-requirements.txt
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
 && /opt/venv/bin/pip install -r /tmp/tram-requirements.txt

#----------3. Setup project directory -----------------------
# we don't actually need any of this code, but let's download it for posterity
WORKDIR /opt/TRAM
RUN git init . \
    && git remote add origin $REPO_URL \
    && git fetch --depth 1 origin $COMMIT \
    && git checkout --detach $COMMIT
    
WORKDIR /opt/TRAM
COPY predict_multi_label.py /opt/TRAM/predict_multi_label.py
RUN mkdir /opt/TRAM/scibert_multi_label_model
RUN wget https://ctidtram.blob.core.windows.net/tram-models/multi-label-20230803/config.json -O /opt/TRAM/scibert_multi_label_model/config.json
RUN wget https://ctidtram.blob.core.windows.net/tram-models/multi-label-20230803/pytorch_model.bin -O /opt/TRAM/scibert_multi_label_model/pytorch_model.bin

# ---------- 4. Entrypoint ---------- #
#COPY docker-entrypoint.sh /usr/local/bin/entrypoint.sh
#RUN chmod +x /usr/local/bin/entrypoint.sh
#ENTRYPOINT ["bash", "/usr/local/bin/entrypoint.sh"]
