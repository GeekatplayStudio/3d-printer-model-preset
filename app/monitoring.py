from __future__ import annotations

import threading
import time
from typing import Any


class InMemoryMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started_at = time.time()
        self._requests_total = 0
        self._status_counts: dict[str, int] = {}
        self._path_counts: dict[str, int] = {}
        self._latency_samples_ms: list[float] = []

    def record(self, *, path: str, status_code: int, latency_ms: float) -> None:
        status_key = str(status_code)
        with self._lock:
            self._requests_total += 1
            self._status_counts[status_key] = self._status_counts.get(status_key, 0) + 1
            self._path_counts[path] = self._path_counts.get(path, 0) + 1
            if len(self._latency_samples_ms) >= 2000:
                self._latency_samples_ms.pop(0)
            self._latency_samples_ms.append(latency_ms)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            latency = list(self._latency_samples_ms)
            if latency:
                avg_latency = round(sum(latency) / len(latency), 3)
                p95_index = max(0, int(len(latency) * 0.95) - 1)
                p95 = round(sorted(latency)[p95_index], 3)
            else:
                avg_latency = 0.0
                p95 = 0.0

            return {
                "uptime_s": round(time.time() - self._started_at, 3),
                "requests_total": self._requests_total,
                "status_counts": dict(self._status_counts),
                "top_paths": sorted(
                    [{"path": k, "count": v} for k, v in self._path_counts.items()],
                    key=lambda item: item["count"],
                    reverse=True,
                )[:20],
                "latency_ms_avg": avg_latency,
                "latency_ms_p95": p95,
                "latency_samples": len(latency),
            }

    def to_prometheus_text(self) -> str:
        snap = self.snapshot()
        lines = [
            "# HELP resinlogic_requests_total Total HTTP requests handled.",
            "# TYPE resinlogic_requests_total counter",
            f"resinlogic_requests_total {snap['requests_total']}",
            "# HELP resinlogic_uptime_seconds Process uptime in seconds.",
            "# TYPE resinlogic_uptime_seconds gauge",
            f"resinlogic_uptime_seconds {snap['uptime_s']}",
            "# HELP resinlogic_latency_ms_avg Average request latency (ms).",
            "# TYPE resinlogic_latency_ms_avg gauge",
            f"resinlogic_latency_ms_avg {snap['latency_ms_avg']}",
            "# HELP resinlogic_latency_ms_p95 p95 request latency (ms).",
            "# TYPE resinlogic_latency_ms_p95 gauge",
            f"resinlogic_latency_ms_p95 {snap['latency_ms_p95']}",
        ]
        for code, count in sorted(snap["status_counts"].items()):
            lines.append(f'resinlogic_status_count{{code="{code}"}} {count}')
        return "\n".join(lines) + "\n"
