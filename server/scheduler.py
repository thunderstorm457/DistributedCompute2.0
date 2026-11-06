from datetime import datetime
from typing import Dict, Iterable, List, Mapping, Protocol


class ComputeNode(Protocol):
    id: str
    registered_at: datetime


def choose_compute_nodes(
    nodes: Iterable[ComputeNode],
    active_task_counts: Mapping[str, int],
    shard_count: int,
) -> List[ComputeNode]:
    """Assign shards to the least-loaded distinct nodes, with stable tie-breaking."""
    if shard_count < 1:
        raise ValueError("shard_count must be positive")

    candidates = sorted(
        nodes,
        key=lambda node: (
            max(0, active_task_counts.get(node.id, 0)),
            node.registered_at,
            node.id,
        ),
    )
    if len(candidates) < shard_count:
        raise ValueError("not enough distinct compute nodes for requested shards")
    return candidates[:shard_count]
