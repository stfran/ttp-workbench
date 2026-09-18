#!/usr/bin/env python3
"""Memoize immutable attack-graph distances in the pinned AttacKG matcher."""

import argparse
from pathlib import Path


ORIGINAL = """                if source_node == sink_node:
                    distance = 1
                else:
                    try:
                        distance = nx.shortest_path_length(attack_graph.attackgraph_nx, source_node, sink_node)
                    except:
                        self.edge_match_record[template_edge] = 0.0
                        continue
"""

MEMOIZED = """                if source_node == sink_node:
                    distance = 1
                else:
                    # The attack graph is immutable during matching. Cache each
                    # ordered node-pair distance across templates and candidate
                    # alignments instead of asking NetworkX to recompute it.
                    distance_cache = getattr(attack_graph, \"_shortest_path_length_cache\", None)
                    if distance_cache is None:
                        distance_cache = {}
                        attack_graph._shortest_path_length_cache = distance_cache
                    cache_key = (source_node, sink_node)
                    if cache_key not in distance_cache:
                        try:
                            distance_cache[cache_key] = nx.shortest_path_length(
                                attack_graph.attackgraph_nx, source_node, sink_node)
                        except:
                            distance_cache[cache_key] = None
                    distance = distance_cache[cache_key]
                    if distance is None:
                        self.edge_match_record[template_edge] = 0.0
                        continue
"""


def memoized_source(source: str) -> str:
    """Return source with the exact distance lookup replaced, idempotently."""
    if source.count(MEMOIZED) == 1 and ORIGINAL not in source:
        return source
    if source.count(ORIGINAL) != 1:
        raise RuntimeError("AttacKG shortest-path source did not match the expected revision")
    return source.replace(ORIGINAL, MEMOIZED, 1)


def patch_tree(root: Path) -> Path:
    target = root / "technique_knowledge_graph" / "technique_identifier.py"
    source = target.read_text()
    updated = memoized_source(source)
    if updated != source:
        target.write_text(updated)
    return target


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    patch_tree(args.root.resolve())


if __name__ == "__main__":
    main()
