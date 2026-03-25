import sys
import time
from unittest.mock import MagicMock

if "frappe" not in sys.modules or not isinstance(sys.modules["frappe"], MagicMock):
    _fm = MagicMock()
    _fm._ = lambda x: x
    sys.modules["frappe"] = _fm
    sys.modules["frappe.utils"] = _fm.utils

import unittest

from brazil_module.services.intelligence.circuit_breaker import CircuitBreaker


class TestCircuitBreaker(unittest.TestCase):
    def test_starts_closed(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1)
        self.assertEqual(cb.state, "closed")

    def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1)
        cb.record_failure()
        cb.record_failure()
        self.assertEqual(cb.state, "closed")
        cb.record_failure()
        self.assertEqual(cb.state, "open")

    def test_rejects_when_open(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60)
        cb.record_failure()
        self.assertFalse(cb.allow_request())

    def test_allows_when_closed(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1)
        self.assertTrue(cb.allow_request())

    def test_transitions_to_half_open_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)
        cb.record_failure()
        self.assertEqual(cb.state, "open")
        time.sleep(0.15)
        self.assertTrue(cb.allow_request())
        self.assertEqual(cb.state, "half_open")

    def test_closes_after_success_in_half_open(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)
        cb.record_failure()
        time.sleep(0.15)
        cb.allow_request()  # transitions to half_open
        cb.record_success()
        self.assertEqual(cb.state, "closed")

    def test_reopens_on_failure_in_half_open(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)
        cb.record_failure()
        time.sleep(0.15)
        cb.allow_request()  # half_open
        cb.record_failure()
        self.assertEqual(cb.state, "open")

    def test_reset_clears_state(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60)
        cb.record_failure()
        self.assertEqual(cb.state, "open")
        cb.reset()
        self.assertEqual(cb.state, "closed")

    def test_half_open_limits_calls(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1, half_open_max_calls=2)
        cb.record_failure()
        time.sleep(0.15)
        self.assertTrue(cb.allow_request())   # half_open call 1
        self.assertTrue(cb.allow_request())   # half_open call 2
        self.assertFalse(cb.allow_request())  # exceeds max


if __name__ == "__main__":
    unittest.main()
