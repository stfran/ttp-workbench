########################################
# Dockerfile for LADDER setup
# Based on Ubuntu 22.04 with CUDA 12.6 base image
########################################

FROM docker.io/nvidia/cuda:12.6.0-base-ubuntu22.04

ARG REPO_URL=https://github.com/aiforsec/LADDER.git
ARG COMMIT=863ec65859700ac13b1308fbe48ad34f4078eb39

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8

#----------1. Setup environment -------------------
RUN apt-get update && apt-get install -y \
    git build-essential pkg-config vim wget \
    software-properties-common \
  && add-apt-repository -y ppa:deadsnakes/ppa \
  && apt-get update && apt-get install -y --no-install-recommends \
    python3.10 python3.10-venv python3.10-distutils python3-pip \
  && python3.10 -m pip install --no-cache-dir -U pip \
  && python3.10 -m pip install --no-cache-dir gdown \
  && ln -sf /usr/bin/python3.10 /usr/bin/python \
  && apt-get clean && rm -rf /var/lib/apt/lists/*

# ----------2. Clone repository at commit--------------
WORKDIR /opt/LADDER
RUN git init . \
    && git remote add origin $REPO_URL \
    && git fetch --depth 1 origin $COMMIT \
    && git checkout --detach $COMMIT

# ----------3. Setup python virtual environment--------------
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip setuptools wheel
RUN cp /opt/LADDER/attack_pattern/requirements.txt /opt/LADDER/attack_pattern/requirements.txt.bak
COPY requirements_updated.txt /opt/LADDER/attack_pattern/requirements.txt
RUN /opt/venv/bin/pip install -r /opt/LADDER/attack_pattern/requirements.txt \
    && /opt/venv/bin/python -m nltk.downloader punkt_tab averaged_perceptron_tagger_eng

# ----------4. Download models ---------- #
WORKDIR /opt/LADDER/attack_pattern
RUN mkdir -p models
RUN gdown https://drive.google.com/uc?id=1yYRNoV4SFwcS1HAgrwxNQftqQVnaXLGo -O models/
RUN gdown https://drive.google.com/uc?id=15YJgo4iqfQ7zWoHLBOnHOW4BV3hsGENY -O models/

# ---------- 5. Targeted fixes ---------- #
# In attack_pattern/models.py, replace the modeling_bert and roberta imports (lines 3 and 4) to transformers
RUN cp /opt/LADDER/attack_pattern/models.py /opt/LADDER/attack_pattern/models.py.bak
RUN sed -i '3,4c\
from transformers import (\
    BertModel, BertPreTrainedModel,\
    RobertaModel, RobertaConfig\
)' models.py
# comment out the import to spacy and torchtext in attack_pattern/dataset.py (lines 2 and 3), it doesn't actually use it
RUN cp /opt/LADDER/attack_pattern/dataset.py /opt/LADDER/attack_pattern/dataset.py.bak
RUN sed -i '2,3c\# import spacy\n# from torchtext.vocab import vocab' /opt/LADDER/attack_pattern/dataset.py
# replace inference.py with inference_updated.py, which changes the model loading to avoid strict loading errors
# Patch inference.py 
# Changes:
#   - unwrap checkpoints that contain a "state_dict" key;
#   - remove transformer position_id buffers that are version-sensitive;
#   - load the expected model weights strictly after removing version buffers;
#   - print lightweight diagnostics for reproducibility.
RUN cp /opt/LADDER/attack_pattern/inference.py /opt/LADDER/attack_pattern/inference.py.bak \
 && /opt/venv/bin/python - <<'PY'
from pathlib import Path

path = Path("/opt/LADDER/attack_pattern/inference.py")
s = path.read_text(encoding="utf-8")

old_entity = """    entity_model = EntityRecognition(args.entity_extraction_model).to(device)
    entity_model.load_state_dict(torch.load(args.entity_extraction_weight, map_location=device))
"""

new_entity = """    entity_model = EntityRecognition(args.entity_extraction_model).to(device)

    sd = torch.load(args.entity_extraction_weight, map_location=device)
    if isinstance(sd, dict) and "state_dict" in sd:
        sd = sd["state_dict"]
    sd.pop("bert_layer.embeddings.position_ids", None)

    missing, unexpected = entity_model.load_state_dict(sd, strict=True)
    print("missing:", missing)
    print("unexpected:", unexpected)
    print("Has LSTM weights:", "lstm.weight_ih_l0" in sd, "lstm.weight_hh_l0" in sd)
    print("Has Linear weights:", "linear.weight" in sd, "linear.bias" in sd)
"""

if old_entity not in s:
    raise SystemExit("Could not find expected entity_model.load_state_dict block in inference.py")
s = s.replace(old_entity, new_entity, 1)

old_sentence_roberta = """    elif MODELS[args.sentence_classification_model][3] == 'roberta':
        sentence_model = SentenceClassificationRoBERTa(args.sentence_classification_model, num_class=2).to(device)
        sentence_model.load_state_dict(torch.load(args.sentence_classification_weight, map_location=device))
"""

new_sentence_roberta = """    elif MODELS[args.sentence_classification_model][3] == 'roberta':
        sentence_model = SentenceClassificationRoBERTa(args.sentence_classification_model, num_class=2).to(device)

        sd = torch.load(args.sentence_classification_weight, map_location=device)
        if isinstance(sd, dict) and "state_dict" in sd:
            sd = sd["state_dict"]
        sd.pop("bert.roberta.embeddings.position_ids", None)

        missing, unexpected = sentence_model.load_state_dict(sd, strict=True)
        print("sentence missing:", missing)
        print("sentence unexpected:", unexpected)
"""

if old_sentence_roberta not in s:
    raise SystemExit("Could not find expected roberta sentence_model.load_state_dict block in inference.py")
s = s.replace(old_sentence_roberta, new_sentence_roberta, 1)

path.write_text(s, encoding="utf-8")
PY


# download the huggingface models manually as the code relies on an older transformers library version (different than LADDER's setup instructions)
#RUN mkdir -p roberta-large && \
#  wget -q -O roberta-large/config.json https://huggingface.co/FacebookAI/roberta-large/resolve/main/config.json && \
#  wget -q -O roberta-large/pytorch_model.bin https://huggingface.co/FacebookAI/roberta-large/resolve/main/pytorch_model.bin && \
#  wget -q -O roberta-large/vocab.json https://huggingface.co/FacebookAI/roberta-large/resolve/main/vocab.json && \
#  wget -q -O roberta-large/merges.txt https://huggingface.co/FacebookAI/roberta-large/resolve/main/merges.txt && \
#  wget -q -O roberta-large/tokenizer_config.json https://huggingface.co/FacebookAI/roberta-large/resolve/main/tokenizer_config.json

# Build the CLI in the container from the attack_pattern demonstraction code in LADDER/notebooks/attack-pattern-extraction.ipynb
COPY build_ladder_cli_from_notebook.py /tmp/build_ladder_cli_from_notebook.py

RUN /opt/venv/bin/python /tmp/build_ladder_cli_from_notebook.py 

# Run the test case to verify setup, this will also cache the models in the venv
RUN /opt/venv/bin/python /opt/LADDER/attack_pattern/ladder_attack_pattern_cli.py --test
