"""Async-local ceilings for transport work hidden inside one provider operation."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Iterator

from reconrelate.core.errors import ProviderBudgetExceededError


@dataclass(slots=True)
class ProviderBudget:
    max_requests: int
    max_pages: int
    requests: int = 0
    pages: int = 0

    def consume_request(self) -> None:
        if self.requests >= self.max_requests:
            raise ProviderBudgetExceededError(
                f"provider request budget exceeded ({self.max_requests} per attempt)"
            )
        self.requests += 1

    def consume_page(self) -> None:
        if self.pages >= self.max_pages:
            raise ProviderBudgetExceededError(
                f"provider page budget exceeded ({self.max_pages} per attempt)"
            )
        self.pages += 1


_CURRENT_BUDGET: ContextVar[ProviderBudget | None] = ContextVar(
    "reconrelate_provider_budget", default=None
)


@contextmanager
def provider_budget(*, max_requests: int, max_pages: int) -> Iterator[ProviderBudget]:
    budget = ProviderBudget(max(1, int(max_requests)), max(1, int(max_pages)))
    token: Token[ProviderBudget | None] = _CURRENT_BUDGET.set(budget)
    try:
        yield budget
    finally:
        _CURRENT_BUDGET.reset(token)


def consume_request() -> None:
    budget = _CURRENT_BUDGET.get()
    if budget is not None:
        budget.consume_request()


def consume_page() -> None:
    budget = _CURRENT_BUDGET.get()
    if budget is not None:
        budget.consume_page()
