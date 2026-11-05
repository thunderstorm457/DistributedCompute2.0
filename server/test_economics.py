import unittest
from decimal import Decimal

from server.economics import (
    quote_credits,
    quote_distributed_credits,
    settle_credits,
    settle_distributed_credits,
)


class CreditEconomicsTests(unittest.TestCase):
    def test_one_credit_buys_one_reference_cpu_minute_with_one_gib_minute(self):
        self.assertEqual(quote_credits(1, 60, 1, 1000, 1000), Decimal("1.00"))

    def test_multicore_quote_scales_by_cpu_and_reserved_memory(self):
        self.assertEqual(quote_credits(4, 1800, 4, 1000, 1000), Decimal("120.00"))

    def test_memory_heavy_quote_costs_more_than_cpu_time(self):
        self.assertEqual(quote_credits(1, 60, 4, 1000, 1000), Decimal("4.00"))

    def test_faster_node_finishes_equal_work_for_equal_credits(self):
        self.assertEqual(quote_credits(1, 30, 0.5, 2000, 1000), Decimal("1.00"))

    def test_settlement_uses_measured_cpu_and_reserved_ram(self):
        self.assertEqual(settle_credits(30, 30, 2000, 1000), Decimal("1.00"))

    def test_one_account_can_quote_capacity_across_many_nodes(self):
        allocation = (1, 600, 1, 1000, 1000)
        self.assertEqual(
            quote_distributed_credits([allocation] * 64), Decimal("640.00")
        )

    def test_distributed_settlement_sums_each_providers_usage(self):
        allocation = (60, 60, 1000, 1000)
        self.assertEqual(
            settle_distributed_credits([allocation] * 64), Decimal("64.00")
        )

    def test_quotes_round_up_to_the_smallest_credit_fraction(self):
        self.assertEqual(quote_credits(1, 1, 0.001, 500, 1000), Decimal("0.01"))

    def test_invalid_resource_values_are_rejected(self):
        with self.assertRaises(ValueError):
            quote_credits(0, 60, 1, 1000, 1000)
        with self.assertRaises(ValueError):
            settle_credits(-1, 1, 1000, 1000)


if __name__ == "__main__":
    unittest.main()