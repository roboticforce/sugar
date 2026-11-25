"""Optional model pricing for usage cost estimates (never invented).

Sugar does not ship vendor prices and never guesses: ``cost_usd`` in usage
payloads stays harness-reported. If the operator provides a price table, the
usage aggregator can additionally show an **estimated** cost from token counts.

Price table location:

* ``$SUGAR_MODEL_PRICES`` — explicit JSON file path;
* otherwise ``~/.sugar/model-prices.json``.

Format (USD per million tokens)::

    {
      "deepseek-flash": {"input_per_mtok": 0.14, "output_per_mtok": 0.28},
      "claude-sonnet": {"input_per_mtok": 3.0, "output_per_mtok": 15.0}
    }

Missing file/models mean "no estimate" (never an error for the dashboard).
Malformed JSON/entries are ignored defensively. ``estimated_cost_usd`` is always
derived from the configured table; reported ``cost_usd`` is never overwritten.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

ENV_MODEL_PRICES = "SUGAR_MODEL_PRICES"
DEFAULT_PRICE_FILENAME = "model-prices.json"
ESTIMATE_PRECISION = 6


def default_price_path() -> Path:
    """Default operator price table: ``~/.sugar/model-prices.json``."""
    return Path.home() / ".sugar" / DEFAULT_PRICE_FILENAME


def resolve_price_path(explicit: Any = None) -> Path:
    """Resolve the price-table path (explicit -> env -> default)."""
    if explicit is not None and str(explicit).strip():
        return Path(str(explicit).strip()).expanduser()
    env = os.environ.get(ENV_MODEL_PRICES, "").strip()
    if env:
        return Path(env).expanduser()
    return default_price_path()


@dataclass(frozen=True)
class ModelPrice:
    """One model's USD-per-million-token rates."""

    input_per_mtok: float
    output_per_mtok: float


def _as_rate(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _parse_entry(value: Any) -> Optional[ModelPrice]:
    if not isinstance(value, Mapping):
        return None
    input_rate = _as_rate(value.get("input_per_mtok"))
    output_rate = _as_rate(value.get("output_per_mtok"))
    if input_rate is None or output_rate is None:
        return None
    return ModelPrice(input_rate, output_rate)


def parse_price_table(value: Any) -> Dict[str, ModelPrice]:
    """Parse a decoded price-table object defensively (invalid entries ignored)."""
    if not isinstance(value, Mapping):
        return {}
    prices: Dict[str, ModelPrice] = {}
    for model, entry in value.items():
        if not isinstance(model, str):
            continue
        key = model.strip()
        if not key:
            continue
        price = _parse_entry(entry)
        if price is not None:
            prices[key] = price
    return prices


class PriceTable:
    """Loaded operator price table; an empty table means no estimates."""

    def __init__(
        self,
        prices: Optional[Mapping[str, ModelPrice]] = None,
        *,
        source: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        self.prices: Dict[str, ModelPrice] = dict(prices or {})
        self.source = source
        self.error = error
        self._lower = {key.lower(): key for key in self.prices}

    def __bool__(self) -> bool:
        return bool(self.prices)

    def __len__(self) -> int:
        return len(self.prices)

    def get(self, model: Any) -> Optional[ModelPrice]:
        if not isinstance(model, str):
            return None
        text = model.strip()
        if not text:
            return None
        price = self.prices.get(text)
        if price is None:
            canonical = self._lower.get(text.lower())
            if canonical is not None:
                price = self.prices.get(canonical)
        return price

    def estimate_cost(
        self, model: Any, tokens_in: Any, tokens_out: Any
    ) -> Optional[float]:
        """Estimate USD from token counts; ``None`` when the model is unpriced."""
        price = self.get(model)
        if price is None:
            return None
        try:
            input_tokens = max(0, int(tokens_in or 0))
            output_tokens = max(0, int(tokens_out or 0))
        except (TypeError, ValueError):
            return None
        return round(
            input_tokens / 1_000_000 * price.input_per_mtok
            + output_tokens / 1_000_000 * price.output_per_mtok,
            ESTIMATE_PRECISION,
        )


def load_price_table(path: Any = None) -> PriceTable:
    """Load the operator price table; missing/malformed files yield an empty table."""
    resolved = resolve_price_path(path)
    try:
        raw = resolved.read_text(encoding="utf-8")
    except FileNotFoundError:
        return PriceTable(source=str(resolved))
    except OSError as exc:
        logger.debug("pricing: cannot read %s: %s", resolved, exc)
        return PriceTable(source=str(resolved), error=str(exc))
    try:
        decoded = json.loads(raw)
    except ValueError:
        logger.debug("pricing: invalid JSON in %s", resolved)
        return PriceTable(source=str(resolved), error="invalid JSON")
    return PriceTable(parse_price_table(decoded), source=str(resolved))


def estimate_cost(
    model: Any, tokens_in: Any, tokens_out: Any, *, table: Optional[PriceTable] = None
) -> Optional[float]:
    """Convenience wrapper: estimate one event with the resolved price table."""
    price_table = table if table is not None else load_price_table()
    return price_table.estimate_cost(model, tokens_in, tokens_out)


__all__ = [
    "DEFAULT_PRICE_FILENAME",
    "ENV_MODEL_PRICES",
    "ModelPrice",
    "PriceTable",
    "default_price_path",
    "estimate_cost",
    "load_price_table",
    "parse_price_table",
    "resolve_price_path",
]
