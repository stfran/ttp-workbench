########################################
# Dockerfile – builds a self-contained
# rcATT runtime (Ubuntu 22.04 + Python 3.7)
########################################

FROM ubuntu:22.04

ARG REPO_URL=https://github.com/vlegoy/rcATT.git
ARG COMMIT=f82f7fd456279abefcd3e0b50e8056345c11aeb7

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHON_VERSION=3.7 \
    TZ=Etc/UTC \
    LANG=C.UTF-8 LC_ALL=C.UTF-8
 
# ---------- 1. OS packages ----------
RUN apt-get update && apt-get install -y \
    git build-essential pkg-config wget curl vim unzip \
 && apt-get install -y software-properties-common \
 && add-apt-repository -y ppa:deadsnakes/ppa \
 && apt-get update \
 && apt-get install -y --no-install-recommends \ 
        python3.7 \
        python3.7-venv \
        python3.7-distutils \
 && ln -sf /usr/bin/python3.7 /usr/bin/python \               
 && apt-get clean && rm -rf /var/lib/apt/lists/* 

# ---------- 2. Clone rcATT ---------- #
RUN mkdir /opt/rcATT
WORKDIR /opt/rcATT
RUN git init . \
    && git remote add origin $REPO_URL \
    && git fetch --depth 1 origin $COMMIT \
    && git checkout --detach $COMMIT

# ---------- 3. Install Requirements  ---------- #
WORKDIR /opt/rcATT
COPY requirements.txt /opt/rcATT/requirements.txt
RUN python -m venv /opt/venv             
RUN /opt/venv/bin/pip install --upgrade pip setuptools wheel 
RUN /opt/venv/bin/pip install -r /opt/rcATT/requirements.txt 
RUN mkdir -p /opt/nltk_data 
RUN /opt/venv/bin/python -m nltk.downloader -d /opt/nltk_data \
        punkt stopwords wordnet
ENV NLTK_DATA=/opt/nltk_data

# ---------- 4. Patch save_results.py ----------#
# deal with empty case in stix objects. 
RUN /opt/venv/bin/python - <<'PY'
p = "/opt/rcATT/classification_tools/save_results.py"
s = open(p, "r", encoding="utf-8").read()
s = s.replace(
    "object_refs = ttps)",
    'object_refs = (ttps if ttps else ["marking-definition--fa42a846-8d90-4e51-bc29-71d5b4802168"]), allow_custom=True)'
)
open(p, "w", encoding="utf-8").write(s)
print("Patched save_results.py")
PY

# Optional bulk mode reuses models while retaining upstream per-report logic.
COPY patch_bulk.py rcatt_bulk.py /opt/rcATT/
RUN /opt/venv/bin/python patch_bulk.py /opt/rcATT
