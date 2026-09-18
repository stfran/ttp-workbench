"""Translate native Buchel Table 13 score files; no replacement evaluator."""
import ast
import math


def read_native_scores(path, tool, trial):
    """Preserve the released evaluator's percentage rounding and label scopes."""
    metrics = {}
    for line in path.read_text().splitlines():
        label, value = line.split(":", 1)
        metric, scope = label.split(" ", 1)
        if metric not in ("F1", "PREC", "REC") or metric + scope in metrics:
            raise ValueError("Unexpected native metric row: " + label)
        if scope == "(all)":
            metrics[metric + scope] = {"open": float(value)}
        else:
            scopes, values = ast.literal_eval(scope), ast.literal_eval(value.strip())
            if scopes != ["10", "25", "50", "118"] or len(values) != len(scopes):
                raise ValueError("Unexpected native label scopes: " + label)
            metrics[metric + scope] = dict(zip(scopes, values))
    if len(metrics) != 6:
        raise ValueError("Incomplete native score file: " + str(path))
    key = tool + "/" + trial
    scores = {}
    for name, display in (("F1", "F1"), ("PREC", "precision"), ("REC", "recall")):
        rows = [values for label, values in metrics.items() if label.startswith(name)]
        if len(rows) != 2:
            raise ValueError("Incomplete native metric: " + name)
        for values in rows:
            for scope, value in values.items():
                if not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= 100:
                    raise ValueError("Invalid native percentage: " + str(value))
                scores[key + " " + display + " " + scope] = value / 100
    scores[key + " F1 published50_archived25"] = scores[key + " F1 25"]
    for label, scopes in (("historical 25/118/open average", ("25", "118", "open")),
                          ("actual 50/118/open average", ("50", "118", "open"))):
        scores[key + " F1 " + label] = sum(scores[key + " F1 " + scope] for scope in scopes) / 3
    return scores
