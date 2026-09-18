import os
from TextClassify import TextDeal
import argparse
from tqdm import tqdm
import json
from nltk.tokenize import sent_tokenize

# SeqMask authors evaluate multiple model families; saved models are in ./models/.
# The released model directory names do not state the n-gram setting.
# Tables 6 (tactics) and 8 (techniques) report the *best-performing* n-gram settings per family;
# we assume the released checkpoints correspond to those best configs.
TACT_MODELS = {
  "self_attention": "./models/tactics_self_attention_model",  # best SelfATT uses n-grams 1–4 in Table 6
  "sv_mask": "./models/tactics_sv_mask_model",                # best SV_Mask uses n-grams 1,2 in Table 6
  "mp_mask": "./models/tactics_mp_mask_model",                # best MP_Mask uses n-gram 1 in Table 6
  "ar_mask": "./models/tactics_ar_mask_model",                # best AR_Mask uses n-grams 1–3 in Table 6
}

TECH_MODELS = {
  "self_attention": "./models/techniques_self_attention_model",  # best SelfATT uses n-grams 1,2 in Table 8
  "sv_mask": "./models/techniques_sv_mask_model",                # best SV_Mask uses n-gram 1 in Table 8
  "mp_mask": "./models/techniques_mp_mask_model",                # best MP_Mask uses n-gram 1 in Table 8
  "ar_mask": "./models/techniques_ar_mask_model",                # best AR_Mask uses n-grams 1,2 in Table 8
}


def parse_args():
    parser = argparse.ArgumentParser(description="SeqMask CLI for text classification and keyword extraction. Follows the example laid out in SeqMask/test.py")
    parser.add_argument('--input_file', type=str, default=None,
                        help="Path to the input text file")
    parser.add_argument('--output_file', type=str, default=None,
                        help="Path to the output results file")
    parser.add_argument('--input_dir', type=str, default=None,
                        help="Path to the input directory containing text files")
    parser.add_argument('--output_dir', type=str, default=None,
                        help="Path to the output directory for results")
    parser.add_argument("--mode", choices=["reproduction", "workbench"], default="workbench",
                   help="reproduction: pass whole text as-is; workbench: sentence tokenize + top-k/tau acceptance")
    parser.add_argument("--tact_model", choices=TACT_MODELS.keys(), default="ar_mask", # this is default in TextClassify.py
                        help="Tactics model to use")
    parser.add_argument("--tech_model", choices=TECH_MODELS.keys(), default="ar_mask", # this is default in TextClassify.py
                        help="Techniques model to use")
    # Workbench mode parameters
    # The SeqMask paper and repository does not specify how to accept predictions, so we assess top-k and tau thresholds
    parser.add_argument('--top_k', type=int, default=3,
                        help="workbench mode: accept among top-k predictions per sentence")
    parser.add_argument('--tau', type=float, default=0.5,
                        help="workbench mode: minimum probability threshold to accept a prediction per sentence")

    return parser.parse_args()

def aggregate_max(dst: dict, src: dict):
    for k, v in (src or {}).items():
        try:
            fv = float(v)
        except Exception:
            continue
        if k not in dst or fv > dst[k]:
            dst[k] = fv

def accept_topk_tau(scores: dict, top_k: int, tau: float):
    if not scores:
        return []
    items = sorted(((k, float(v)) for k, v in scores.items()), key=lambda kv: kv[1], reverse=True)
    top = items[:max(0, top_k)]
    return [k for k, v in top if v >= tau]

def classify_reproduction(text_deal: TextDeal, text: str) -> dict:
    """
    do not sentence tokenize.
    Pass full text as-is into SeqMask
    """
    try:
        res = text_deal.classify_text(text)
    except Exception as e:
        raise RuntimeError(f"Error during classification: {type(e).__name__}: {e}\nDid you download and copy the models into the container (See README)")
    res.pop("embedding", None) # embedding will be large and meaningless in our analysis so we remove it
    return {
        "mode": "reproduction",
        "text_len": len(text),
        "total_tactics": res.get("total_tactics", {}) or {},
        "total_techniques": res.get("total_techniques", {}) or {},
    }


def classify_workbench(text_deal: TextDeal, text: str, top_k: int, tau: float) -> dict:
    """
    sentence tokenize and accept labels per sentence using top-k + tau,
    then aggregate to report level via max pooling.
    """

    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    sentences = [s.strip() for s in sent_tokenize(cleaned, language="english") if s.strip()]

    total_tactics: dict = {}
    total_techniques: dict = {}

    accepted_tactics = set()
    accepted_techniques = set()

    per_sentence = []

    for s in sentences:
        try:
            res = text_deal.classify_text(s)
        except Exception as e:
            per_sentence.append({
                "text": s,
                "error": f"{type(e).__name__}: {e}",
                "total_tactics": {},
                "total_techniques": {},
                "accepted_tactics": [],
                "accepted_techniques": [],
            })
            continue
        
        res.pop("embedding", None)

        st = res.get("total_tactics", {}) or {}
        se = res.get("total_techniques", {}) or {}

        aggregate_max(total_tactics, st)
        aggregate_max(total_techniques, se)

        sent_acc_ta = accept_topk_tau(st, top_k=top_k, tau=tau)
        sent_acc_te = accept_topk_tau(se, top_k=top_k, tau=tau)

        accepted_tactics.update(sent_acc_ta)
        accepted_techniques.update(sent_acc_te)

        per_sentence.append({
            "text": s,
            "total_tactics": st,
            "total_techniques": se,
            "accepted_tactics": sent_acc_ta,
            "accepted_techniques": sent_acc_te,
        })

    return {
        "mode": "workbench",
        "top_k": top_k,
        "tau": tau,
        "num_sentences": len(sentences),
        "total_tactics": total_tactics,
        "total_techniques": total_techniques,
        "accepted_tactics": sorted(accepted_tactics),
        "accepted_techniques": sorted(accepted_techniques),
        "per_sentence": per_sentence,
    }


def classify_one(text_deal: TextDeal, text: str, args) -> dict:
    if args.mode == "reproduction":
        return classify_reproduction(text_deal, text)
    else:
        return classify_workbench(text_deal, text, top_k=args.top_k, tau=args.tau)

def main():
    args = parse_args()
    
    text_deal = TextDeal(tact_model_path=TACT_MODELS[args.tact_model],
                         tech_model_path=TECH_MODELS[args.tech_model])
    
    if args.input_dir and args.output_dir:
        import os
        os.makedirs(args.output_dir, exist_ok=True)
        input_files = [f for f in os.listdir(args.input_dir) if os.path.isfile(os.path.join(args.input_dir, f))]
        
        for file_name in tqdm(input_files, desc="Processing files"):
            with open(os.path.join(args.input_dir, file_name), 'r') as f:
                text = f.read()
            
            try:
                classification = classify_one(text_deal, text, args)
            except Exception as exc:
                # Preserve alignment and successful outputs from the same batch.
                classification = {"error": f"{type(exc).__name__}: {exc}"}

            out_name = os.path.splitext(file_name)[0] + ".json"
            out_path = os.path.join(args.output_dir, out_name)
            
            with open(out_path, 'w') as out_f:
                json.dump(classification, out_f, indent=2)
            
    else:
        with open(args.input_file, 'r') as f:
            text = f.read()
        
        classification = classify_one(text_deal, text, args)

        with open(args.output_file, 'w') as out_f:
            json.dump(classification, out_f, indent=2)

if __name__ == '__main__':
    main()
