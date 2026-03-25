import time


class CircuitBreaker:
    """Circuit breaker for Claude API resilience.

    States: closed -> open (after N failures) -> half_open (after timeout) -> closed (on success)
    """

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 300.0,
                 half_open_max_calls: int = 2):
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_max_calls = half_open_max_calls
        self._failure_count = 0
        self._half_open_calls = 0
        self._last_failure_time = 0.0
        self._state = "closed"

    @property
    def state(self) -> str:
        return self._state

    def allow_request(self) -> bool:
        if self._state == "closed":
            return True
        if self._state == "open":
            if time.monotonic() - self._last_failure_time >= self._recovery_timeout:
                self._state = "half_open"
                self._half_open_calls = 1
                return True
            return False
        # half_open — allow limited test requests
        if self._half_open_calls < self._half_open_max_calls:
            self._half_open_calls += 1
            return True
        return False

    def record_success(self) -> None:
        self._failure_count = 0
        self._half_open_calls = 0
        self._state = "closed"

    def record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._state == "half_open" or self._failure_count >= self._failure_threshold:
            self._state = "open"

    def reset(self) -> None:
        self._failure_count = 0
        self._half_open_calls = 0
        self._last_failure_time = 0.0
        self._state = "closed"
