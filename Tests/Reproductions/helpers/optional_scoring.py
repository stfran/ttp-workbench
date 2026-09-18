"""Scoring retained from the individual historical reproduction workflows."""
import csv
import json
from pathlib import Path
import re
from functools import lru_cache


ORBINATO_MODELS = (
    "MLP",
    "Logreg",
    "Multinomial_NB",
    "SVM_OVR",
    "CNN",
    "LSTM",
    "PRETRAINED_LSTM",
    "SecBERT",
)
ORBINATO_THRESHOLDS = tuple(round(i / 10, 1) for i in range(1, 9))


def seqmask_records(data, table, tool):
    if tool == "SeqMask":
        from helpers.source_evaluation import seqmask_runner as seqmask_evaluation
        seqmask_evaluation.DATA_PATH_MAP = {str(n): data / filename for n, filename in [(7, "data_origin13.csv"), (14, "TTPDrill-subTTP.csv"), (15, "data_origin4.csv")]}
        texts, labels = seqmask_evaluation.load_data(table, table in ("7", "15"))
    else:
        import pandas as pd
        from helpers.source_evaluation.seqmask_adapter_repro import extract_text_and_labels
        filenames = {"7": "data_origin13.csv", "14": "TTPDrill-subTTP.csv", "15": "data_origin4.csv"}
        texts, labels = extract_text_and_labels(pd.read_csv(data / filenames[table]), table)
        labels = [[c for c in row if not c.startswith("TA")] for row in labels]
    return [dict(id="row_{:06d}".format(i), text=text, labels=label) for i, (text, label) in enumerate(zip(texts, labels))]


def rcatt_codes(raw):
    # Do not construct an adapter just to parse archived/native STIX.
    from Framework.utils.rcatt_ttp_map import ALL_TTPS, STIX_IDENTIFIERS
    mapping = dict(zip(STIX_IDENTIFIERS, ALL_TTPS))
    return sorted({mapping[k] for k in raw.get("object_refs", []) if k in mapping})


def seqmask_scores(records, raw, table, tool):
    import numpy as np
    from helpers.source_evaluation import seqmask_runner as seqmask_evaluation
    errors = sum(bool(r.get("error")) for r in raw)
    truth = [r["labels"] for r in records]
    if tool == "SeqMask":
        scores = []
        for r in raw:
            merged = {}
            if not r.get("error"):
                if table in ("7", "15"):
                    merged.update(r.get("total_tactics") or {})
                merged.update(r.get("total_techniques") or {})
            scores.append(merged)
        gt_labels = {c for row in truth for c in row}
        model_labels = {seqmask_evaluation.normalize_code(k) for row in scores for k in row}
        labels = sorted(gt_labels & model_labels)
        predictions = [seqmask_evaluation.scores_to_preds(r, tau=.5, top_k=10) for r in scores]
        policy = dict(tau=.5, top_k=10, include_tactics=table in ("7", "15"),
                      normalization="Historical table-specific parent normalization", label_space="GT/model intersection",
                      excluded_gt_labels=sorted(gt_labels - model_labels), excluded_model_labels=sorted(model_labels - gt_labels))
    else:
        labels = sorted({c for row in truth for c in row})
        predictions = [[c for c in rcatt_codes(r) if not c.startswith("TA")] if not r.get("error") else [] for r in raw]
        policy = dict(include_tactics=False, normalization="Retained rcATT baseline IDs; no parent collapse", label_space="GT labels")
    if not labels:
        raise RuntimeError("No evaluation labels; outputs may all have failed")
    values = tuple(map(float, seqmask_evaluation.eval_metrics(truth, predictions, labels)))
    metrics = dict(zip(("micro_precision", "micro_recall", "micro_f1"), values[:3]))
    policy["evaluated_labels"] = labels
    policy["error_records_as_empty_predictions"] = errors
    # The paper publishes one precision/recall/F1 triplet without naming an
    # averaging convention. Our submitted reproduction reports the micro F1,
    # so comparison.csv uses the micro triplet and retains macro values here as
    # diagnostics rather than presenting them as additional paper results.
    policy["macro_diagnostic"] = dict(zip(("precision", "recall", "f1"), values[3:]))
    return metrics, predictions, policy


def rcatt_scores(records, raw):
    from helpers.source_evaluation.rcatt_table6_repro import evaluate_results
    metrics = {}
    predictions = [rcatt_codes(r) if not r.get("error") else [] for r in raw]
    for scope in ("techniques", "tactics"):
        keep = lambda c: c.startswith("TA") if scope == "tactics" else bool(re.fullmatch(r"T\d{4}(?:\.\d{3})?", c))
        values = evaluate_results([{c for c in row if keep(c)} for row in predictions], [{c for c in r["labels"] if keep(c)} for r in records])
        metrics.update({scope + "/" + k: v for k, v in values.items()})
    return metrics, predictions, dict(label_space="GT/prediction union", dataset_role="retained training-data evaluation")


def _orbinato_sentence_score(sentences, gold, threshold):
    accepted = []
    for sentence in sentences:
        candidates = sentence.get("ttps", [])
        if not candidates:
            continue
        top = max(candidates, key=lambda value: float(value.get("prob", 0)))
        if float(top.get("prob", 0)) > threshold:
            label = top.get("label", top.get("code"))
            if label:
                accepted.append(label)
    prediction = set(accepted)
    tp = len(prediction & gold)
    precision = tp / len(prediction) if prediction else 0
    recall = tp / len(gold) if gold else 0
    # Preserve the released helper's rounded document F1 and 0.01 zero-case.
    f1 = round(2 * precision * recall / (precision + recall) if precision and recall else .01, 2)
    return dict(
        tp=tp,
        fp=len(prediction - gold),
        fn=len(gold - prediction),
        precision=precision,
        recall=recall,
        f1=f1,
        accepted=len(accepted),
        predictions=";".join(sorted(prediction)),
    )


def orbinato_scores(records, raw):
    """Evaluate all eight Figure 3 classifiers at the eight source thresholds.

    Framework raw outputs contain sentence probabilities and are scored here.
    Native execution uses the threshold results calculated by the released
    ``document_analysis`` and SecBERT notebook routines; those values are
    retained so that the direct path is genuinely the upstream evaluation.
    """
    rows, predictions = [], []
    for record, payload in zip(records, raw):
        model_results = payload.get("model_results", {})
        missing = [model for model in ORBINATO_MODELS if model not in model_results]
        if missing:
            detail = payload.get("error", "missing model output")
            raise RuntimeError("Orbinato {}: {} ({})".format(record["id"], detail, ", ".join(missing)))
        gold = set(record["labels"])
        primary = []
        for model in ORBINATO_MODELS:
            result = model_results[model]
            precomputed = result.get("threshold_results", {})
            for threshold in ORBINATO_THRESHOLDS:
                key = "{:.1f}".format(threshold)
                if key in precomputed:
                    scored = dict(precomputed[key])
                    sentences = result.get("sentences")
                    if isinstance(sentences, list):
                        identified = _orbinato_sentence_score(
                            sentences, gold, threshold)
                        for field in ("tp", "fp", "fn"):
                            if int(scored[field]) != int(identified[field]):
                                raise RuntimeError(
                                    "Orbinato retained {} identities disagree with the "
                                    "released {} count for {} {} at tau={}".format(
                                        field, field, record["id"], model, threshold))
                        scored["predictions"] = identified["predictions"]
                    else:
                        scored.setdefault("predictions", "")
                else:
                    scored = _orbinato_sentence_score(result.get("sentences", []), gold, threshold)
                rows.append(dict(
                    id=record["id"],
                    panel=record.get("panel", ""),
                    model=model,
                    threshold=threshold,
                    tp=int(scored["tp"]),
                    fp=int(scored["fp"]),
                    fn=int(scored["fn"]),
                    precision=float(scored["precision"]),
                    recall=float(scored["recall"]),
                    f1=float(scored["f1"]),
                    accepted=int(scored.get("accepted", int(scored["tp"]) + int(scored["fp"]))),
                    predictions=scored.get("predictions", ""),
                ))
                if model == "SecBERT" and threshold == .2:
                    primary = [value for value in scored.get("predictions", "").split(";") if value]
        predictions.append(sorted(primary))
    metrics = {}
    for model in ORBINATO_MODELS:
        for threshold in ORBINATO_THRESHOLDS:
            selected = [row for row in rows if row["model"] == model and row["threshold"] == threshold]
            metrics["model={}/tau={:.1f}/document_f1".format(model, threshold)] = sum(row["f1"] for row in selected) / len(selected)
    return metrics, predictions, dict(
        models=list(ORBINATO_MODELS),
        thresholds=list(ORBINATO_THRESHOLDS),
        top_k=1,
        comparison="strictly greater than threshold",
        f1="released per-document rounding with 0.01 when precision or recall is zero",
        per_document=rows,
    )


def rafag_scores(records, predictions):
    from helpers.source_evaluation import evaluate_raf_ag as raf_evaluation
    rows = []
    generous_rows = []
    for record, prediction in zip(records, predictions):
        labels = record["labels"]
        groups, tactics = raf_evaluation._build_gt_groups_and_tactics(labels)
        gold = {raf_evaluation._parent(c) for c in labels if raf_evaluation._is_tech_like(c)}
        pred = {raf_evaluation._parent(c) for c in prediction if raf_evaluation._is_tech_like(c)}
        exact = (len(pred & gold), len(pred-gold), len(gold-pred))
        p, r, f = raf_evaluation._prf(*exact)
        rows.append(dict(id=record["id"], report=record.get("report", ""), tp=exact[0], fp=exact[1], fn=exact[2],
                         precision=p, recall=r, f1=f))

        # This group-aware matching with tactic fallback arose from discussion
        # with the RAF-AG authors. It is retained as diagnostic evidence, but
        # the paper does not describe it and its external tactic lookup is
        # brittle on unknown data. The published comparison therefore uses
        # exact normalized parent technique IDs above.
        generous = raf_evaluation._count_matches(set(prediction), groups, tactics, tactic_fallback=True)
        p, r, f = raf_evaluation._prf(*generous)
        generous_rows.append(dict(id=record["id"], report=record.get("report", ""), tp=generous[0], fp=generous[1], fn=generous[2],
                                   precision=p, recall=r, f1=f))

    metrics = {"document_" + metric: sum(row[metric] for row in rows) / len(rows)
               for metric in ("precision", "recall", "f1")}
    generous_metrics = {"document_" + metric: sum(row[metric] for row in generous_rows) / len(generous_rows)
                        for metric in ("precision", "recall", "f1")}
    return metrics, dict(
        per_document=rows,
        evaluation="Exact normalized parent technique IDs; document-average metrics",
        generous_diagnostic=dict(
            note="Author-discussed grouped-label matching with tactic fallback; omitted from comparison.csv because it is not documented in the paper and is brittle on unknown data.",
            metrics=generous_metrics,
            per_document=generous_rows,
        ),
    )


@lru_cache(maxsize=1024)
def frozen_ttp_details(code):
    path = Path(__file__).resolve().parents[1] / "data/optional/rafag/tactic_lookup.json"
    mapping = json.loads(path.read_text())
    return {"tactic_codes": mapping.get(code, [])}


def save_csv(path, rows):
    if not rows:
        return
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
