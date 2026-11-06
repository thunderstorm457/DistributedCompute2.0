import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from server.scheduler import choose_compute_nodes


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.nodes = [
            SimpleNamespace(id=f"node-{index}", registered_at=start + timedelta(minutes=index))
            for index in range(4)
        ]

    def test_selects_least_loaded_distinct_nodes(self):
        selected = choose_compute_nodes(
            self.nodes,
            {"node-0": 4, "node-1": 1, "node-2": 1, "node-3": 3},
            2,
        )
        self.assertEqual([node.id for node in selected], ["node-1", "node-2"])

    def test_ties_are_stable_and_distinct(self):
        selected = choose_compute_nodes(self.nodes, {}, 3)
        self.assertEqual([node.id for node in selected], ["node-0", "node-1", "node-2"])
        self.assertEqual(len({node.id for node in selected}), 3)

    def test_requires_enough_nodes(self):
        with self.assertRaises(ValueError):
            choose_compute_nodes(self.nodes[:1], {}, 2)


if __name__ == "__main__":
    unittest.main()
