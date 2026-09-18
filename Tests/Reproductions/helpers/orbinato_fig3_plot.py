"""Plot the six-document Orbinato Figure 3 reproduction."""
import csv
from pathlib import Path


MODEL_ORDER = (
    ("SecBERT", "SecBERT"),
    ("MLP", "MLP"),
    ("SVM_OVR", "OVR"),
    ("CNN", "CNN"),
    ("LSTM", "LSTM"),
    ("PRETRAINED_LSTM", "pre-LSTM"),
    ("Logreg", "Logreg"),
    ("Multinomial_NB", "NB"),
)
THRESHOLDS = tuple(round(i / 10, 1) for i in range(1, 9))
PANEL_TITLES = {
    "a": "(a) FIN6 [20]",
    "b": "(b) FIN6 [25]",
    "c": "(c) MenuPass [51]",
    "d": "(d) MenuPass [47]",
    "e": "(e) WizardSpider [18]",
    "f": "(f) WizardSpider [48]",
}
PANEL_Y_LIMITS = {
    "a": (0.0, 0.71),
    "b": (0.0, 0.59),
    "c": (0.0, 0.49),
    "d": (0.0, 0.52),
    "e": (0.0, 0.63),
    "f": (0.0, 0.49),
}


def _load(path):
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def _values(rows, record_id):
    indexed = {
        (row["model"], round(float(row["threshold"]), 1)): float(row["f1"])
        for row in rows if row["id"] == record_id
    }
    return [[indexed[(model, threshold)] for model, _ in MODEL_ORDER] for threshold in THRESHOLDS]


def _draw(ax, rows, record, *, title=True, show_x=True):
    values = _values(rows, record["id"])
    width = .1
    centers = list(range(len(MODEL_ORDER)))
    for index, (threshold, scores) in enumerate(zip(THRESHOLDS, values)):
        offset = (index - (len(THRESHOLDS) - 1) / 2) * width
        shade = str(.15 + index * .1)
        ax.bar([value + offset for value in centers], scores, width=width,
               color=shade, label="{:.1f}".format(threshold))
    ax.set_xticks(centers)
    ax.set_xticklabels([display for _, display in MODEL_ORDER], fontsize=7)
    ax.set_ylim(0, 1)
    ax.set_ylabel("F1")
    if show_x:
        ax.set_xlabel("Models")
    else:
        ax.tick_params(axis="x", labelbottom=False)
    if title:
        ax.set_title("({}) {}".format(record.get("panel", ""), record["id"]))


def _save_individual(plt, rows, records, label, output):
    for record in records:
        figure, axis = plt.subplots(figsize=(7.2, 3.4))
        _draw(axis, rows, record)
        axis.legend(title="Threshold", ncol=4, fontsize=7, title_fontsize=8)
        figure.tight_layout()
        figure.savefig(output / "{}_{}.png".format(label, record["id"]), dpi=200)
        plt.close(figure)


def _save_grid(plt, rows, records, label, output):
    figure, axes = plt.subplots(2, 3, figsize=(16, 7), sharey=True)
    for axis, record in zip(axes.flat, records):
        _draw(axis, rows, record)
    handles, labels = axes.flat[0].get_legend_handles_labels()
    figure.legend(handles, labels, title="Threshold", ncol=8,
                  loc="upper center", bbox_to_anchor=(.5, 1.01), fontsize=8)
    figure.suptitle(label, y=1.04)
    figure.tight_layout()
    figure.savefig(output / "orbinato_figure3_{}.png".format(label.lower()),
                   dpi=200, bbox_inches="tight")
    plt.close(figure)


def _save_comparison(plt, original, framework, records, results_dir, paper_dir):
    import math
    import numpy as np

    blocks = max(1, math.ceil(len(records) / 3))
    height_ratios = [1.18, 1.0, 1.0] * blocks
    figure, axes = plt.subplots(
        blocks * 3, 3, squeeze=False,
        figsize=(15.5, 8.8 * blocks),
        gridspec_kw={"height_ratios": height_ratios},
    )
    for axis in axes.flat:
        axis.set_visible(False)

    legend_axis = None
    aligned_axes = []
    for index, record in enumerate(records):
        block, column = divmod(index, 3)
        paper_axis = axes[block * 3][column]
        original_axis = axes[block * 3 + 1][column]
        framework_axis = axes[block * 3 + 2][column]
        for axis in (paper_axis, original_axis, framework_axis):
            axis.set_visible(True)

        panel = record.get("panel", "")
        paper_path = Path(paper_dir) / (panel + ".png")
        if not paper_path.is_file():
            raise FileNotFoundError("Missing Orbinato paper panel: {}".format(paper_path))
        paper_axis.imshow(plt.imread(paper_path), aspect="equal")
        paper_axis.axis("off")
        paper_axis.set_title(PANEL_TITLES.get(
            panel, "({}) {}".format(panel, record["id"])), pad=7, fontsize=12)

        _draw(original_axis, original, record, title=False, show_x=False)
        _draw(framework_axis, framework, record, title=False, show_x=True)
        aligned_axes.append((paper_axis, original_axis, framework_axis))
        if legend_axis is None:
            legend_axis = original_axis
        ylim = PANEL_Y_LIMITS.get(panel, (0.0, 1.0))
        upper_tick = math.floor(ylim[1] * 10) / 10
        ticks = np.arange(ylim[0], upper_tick + .001, .1)
        for axis in (original_axis, framework_axis):
            axis.set_ylim(*ylim)
            axis.set_yticks(ticks)
            axis.set_yticklabels(["{:.1f}".format(value) for value in ticks], fontsize=8)
            axis.set_ylabel("F1" if column == 0 else "")

        if column == 0:
            for axis, label in ((paper_axis, "Paper"),
                                (original_axis, "Original tool"),
                                (framework_axis, "Framework")):
                axis.text(-.14, .5, label, rotation=90, va="center", ha="center",
                          transform=axis.transAxes, fontsize=11)

    if legend_axis is not None:
        handles, labels = legend_axis.get_legend_handles_labels()
        figure.legend(handles, labels, title="Threshold", ncol=8,
                      loc="upper center", bbox_to_anchor=(.5, .997),
                      fontsize=8, title_fontsize=9)
    figure.subplots_adjust(left=.075, right=.99, bottom=.04, top=.94,
                           hspace=.48, wspace=.16)
    # ``imshow(..., aspect="equal")`` narrows each cropped paper panel within
    # its grid cell.  Carry that rendered width and center into both generated
    # rows so all three visual panels have identical widths.  The older
    # reconciliation runner applied the screenshot aspect only to standalone
    # figures, which left its stacked bar axes stretched across the grid cell.
    figure.canvas.draw()
    for paper_axis, original_axis, framework_axis in aligned_axes:
        paper_box = paper_axis.get_position()
        center = paper_box.x0 + paper_box.width / 2
        for axis in (original_axis, framework_axis):
            box = axis.get_position()
            axis.set_position([
                center - paper_box.width / 2,
                box.y0,
                paper_box.width,
                box.height,
            ])
    target = Path(results_dir) / "orbinato_figure3_comparison.png"
    figure.savefig(target, dpi=250, bbox_inches="tight")
    plt.close(figure)
    return target


def build_orbinato_plots(results_dir, records, paper_dir):
    """Generate supporting plots and the paper/direct/framework main comparison."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    results_dir = Path(results_dir)
    original = _load(results_dir / "Orbinato_original_per_document.csv")
    framework = _load(results_dir / "Orbinato_framework_per_document.csv")
    output = results_dir / "figures"
    output.mkdir(parents=True, exist_ok=True)
    _save_individual(plt, original, records, "original", output)
    _save_individual(plt, framework, records, "framework", output)
    _save_grid(plt, original, records, "original", output)
    _save_grid(plt, framework, records, "framework", output)
    primary = _save_comparison(
        plt, original, framework, records, results_dir, paper_dir)
    legacy = output / "orbinato_figure3_comparison.png"
    legacy.unlink(missing_ok=True)
    return primary
