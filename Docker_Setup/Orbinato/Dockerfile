########################################
# Dockerfile – builds a self-contained
# Orbinato Experiments runtime (Ubuntu 22.04)
########################################

FROM docker.io/nvidia/cuda:12.6.0-base-ubuntu22.04

ARG REPO_URL=https://github.com/dessertlab/cti-to-mitre-with-nlp.git
ARG COMMIT=a8cacf3185d098c686e0d88768a619a03a4d76d1
ENV DEBIAN_FRONTEND=noninteractive \
    VIRTUAL_ENV=/opt/venv \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility


# Install OS dependencies and Python 3.10
RUN apt-get update && apt-get install -y --no-install-recommends \
      git git-lfs curl wget unzip ca-certificates \
      python3.10 python3.10-venv python3.10-dev python3-pip \
      build-essential pkg-config \
    && rm -rf /var/lib/apt/lists/* \
    && git lfs install --skip-repo

# Clone and pull LFS content
RUN mkdir /opt/Orbinato
WORKDIR /opt/Orbinato
RUN git init . \
    && git remote add origin $REPO_URL \
    && git fetch --depth 1 origin $COMMIT \
    && git checkout --detach $COMMIT \
    && git lfs pull

RUN python3.10 -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
 && pip install --no-cache-dir -r requirements.txt \
 && python -m nltk.downloader punkt wordnet omw-1.4 stopwords
RUN pip install --no-cache-dir "transformers<5" torch torchvision

# setup_orbinato.sh downloads both configured archives into the ignored build
ARG ORBINATO_ADDITIONAL_FILES_SHA256="ac7f07d0a1806b93c0be4f2d0dacab6e9222d57e8407a1ee11f39872edb5f996"
COPY additional_files.zip /tmp/additional_files.zip
RUN echo "${ORBINATO_ADDITIONAL_FILES_SHA256}  /tmp/additional_files.zip" | sha256sum -c -

RUN unzip /tmp/additional_files.zip -d /opt/Orbinato \
 && rm -f /tmp/additional_files.zip

ARG ORBINATO_MODELS_SHA256="eb63b59b1d4ab9150e196795f70b3ddc5bf4d2768466ebb5d1e9cd97bd2dc9c8"
COPY orbinato_models.zip /tmp/orbinato_models.zip
RUN echo "${ORBINATO_MODELS_SHA256}  /tmp/orbinato_models.zip" | sha256sum -c - \
 && mkdir -p /tmp/orbinato_models \
 && unzip -q /tmp/orbinato_models.zip -d /tmp/orbinato_models \
 && cp -a /tmp/orbinato_models/orbinato/src/. /opt/Orbinato/src/ \
 && ln -sfn secbert_model/trained_secbert.pt /opt/Orbinato/src/trained_secbert.pt \
 && ln -sfn secbert_model/secbert_labels.txt /opt/Orbinato/src/secbert_labels.txt \
 && rm -rf /tmp/orbinato_models /tmp/orbinato_models.zip \
 && test -s /opt/Orbinato/src/trained_secbert.pt \
 && test -s /opt/Orbinato/src/secbert_labels.txt \
 && test -s /opt/Orbinato/src/lstm_model/saved_model.sav \
 && test -s /opt/Orbinato/src/pretrained-lstm_model/saved_model.sav \
 && test -s /opt/Orbinato/src/cnn_model/saved_model.sav



# Make a import-safe document_analysis copy (strip bottom run block)
WORKDIR /opt/Orbinato/src
RUN cp document_analysis.py document_analysis_import.py
RUN sed -i '/^from document_data import \*/,$d' document_analysis_import.py

# Copy CLI 
COPY orbinato_cli.py /opt/Orbinato/src
COPY secbert_train.py /opt/Orbinato/src

# Use supplied trained models; this image intentionally has no training fallback.
WORKDIR /opt/Orbinato/src
