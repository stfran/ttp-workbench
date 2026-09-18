########################################
# Dockerfile – builds a self-contained
# TTPDrill + CoreNLP runtime (Ubuntu 22.04)
########################################

FROM docker.io/ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHON_VERSION=3.9 \
    CORENLP_VERSION=2018-10-05 \
    CORENLP_HOME=/opt/stanford-corenlp-full-2018-10-05 \
    TZ=Etc/UTC

ARG REPO_URL_ONE=https://github.com/ccsnow127/TTPDrill-1.0.git
ARG ONE_COMMIT=48c99ae855e625ad9b9cdc71e7f6a597db898c99
ARG REPO_URL_OHTHREE=https://github.com/SkyBulk/TTPDrill-0.3.git
ARG OHTHREE_COMMIT=78435268f26e71c966af53321a4a6037a6bb7853

# ---------- 1. OS packages ----------
RUN apt-get update && apt-get install -y \
    git build-essential pkg-config wget curl vim unzip \
 && apt-get install -y software-properties-common \
 && add-apt-repository -y ppa:deadsnakes/ppa \
 && add-apt-repository universe \
 && apt-get update \
 && apt-get install -y --no-install-recommends \ 
        python3.9 \
        python3.9-venv \
        python3.9-distutils \
        openjdk-8-jdk-headless \
 && ln -sf /usr/bin/python3.9 /usr/bin/python \               
 && apt-get clean && rm -rf /var/lib/apt/lists/* 

# ---------- 2. CoreNLP ----------
RUN mkdir -p /opt \
 && curl -L -o /tmp/corenlp.zip \
      https://downloads.cs.stanford.edu/nlp/software/stanford-corenlp-full-${CORENLP_VERSION}.zip \
 && unzip /tmp/corenlp.zip -d /opt \
 && rm /tmp/corenlp.zip
ENV CORENLP_HOME=${CORENLP_HOME}


WORKDIR /opt


# 3a) TTPDrill 1.0 @ ONE_COMMIT
RUN git clone --no-checkout ${REPO_URL_ONE} TTPDrill-1.0 \
&& cd TTPDrill-1.0 \
&& git fetch --depth 1 origin ${ONE_COMMIT} \
&& git checkout --detach ${ONE_COMMIT}


# 3b) TTPDrill 0.3 @ OHTHREE_COMMIT
RUN git clone --no-checkout ${REPO_URL_OHTHREE} TTPDrill-0.3 \
&& cd TTPDrill-0.3 \
&& git fetch --depth 1 origin ${OHTHREE_COMMIT} \
&& git checkout --detach ${OHTHREE_COMMIT}


# 3c) Merge into a single project tree where 1.0 overlays 0.3
RUN mkdir -p /opt/TTPDrill \
&& cp -a /opt/TTPDrill-0.3/. /opt/TTPDrill/ \
&& cp -a /opt/TTPDrill-1.0/. /opt/TTPDrill/ \
&& rm -rf /opt/TTPDrill-0.3/.git /opt/TTPDrill-1.0/.git

# ---------- 4. Patch relation_miner.py ----------
RUN sed -i '25 a\        output = json.loads(output)' /opt/TTPDrill/relation_miner.py

# ---------- 5. Install Requirements ----------
WORKDIR /opt/TTPDrill
COPY requirements.txt /opt/TTPDrill/requirements.txt
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
 && /opt/venv/bin/pip install -r requirements.txt \
 && mkdir -p /opt/nltk_data \
 && /opt/venv/bin/python -m nltk.downloader -d /opt/nltk_data punkt_tab stopwords

ENV NLTK_DATA=/opt/nltk_data

# ---------- 6. Launch script ----------
#   1. starts CoreNLP (background)
#   2. waits until port 9000 is open
#   3. runs TTPDrill main.py
#COPY docker-entrypoint.sh /usr/local/bin/entrypoint.sh
#RUN chmod +x /usr/local/bin/entrypoint.sh

# ----------7. Runner for Evaluation ----
#   1. starts CoreNLP (background)
#   2. waits until port 9000 is open
#   3. runs TTPDrill main.py redirecting stdout to a file that is retrieved by the adapter on the host
#  run_ttp.sh is for one file at a time
#COPY run_ttp.sh /usr/local/bin/run_ttp.sh
#RUN chmod +x /usr/local/bin/run_ttp.sh
# run_ttp_batch.sh starts the core nlp server and then runs all files in the directory
#COPY run_ttp_batch.sh /usr/local/bin/run_ttp_batch.sh
#RUN chmod +x /usr/local/bin/run_ttp_batch.sh

#WORKDIR /opt/TTPDrill
#ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]