#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

DEFAULT_TOOLS = [
    "TTPDrill", "rcATT", "TRAM",
    "AttacKG", "Orbinato-MLP", "SeqMask", "LADDER", "TTP-LLM", "RAF-AG", "Buchel",
]


def prf_from_counts(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    denom_p = out["tp"] + out["fp"]
    denom_r = out["tp"] + out["fn"]
    out["precision"] = np.where(denom_p > 0, out["tp"] / denom_p, 0.0)
    out["recall"] = np.where(denom_r > 0, out["tp"] / denom_r, 0.0)
    denom_f = out["precision"] + out["recall"]
    out["f1"] = np.where(denom_f > 0, 2 * out["precision"] * out["recall"] / denom_f, 0.0)
    return out


def aggregate_combined(per_ttp_path: Path, tools: list[str], exclude_tactics: bool = True) -> pd.DataFrame:
    df = pd.read_csv(per_ttp_path)
    df = df[df["tool"].isin(tools)].copy()
    if exclude_tactics:
        df = df[~df["ttp"].astype(str).str.startswith("TA")].copy()

    numeric_cols = ["support", "pred_support", "tp", "fp", "fn"]
    agg = (
        df.groupby(["tool", "ttp"], as_index=False)[numeric_cols]
        .sum()
        .sort_values(["tool", "ttp"])
        .reset_index(drop=True)
    )
    return prf_from_counts(agg)


def ttp_stats_for_supported(agg: pd.DataFrame, min_support: int) -> pd.DataFrame:
    supported = agg[agg["support"] >= min_support].copy()
    ttp_stats = (
        supported.groupby("ttp", as_index=False)
        .agg(
            tools_with_support=("tool", "nunique"),
            total_support=("support", "sum"),
            mean_f1=("f1", "mean"),
            max_f1=("f1", "max"),
            min_f1=("f1", "min"),
        )
    )
    ttp_stats["f1_spread"] = ttp_stats["max_f1"] - ttp_stats["min_f1"]
    return supported.merge(ttp_stats, on="ttp", how="left")


def filter_scope(df: pd.DataFrame, scope: str, min_tools_with_support: int) -> pd.DataFrame:
    if scope == "all":
        return df.copy()
    if scope == "comparable":
        return df[df["tools_with_support"] >= min_tools_with_support].copy()
    raise ValueError(f"Unknown scope: {scope}")


def add_best_tool_oracle(df: pd.DataFrame, oracle_label: str) -> pd.DataFrame:
    """
    Add a best-tool oracle column.

    For each TTP in the filtered plotting scope, the oracle receives the best
    observed per-TTP F1 among support-qualified tools. This is not an absolute
    perfect classifier; it is an oracle selector over the evaluated tools.
    """
    oracle = (
        df.sort_values(["ttp", "f1", "support", "tool"], ascending=[True, False, False, True])
        .groupby("ttp", as_index=False)
        .first()
    )
    oracle["oracle_source_tool"] = oracle["tool"]
    oracle["tool"] = oracle_label
    return pd.concat([df, oracle[df.columns.tolist() + ["oracle_source_tool"] if "oracle_source_tool" in df.columns else oracle.columns]], ignore_index=True, sort=False)


def make_plot_data(
    per_ttp_path: Path,
    tools: list[str],
    scope: str,
    min_support: int,
    min_tools_with_support: int,
    include_tactics: bool,
    add_oracle: bool,
    oracle_label: str,
) -> tuple[pd.DataFrame, list[str]]:
    agg = aggregate_combined(per_ttp_path, tools=tools, exclude_tactics=not include_tactics)
    supported = ttp_stats_for_supported(agg, min_support=min_support)
    plot_df = filter_scope(supported, scope=scope, min_tools_with_support=min_tools_with_support)
    if plot_df.empty:
        raise ValueError("No rows remain after filtering.")

    present_tools = [t for t in tools if t in set(plot_df["tool"])]
    if add_oracle:
        oracle = (
            plot_df.sort_values(["ttp", "f1", "support", "tool"], ascending=[True, False, False, True])
            .groupby("ttp", as_index=False)
            .first()
        )
        oracle["oracle_source_tool"] = oracle["tool"]
        oracle["tool"] = oracle_label
        plot_df = pd.concat([plot_df, oracle], ignore_index=True, sort=False)
        present_tools = present_tools + [oracle_label]
    return plot_df, present_tools


def plot_boxplots(
    df: pd.DataFrame,
    tools: list[str],
    output: Path,
    scope: str,
    min_support: int,
    min_tools_with_support: int,
    oracle_label: str,
    show_points: bool,
    point_alpha: float,
    point_size: float,
    width: float,
    height: float,
    dpi: int,
    title: str | None,
    ylabel: str,
    rotate_xticks: float,
    y_max: float,
    showfliers: bool,
) -> None:
    data = [df.loc[df["tool"] == t, "f1"].to_numpy() for t in tools]

    fig, ax = plt.subplots(figsize=(width, height))

    flierprops = dict(
        marker="o",
        markerfacecolor="none",
        markeredgecolor="0.35",
        markersize=2.6,
        linestyle="none",
        markeredgewidth=0.5,
        alpha=0.8,
    )

    boxplot_kwargs = dict(
        widths=0.55,
        patch_artist=False,
        showfliers=showfliers,
        medianprops=dict(linewidth=1.25),
        boxprops=dict(linewidth=0.95),
        whiskerprops=dict(linewidth=0.85),
        capprops=dict(linewidth=0.85),
        flierprops=flierprops,
    )
    try:
        bp = ax.boxplot(data, tick_labels=tools, **boxplot_kwargs)
    except TypeError:
        bp = ax.boxplot(data, labels=tools, **boxplot_kwargs)

    if show_points:
        rng = np.random.default_rng(7)
        for i, tool in enumerate(tools, start=1):
            sub = df[df["tool"] == tool].copy()
            if sub.empty:
                continue
            jitter = rng.uniform(-0.14, 0.14, size=len(sub))
            ax.scatter(i + jitter, sub["f1"], s=point_size, alpha=point_alpha, zorder=2)

    # Lightly distinguish the oracle column without adding a legend.
    if oracle_label in tools:
        oracle_idx = tools.index(oracle_label)      # 0-based
        oracle_x = oracle_idx + 1                   # matplotlib boxplot position

        # Divider before Oracle column
        ax.axvline(
            oracle_x - 0.5,
            color="0.55",
            linewidth=0.8,
            linestyle="--",
            zorder=0,
        )

        # Bold Oracle box
        bp["boxes"][oracle_idx].set_linewidth(1.8)

        # Bold Oracle median
        bp["medians"][oracle_idx].set_linewidth(2.0)

        # Bold Oracle whiskers and caps
        for line in bp["whiskers"][2 * oracle_idx : 2 * oracle_idx + 2]:
            line.set_linewidth(1.5)

        for line in bp["caps"][2 * oracle_idx : 2 * oracle_idx + 2]:
            line.set_linewidth(1.5)

        # Bold Oracle tick label
        ax.get_xticklabels()[oracle_idx].set_fontweight("bold")

    ax.set_ylim(-0.02, y_max)
    ax.set_ylabel(ylabel)
    ax.grid(axis="y", alpha=0.22, linewidth=0.7)
    ax.set_axisbelow(True)
    plt.setp(ax.get_xticklabels(), rotation=rotate_xticks, ha="right")

    if title is None:
        if scope == "all":
            title = f"Per-tool TTP performance distribution\nall support-qualified TTPs; support ≥ {min_support}"
        else:
            title = (
                "Per-tool TTP performance distribution\n"
                f"TTPs supported by at least {min_tools_with_support} tools; support ≥ {min_support}"
            )
    elif title != "none":
        ax.set_title(title, fontsize=10.5)

    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=dpi, bbox_inches="tight")
    if output.suffix.lower() == ".pdf":
        fig.savefig(output.with_suffix(".png"), dpi=dpi, bbox_inches="tight")
    plt.close(fig)

    df.to_csv(output.with_suffix(".plotted_points.csv"), index=False)
    if oracle_label in set(df["tool"]):
        df[df["tool"] == oracle_label][["ttp", "f1", "oracle_source_tool", "support"]].to_csv(
            output.with_suffix(".oracle_points.csv"), index=False
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input_dir", type=Path, default=Path("."), help="Directory containing per_ttp_tool_dataset.csv")
    ap.add_argument("--output", type=Path, default=Path("ttp_boxplots_oracle.png"))
    ap.add_argument("--tools", nargs="*", default=DEFAULT_TOOLS)
    ap.add_argument("--scope", choices=["all", "comparable"], default="all")
    ap.add_argument("--min_support", type=int, default=25)
    ap.add_argument("--min_tool_support", type=int, default=None, help="Deprecated alias for --min_support")
    ap.add_argument("--min_tools_with_support", type=int, default=3)
    ap.add_argument("--include_tactics", action="store_true")
    ap.add_argument("--add_oracle", action="store_true", default=True)
    ap.add_argument("--no_oracle", action="store_true")
    ap.add_argument("--oracle_label", type=str, default="Oracle")
    ap.add_argument("--show_points", action="store_true", help="Optional: overlay jittered raw points. Usually omit for the paper figure.")
    ap.add_argument("--point_alpha", type=float, default=0.14)
    ap.add_argument("--point_size", type=float, default=8)
    ap.add_argument("--width", type=float, default=6.0)
    ap.add_argument("--height", type=float, default=3.0)
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--title", type=str, default=None, help="'none' to suppress title")
    ap.add_argument("--ylabel", type=str, default="Per-TTP F1")
    ap.add_argument("--rotate_xticks", type=float, default=22)
    ap.add_argument("--y_max", type=float, default=0.85)
    ap.add_argument("--hide_outliers", action="store_true")
    args = ap.parse_args()

    if args.min_tool_support is not None:
        args.min_support = args.min_tool_support
    if args.no_oracle:
        args.add_oracle = False
    if args.title == "":
        args.title = None

    per_ttp_path = args.input_dir / "per_ttp_tool_dataset.csv"
    if not per_ttp_path.exists():
        raise FileNotFoundError(f"Could not find {per_ttp_path}")

    plot_df, present_tools = make_plot_data(
        per_ttp_path=per_ttp_path,
        tools=args.tools,
        scope=args.scope,
        min_support=args.min_support,
        min_tools_with_support=args.min_tools_with_support,
        include_tactics=args.include_tactics,
        add_oracle=args.add_oracle,
        oracle_label=args.oracle_label,
    )

    plot_boxplots(
        df=plot_df,
        tools=present_tools,
        output=args.output,
        scope=args.scope,
        min_support=args.min_support,
        min_tools_with_support=args.min_tools_with_support,
        oracle_label=args.oracle_label,
        show_points=args.show_points,
        point_alpha=args.point_alpha,
        point_size=args.point_size,
        width=args.width,
        height=args.height,
        dpi=args.dpi,
        title=args.title,
        ylabel=args.ylabel,
        rotate_xticks=args.rotate_xticks,
        y_max=args.y_max,
        showfliers=not args.hide_outliers,
    )
    print(f"Wrote {args.output}")
    print(f"Also wrote {args.output.with_suffix('.plotted_points.csv')}")
    if args.add_oracle:
        print(f"Also wrote {args.output.with_suffix('.oracle_points.csv')}")


if __name__ == "__main__":
    main()
