#!/usr/bin/env python3

import argparse
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize


# ---------------------------------------------------------------------
# Manual label offsets.
#
# Units are points, not data coordinates.
# Positive dx moves right; positive dy moves up.
# Tweak these until the paper version looks right.
# ---------------------------------------------------------------------
LABEL_OFFSETS = {
    "TTPDrill":          {"xytext": (-8, -12), "ha": "center", "va": "top"},
    "rcATT":             {"xytext": (-10,  8), "ha": "center", "va": "bottom"},
    "AttacKG":           {"xytext": (-6, -12), "ha": "center", "va": "top"},
    "TRAM":              {"xytext": (0, -14),  "ha": "center", "va": "top"},
    "RAF-AG":            {"xytext": (10, 6),   "ha": "center", "va": "bottom"},
    "TTP-LLM":           {"xytext": (0, 8),    "ha": "center", "va": "bottom"},
    "Orbinato-MLP":      {"xytext": (-4, -12), "ha": "center", "va": "top"},
    "Orbinato-LSTM":     {"xytext": (16, -2),  "ha": "left",   "va": "center"},
    "Orbinato-SecBERT":  {"xytext": (12, -4),  "ha": "left",   "va": "center"},
    "Buchel":            {"xytext": (-18, 10), "ha": "center", "va": "bottom"},
    "LADDER":            {"xytext": (14, -2),  "ha": "left",   "va": "center"},
    "SeqMask":           {"xytext": (0, -12),  "ha": "center", "va": "top"},
}


F1_COLORS = {
    0.1: "#d62728",  # red
    0.2: "#ff7f0e",  # orange
    0.3: "#2ca02c",  # green
    0.4: "#1f77ff",  # blue
}

F1_LABEL_POSITIONS = {
    0.4: {"x": 0.77, "offset": (8, 4),  "ha": "left", "va": "center"},
    0.3: {"x": 0.77, "offset": (8, 4),  "ha": "left", "va": "center"},
    0.2: {"x": 0.77, "offset": (8, 4),  "ha": "left", "va": "center"},
    0.1: {"x": 0.77, "offset": (8, 4),  "ha": "left", "va": "center"},
}


def f1_iso_precision(recall, f1):
    """
    Given F1 and recall, solve for precision:

        F1 = 2PR / (P + R)

    so:

        P = F1 * R / (2R - F1)
    """
    denom = 2 * recall - f1
    precision = np.full_like(recall, np.nan, dtype=float)
    valid = denom > 0
    precision[valid] = (f1 * recall[valid]) / denom[valid]
    return precision


def plot_prc(
    csv_path,
    out_prefix,
    eval_mode="generous",
    font_scale=1.8,
    figsize=(7.6, 3.6),
):
    df = pd.read_csv(csv_path)

    d = df[df["eval"] == eval_mode].copy()
    if d.empty:
        raise ValueError(f"No rows found for eval={eval_mode!r}")

    # The original plot used micro precision/recall under generous evaluation.
    x_col = "recall_micro"
    y_col = "precision_micro"

    # Coverage colorbar uses support_macro_labels, not capacity.
    coverage_col = "support_macro_labels"

    # Font sizes: scaled up relative to the small original figure.
    title_fs = 10 * font_scale
    axis_fs = 9 * font_scale
    tick_fs = 8 * font_scale
    label_fs = 7 * font_scale
    f1_fs = 7 * font_scale
    cbar_fs = 8 * font_scale

    plt.rcParams.update({
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "font.size": axis_fs,
    })

    fig, ax = plt.subplots(figsize=figsize)

    # Axes chosen to match the figure.
    xmin, xmax = 0.15, 0.85
    ymin, ymax = 0.00, 0.40
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)

    # F1 iso-curves.
    recall_grid = np.linspace(xmin, xmax, 600)
    for f1, color in F1_COLORS.items():
        precision_grid = f1_iso_precision(recall_grid, f1)
        mask = (precision_grid >= ymin) & (precision_grid <= ymax)

        ax.plot(
            recall_grid[mask],
            precision_grid[mask],
            linestyle="--",
            linewidth=1.1,
            color=color,
            zorder=1,
        )

        # Manual label placement for each F1 curve
        cfg = F1_LABEL_POSITIONS.get(
            f1,
            {"x": 0.79, "offset": (6, 0), "ha": "left", "va": "center"}
        )

        x_lab = cfg["x"]
        y_lab = f1_iso_precision(np.array([x_lab]), f1)[0]

        if np.isfinite(y_lab) and ymin <= y_lab <= ymax:
            ax.annotate(
                f"F1={f1:.1f}",
                xy=(x_lab, y_lab),
            xytext=cfg["offset"],
            textcoords="offset points",
            color=color,
            fontsize=f1_fs,
            ha=cfg["ha"],
            va=cfg["va"],
            zorder=2,
        )

    # Scatter points, grayscale by TTP coverage.
    norm = Normalize(
        vmin=d[coverage_col].min(),
        vmax=d[coverage_col].max(),
    )

    sc = ax.scatter(
        d[x_col],
        d[y_col],
        c=d[coverage_col],
        cmap="Greys",
        norm=norm,
        s=38,
        edgecolors="black",
        linewidths=0.6,
        zorder=3,
    )

    # Tool labels.
    default_label_cfg = {"xytext": (0, -10), "ha": "center", "va": "top"}

    for _, row in d.iterrows():
        tool = row["tool"]
        cfg = default_label_cfg.copy()
        cfg.update(LABEL_OFFSETS.get(tool, {}))

        ax.annotate(
            tool,
            xy=(row[x_col], row[y_col]),
            xytext=cfg["xytext"],
            textcoords="offset points",
            ha=cfg["ha"],
            va=cfg["va"],
            fontsize=label_fs,
            zorder=4,
        )

    # Titles and axis labels.
    ax.set_title("Micro Precision vs Recall", fontsize=title_fs, pad=6)
    ax.set_xlabel("Recall (micro)", fontsize=axis_fs)
    ax.set_ylabel("Precision (micro)", fontsize=axis_fs)

    ax.set_xticks([0.15, 0.25, 0.50, 0.75])
    ax.set_yticks(np.arange(0.0, 0.41, 0.1))
    ax.tick_params(axis="both", labelsize=tick_fs)

    # Colorbar: match original tick values visually.
    cbar = fig.colorbar(sc, ax=ax, pad=0.10, fraction=0.045)
    cbar.set_label("TTP coverage", fontsize=cbar_fs)
    cbar.ax.tick_params(labelsize=tick_fs)

    # Use rounded integer ticks spanning the observed coverage range.
    cbar_ticks = np.linspace(
        d[coverage_col].min(),
        d[coverage_col].max(),
        5,
    )
    cbar.set_ticks(cbar_ticks)
    cbar.set_ticklabels([str(int(round(t))) for t in cbar_ticks])

    fig.tight_layout()

    fig.savefig(f"{out_prefix}.pdf", bbox_inches="tight")
    fig.savefig(f"{out_prefix}.png", dpi=300, bbox_inches="tight")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", help="CSV containing the evaluation summary metrics.")
    parser.add_argument(
        "--out",
        default="prc_generous",
        help="Output prefix. Writes <prefix>.pdf and <prefix>.png.",
    )
    parser.add_argument(
        "--eval",
        default="generous",
        choices=["strict", "generous"],
        help="Evaluation mode to plot.",
    )
    parser.add_argument(
        "--font-scale",
        type=float,
        default=1.8,
        help="Scale factor for plot text. Try 1.5, 1.8, or 2.0.",
    )
    args = parser.parse_args()
    if "/" in args.out or "\\" in args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)

    plot_prc(
        csv_path=args.csv,
        out_prefix=args.out,
        eval_mode=args.eval,
        font_scale=args.font_scale,
    )


if __name__ == "__main__":
    main()