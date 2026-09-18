########################################
# AttacKG runtime (Ubuntu 22.04 + Python 3.8)
########################################

FROM docker.io/ubuntu:22.04

ARG REPO_URL=https://github.com/li-zhenyuan/Knowledge-enhanced-Attack-Graph.git
ARG COMMIT=9120ebea25383bfca1254d2b3088266b3680e47b

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHON_VERSION=3.8 \
    TZ=Etc/UTC \
    LANG=C.UTF-8 LC_ALL=C.UTF-8

# ---------- 1. OS packages ----------
RUN apt-get update && apt-get install -y \
    git build-essential pkg-config wget curl vim unzip \
 && apt-get install -y software-properties-common \
 && add-apt-repository -y ppa:deadsnakes/ppa \
 && apt-get update \
 && apt-get install -y --no-install-recommends \ 
        python3.8 \
        python3.8-venv \
        python3.8-distutils \
 && ln -sf /usr/bin/python3.8 /usr/bin/python \               
 && apt-get clean && rm -rf /var/lib/apt/lists/* 

# ---------- 2. Clone AttacKG from frozen rep ---------- #
RUN mkdir /opt/AttacKG
WORKDIR /opt/AttacKG
#RUN git clone https://github.com/li-zhenyuan/Knowledge-enhanced-Attack-Graph.git .
RUN git init . \
    && git remote add origin $REPO_URL \
    && git fetch --depth 1 origin $COMMIT \
    && git checkout --detach $COMMIT

# ---------- 3. Install Requirements  ---------- #
RUN python -m venv /opt/venv             
RUN /opt/venv/bin/pip install --upgrade pip setuptools wheel 
RUN /opt/venv/bin/pip install -r /opt/AttacKG/requirements.txt


# after your venv+pips
RUN /opt/venv/bin/pip install gdown

# 4) Download both files from the shared folder 
# Folder: https://drive.google.com/drive/folders/1zVGPpN-i-BLlpFqQERscFGb45PkhfkUm
RUN set -eux; \
    mkdir -p /tmp/gdfiles; \
    /opt/venv/bin/gdown --folder --id 1zVGPpN-i-BLlpFqQERscFGb45PkhfkUm -O /tmp/gdfiles; \
    ls -l /tmp/gdfiles; \
    # sanity: ensure expected files are present
    test -f /tmp/gdfiles/templates.zip; \
    test -f /tmp/gdfiles/new_cti.model.zip; \
    # unpack templates
    mkdir -p /opt/AttacKG/templates /opt/AttacKG/templates_tmp; \
    unzip -q /tmp/gdfiles/templates.zip -d /opt/AttacKG/templates_tmp; \
    if [ -d /opt/AttacKG/templates_tmp/templates ]; then \
        mv /opt/AttacKG/templates_tmp/templates/* /opt/AttacKG/templates/ ; \
    else \
        mv /opt/AttacKG/templates_tmp/* /opt/AttacKG/templates/ ; \
    fi; \
    rm -rf /opt/AttacKG/templates_tmp; \
    # unpack model
    mkdir -p /opt/AttacKG/new_cti.model /opt/AttacKG/new_cti.model_tmp; \
    unzip -q /tmp/gdfiles/new_cti.model.zip -d /opt/AttacKG/new_cti.model_tmp; \
    if [ -d /opt/AttacKG/new_cti.model_tmp/new_cti.model ]; then \
        mv /opt/AttacKG/new_cti.model_tmp/new_cti.model/* /opt/AttacKG/new_cti.model/ ; \
    else \
        mv /opt/AttacKG/new_cti.model_tmp/* /opt/AttacKG/new_cti.model/ ; \
    fi; \
    rm -rf /opt/AttacKG/new_cti.model_tmp /tmp/gdfiles

# ---------- 5. Add maintained optimizations after upstream setup ---------- #
# The bulk patch permits callers to inject already-loaded model resources. The
# path cache reuses exact queries against the immutable per-report graph. Calls
# through the original CLI retain their thresholds and execution path.
COPY patch_bulk.py /tmp/patch_attackg_bulk.py
COPY patch_shortest_path_cache.py /tmp/patch_attackg_shortest_path_cache.py
COPY attackg_bulk.py /opt/AttacKG/attackg_bulk.py
RUN python /tmp/patch_attackg_bulk.py /opt/AttacKG \
    && python /tmp/patch_attackg_shortest_path_cache.py /opt/AttacKG \
    && rm -f /tmp/patch_attackg_bulk.py /tmp/patch_attackg_shortest_path_cache.py
