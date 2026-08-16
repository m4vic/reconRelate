"""
core/resilience.py

Waterfall fallback pattern for data providers.
If the primary source fails (timeout, error, empty result),
it silently tries the next one in the chain.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


def waterfall(providers: list[tuple[str, Callable[..., Any]]], *args, **kwargs) -> Any:
    """
    Try each provider in order. Return the first non-empty result.
    
    providers: list of (name, callable) tuples
    *args, **kwargs: passed to each callable
    
    Example:
        result = waterfall([
            ("crt.sh", lambda d: crtsh.search(d)),
            ("hackertarget", lambda d: ht.search(d)),
        ], "roche.com")
    """
    for name, fn in providers:
        try:
            result = fn(*args, **kwargs)
            if result:  # non-empty, non-None
                logger.info("✓ [%s] returned %d results", name, len(result) if hasattr(result, '__len__') else 1)
                return result
            else:
                logger.info("⊘ [%s] returned empty — trying next", name)
        except Exception as e:
            logger.warning("✗ [%s] failed: %s — trying next", name, e)
    
    logger.warning("All providers exhausted, returning empty result")
    return []
