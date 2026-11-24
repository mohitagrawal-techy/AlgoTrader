"""Execution interface. V1 has only the paper implementation."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

import pandas as pd

from algoverse.risk.limits import PortfolioState


class ExecutionAdapter(Protocol):
    def submit(self, idea, now: datetime) -> int:
        """Queue an approved idea and return its position id."""
        ...

    def step(self, now: datetime, chains: dict[str, pd.DataFrame]):
        """Process one market snapshot of enriched chains, keyed by symbol."""
        ...

    def portfolio_state(self, now: datetime) -> PortfolioState: ...
