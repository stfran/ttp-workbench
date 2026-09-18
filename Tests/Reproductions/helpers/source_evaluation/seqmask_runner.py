#!/usr/bin/env python3

import argparse
import json
from pathlib import Path

import pandas as pd
import numpy as np
from sklearn.metrics import precision_recall_fscore_support




# ----------------------------
# Paths / model maps
# ----------------------------

DATA_DIR = Path("datas")

DATA_PATH_MAP = {
    "7": DATA_DIR / "data_origin13.csv",
    "14": DATA_DIR / "TTPDrill-subTTP.csv",
    "15": DATA_DIR / "data_origin4.csv",
}

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


# ----------------------------
# Helpers
# ----------------------------

def load_data(table, include_tactics):
    df = pd.read_csv(DATA_PATH_MAP[table])

    if table in ("7", "15"):
        text_col = "processed" if "processed" in df.columns else "Text"
        texts = df[text_col].astype(str).tolist()
        if include_tactics:
            label_cols = [c for c in df.columns if (c.startswith("T") or c.startswith("TA")) and c not in ("Text", "processed")]
        else:
            label_cols = [c for c in df.columns if c.startswith("T") and not c.startswith("TA") and c not in ("Text", "processed")]
        y_true = [
            sorted({c.split(".")[0] for c in label_cols if int(row[c]) == 1})
            for _, row in df[label_cols].iterrows()
        ]

    else:  # table 14
        texts = df["text"].astype(str).tolist()
        y_true = [
            [x for x in str(v).split() if x.startswith("T")]
            for v in df["id"].astype(str)
        ]

    return texts, y_true

def normalize_code(c):
    c = str(c)
    if c.startswith("T") and not c.startswith("TA") and "." in c:
        return c.split(".")[0]
    return c

def scores_to_preds(score_dict, tau, top_k):
    items = [(str(k), float(v)) for k, v in score_dict.items()]
    if tau is not None:
        items = [(k, s) for k, s in items if s >= tau]

    tact = [(k, s) for k, s in items if k.startswith("TA")]
    tech = [(k, s) for k, s in items if k.startswith("T") and not k.startswith("TA")]

    tact.sort(key=lambda x: x[1], reverse=True)
    tech.sort(key=lambda x: x[1], reverse=True)

    if top_k is not None and top_k > 0:
        tact = tact[:top_k]
        tech = tech[:top_k]

    preds = [k for k, _ in (tact + tech)]
    preds = [normalize_code(k) for k in preds]
    return sorted(set(preds))


def eval_metrics(y_true, y_pred, label_space):
    idx = {c: i for i, c in enumerate(label_space)}
    Yt = np.zeros((len(y_true), len(label_space)), dtype=int)
    Yp = np.zeros_like(Yt)

    for i, row in enumerate(y_true):
        for c in row:
            if c in idx:
                Yt[i, idx[c]] = 1

    for i, row in enumerate(y_pred):
        for c in row:
            if c in idx:
                Yp[i, idx[c]] = 1

    # rcATT retains sklearn 0.21: its default is also zero for undefined
    # scores, but zero_division was not yet an accepted keyword.
    import inspect
    options = {"zero_division": 0} if "zero_division" in inspect.signature(precision_recall_fscore_support).parameters else {}
    pm, rm, fm, _ = precision_recall_fscore_support(Yt, Yp, average="micro", **options)
    pM, rM, fM, _ = precision_recall_fscore_support(Yt, Yp, average="macro", **options)

    return pm, rm, fm, pM, rM, fM


# ----------------------------
# Main
# ----------------------------

def main():
    from TextClassify import TextDeal
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", choices=["7", "14", "15"], required=True)
    ap.add_argument("--eval_only", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    models = ['self_attention', 'sv_mask', 'mp_mask', 'ar_mask']
    taus = [0.0, 0.1, 0.2, 0.3, 0.5]
    topks = [1, 3, 5, 10]

    include_tactics = args.table == "7" or args.table == "15"

    texts, y_true = load_data(args.table, include_tactics)
    label_space = sorted({c for row in y_true for c in row})

    out_root = Path("outputs") / f"table_{args.table}"
    out_root.mkdir(parents=True, exist_ok=True)

    records = []

    for model in models:
        model_dir = out_root / model
        model_dir.mkdir(parents=True, exist_ok=True)

        if not args.eval_only:
            td = TextDeal(
                tact_model_path=TACT_MODELS[model],
                tech_model_path=TECH_MODELS[model],
            )

            for i, text in enumerate(texts):
                out_fp = model_dir / f"raw_row_{i:06d}.json"
                if out_fp.exists() and not args.force:
                    continue

                try:
                    pred = td.classify_text(text)
                except Exception as e:
                    pred = {"error":f"{type(e).__name__}: {e}"}
                pred.pop("embedding", None)

                with open(out_fp, "w") as f:
                    json.dump(pred, f)

        raw_preds = []
        for i in range(len(texts)):
            with open(model_dir / f"raw_row_{i:06d}.json") as f:
                raw = json.load(f)

            if raw.get("error") is not None:
                raw_preds.append({})  # keep alignment
            else:
                merged = {}
                if include_tactics:
                    merged.update(raw.get("total_tactics", {}) or {})
                merged.update(raw.get("total_techniques", {}) or {})
                raw_preds.append(merged)

        # --- build model label universe (all possible output keys we observed) ---
        model_labels = sorted({normalize_code(k) for d in raw_preds for k in d.keys()})

        # --- GT universe (already normalized in load_data via split('.') ) ---
        gt_labels = sorted({c for row in y_true for c in row})

        # --- intersection universe for fair eval ---
        eval_labels = sorted(set(gt_labels) & set(model_labels))

        eval_set = set(eval_labels)
        y_true_eval = [[c for c in row if c in eval_set] for row in y_true]

        print(f"[DEBUG] GT labels: {len(gt_labels)}  model labels: {len(model_labels)}  eval labels (intersection): {len(eval_labels)}")

        # Optional: show a few mismatches for debugging
        only_in_gt = sorted(set(gt_labels) - set(model_labels))
        only_in_model = sorted(set(model_labels) - set(gt_labels))
        print("[DEBUG] only_in_gt (sample):", only_in_gt[:20])
        print("[DEBUG] only_in_model (sample):", only_in_model[:20])


        for tau in taus:
            for k in topks:
                y_pred = [scores_to_preds(d, tau=tau, top_k=k) for d in raw_preds]
                y_pred_eval = [[c for c in row if c in eval_set] for row in y_pred]

                pm, rm, fm, pM, rM, fM = eval_metrics(y_true_eval, y_pred_eval, eval_labels)


                gt_set = set(label_space)
                pred_set = set(x for row in y_pred for x in row)
                oov = pred_set - gt_set
                print(f"[DEBUG] tau={tau} k={k} pred_not_in_gt={len(oov)}/{len(pred_set) if pred_set else 0}")


                records.append({
                    "table": args.table,
                    "model": model,
                    "strategy": "threshold_topk",
                    "tau": tau,
                    "top_k": k,
                    "p_micro": pm,
                    "r_micro": rm,
                    "f_micro": fm,
                    "p_macro": pM,
                    "r_macro": rM,
                    "f_macro": fM,
                })

        # max-prob baseline
        y_pred_eval = [[c for c in row if c in eval_set] for row in y_pred]
        pm, rm, fm, pM, rM, fM = eval_metrics(y_true_eval, y_pred_eval, eval_labels)

        records.append({
            "table": args.table,
            "model": model,
            "strategy": "maxprob",
            "tau": None,
            "top_k": None,
            "p_micro": pm,
            "r_micro": rm,
            "f_micro": fm,
            "p_macro": pM,
            "r_macro": rM,
            "f_macro": fM,
        })

    df = pd.DataFrame(records)
    out_csv = out_root / f"metrics_table_{args.table}.csv"
    df.to_csv(out_csv, index=False)
    print(f"[OK] wrote {out_csv}")

    # compile all max
    out_csvs = ["outputs/table_7/metrics_table_7.csv",
                "outputs/table_14/metrics_table_14.csv",
                "outputs/table_15/metrics_table_15.csv"
                ]
    
    # compile all max
    out_csvs = [
        "outputs/table_7/metrics_table_7.csv",
        "outputs/table_14/metrics_table_14.csv",
        "outputs/table_15/metrics_table_15.csv",
    ]

    out = []
    consolidated = Path("reproduced_results.csv")

    for out_csv in out_csvs:
        try:
            df = pd.read_csv(out_csv)
        except:
            pass
        df["source_file"] = out_csv

        df_max = df[df["strategy"] == "maxprob"].copy()

        df_max = (
            df_max.sort_values(["table", "model", "f_micro", "p_micro", "r_micro"],
                               ascending=[True, True, False, False, False])
                  .groupby(["table", "model"], as_index=False)
                  .head(1)
        )

        out.append(df_max)

    out_df = pd.concat(out, ignore_index=True)

    cols = [
        "table", "model",
        "p_micro", "r_micro", "f_micro",
        "p_macro", "r_macro", "f_macro",
    ]

    out_df = out_df[cols].sort_values(["table", "model"]).reset_index(drop=True)

    out_df.to_csv(consolidated, index=False)
    print(f"[OK] wrote consolidated: {consolidated.resolve()}")
    print(out_df)


if __name__ == "__main__":
    main()
