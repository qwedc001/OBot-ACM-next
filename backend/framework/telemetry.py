"""Small bounded operational snapshot; message content is never collected."""

import time
from collections import Counter, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class CommandRun:
    command_id: str
    correlation_id: str
    outcome: str
    duration_ms: float
    context_ms: float = 0
    handling_ms: float = 0
    delivery_ms: float = 0


class Telemetry:
    def __init__(self) -> None:
        self.started_at = time.time()
        self.last_received_at: float | None = None
        self.last_sent_at: float | None = None
        self.connected_bots: set[str] = set()
        self.runs: deque[CommandRun] = deque(maxlen=100)
        self.counts: Counter[str] = Counter()

    def record(self, run: CommandRun) -> None:
        self.runs.append(run)
        self.counts[run.outcome] += 1

    def snapshot(self) -> dict:
        return {
            "uptime_seconds": round(time.time() - self.started_at, 2),
            "connected_bot_count": len(self.connected_bots),
            "last_received_at": self.last_received_at,
            "last_sent_at": self.last_sent_at,
            "command_outcomes": dict(self.counts),
            "recent_runs": [
                {
                    "command_id": run.command_id,
                    "correlation_id": run.correlation_id,
                    "outcome": run.outcome,
                    "duration_ms": run.duration_ms,
                    "context_ms": run.context_ms,
                    "handling_ms": run.handling_ms,
                    "delivery_ms": run.delivery_ms,
                }
                for run in self.runs
            ],
        }
