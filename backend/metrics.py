import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class _Metrics:
    orders_placed: int = 0
    orders_filled: int = 0
    orders_rejected: int = 0
    adapter_calls: int = 0
    adapter_errors: int = 0
    _order_durations: list[float] = field(default_factory=list)
    _adapter_durations: list[float] = field(default_factory=list)
    _lock: Lock = field(default_factory=Lock)

    def record_order(self, duration_s: float, *, filled: bool) -> None:
        with self._lock:
            self.orders_placed += 1
            if filled:
                self.orders_filled += 1
            else:
                self.orders_rejected += 1
            self._order_durations.append(duration_s)

    def record_adapter_call(self, duration_s: float, *, error: bool = False) -> None:
        with self._lock:
            self.adapter_calls += 1
            if error:
                self.adapter_errors += 1
            self._adapter_durations.append(duration_s)

    def summary(self) -> dict:
        with self._lock:
            def avg_ms(times: list[float]) -> float:
                return round(sum(times) / len(times) * 1000, 2) if times else 0.0

            return {
                "orders_placed": self.orders_placed,
                "orders_filled": self.orders_filled,
                "orders_rejected": self.orders_rejected,
                "fill_rate": round(self.orders_filled / self.orders_placed, 4) if self.orders_placed else 0.0,
                "avg_order_execution_ms": avg_ms(self._order_durations),
                "adapter_calls": self.adapter_calls,
                "adapter_errors": self.adapter_errors,
                "avg_adapter_latency_ms": avg_ms(self._adapter_durations),
            }


metrics = _Metrics()


@contextmanager
def time_adapter_call():
    t0 = time.perf_counter()
    error = False
    try:
        yield
    except Exception:
        error = True
        raise
    finally:
        metrics.record_adapter_call(time.perf_counter() - t0, error=error)
