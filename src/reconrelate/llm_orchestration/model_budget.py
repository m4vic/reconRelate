"""Conservative run-wide admission budget for local and cloud model calls."""

from __future__ import annotations

from dataclasses import dataclass

from reconrelate.core.errors import ModelBudgetExceededError
from reconrelate.llm_orchestration.model_pricing import estimate_cloud_cost_microusd


@dataclass(frozen=True, slots=True)
class ModelReservation:
    input_tokens: int
    output_tokens: int
    cloud_tokens: int
    cloud_cost_microusd: int = 0


@dataclass(slots=True)
class ModelBudget:
    max_calls: int
    max_input_tokens: int
    max_output_tokens: int
    max_cloud_tokens: int
    max_cloud_cost_microusd: int = 0
    calls_reserved: int = 0
    input_tokens_reserved: int = 0
    output_tokens_reserved: int = 0
    cloud_tokens_reserved: int = 0
    cloud_cost_microusd_reserved: int = 0

    @staticmethod
    def estimate(
        *, input_text: str, output_tokens: int, cloud: bool, model: str = ""
    ) -> ModelReservation:
        # UTF-8 bytes are a conservative tokenizer-independent upper bound for ordinary model APIs.
        input_upper_bound = len(input_text.encode("utf-8"))
        requested_output = max(0, int(output_tokens))
        cloud_tokens = input_upper_bound + requested_output if cloud else 0
        cloud_cost = (
            estimate_cloud_cost_microusd(model, input_upper_bound, requested_output)
            if cloud else 0
        )
        return ModelReservation(input_upper_bound, requested_output, cloud_tokens, cloud_cost)

    def reserve(
        self, *, input_text: str, output_tokens: int, cloud: bool, model: str = ""
    ) -> ModelReservation:
        reservation = self.estimate(
            input_text=input_text, output_tokens=output_tokens, cloud=cloud, model=model
        )
        if self.calls_reserved + 1 > self.max_calls:
            raise ModelBudgetExceededError(f"model call ceiling {self.max_calls} exhausted")
        if self.input_tokens_reserved + reservation.input_tokens > self.max_input_tokens:
            raise ModelBudgetExceededError(
                f"model input-token ceiling {self.max_input_tokens} would be exceeded"
            )
        if self.output_tokens_reserved + reservation.output_tokens > self.max_output_tokens:
            raise ModelBudgetExceededError(
                f"model output-token ceiling {self.max_output_tokens} would be exceeded"
            )
        if cloud and self.cloud_tokens_reserved + reservation.cloud_tokens > self.max_cloud_tokens:
            raise ModelBudgetExceededError(
                f"cloud token ceiling {self.max_cloud_tokens} would be exceeded"
            )
        if (
            cloud
            and self.cloud_cost_microusd_reserved + reservation.cloud_cost_microusd
            > self.max_cloud_cost_microusd
        ):
            raise ModelBudgetExceededError(
                f"cloud cost ceiling {self.max_cloud_cost_microusd} microdollars would be exceeded"
            )
        self.calls_reserved += 1
        self.input_tokens_reserved += reservation.input_tokens
        self.output_tokens_reserved += reservation.output_tokens
        self.cloud_tokens_reserved += reservation.cloud_tokens
        self.cloud_cost_microusd_reserved += reservation.cloud_cost_microusd
        return reservation

    def snapshot(self) -> dict[str, int]:
        return {
            "calls_reserved": self.calls_reserved,
            "input_tokens_reserved": self.input_tokens_reserved,
            "output_tokens_reserved": self.output_tokens_reserved,
            "cloud_tokens_reserved": self.cloud_tokens_reserved,
            "cloud_cost_microusd_reserved": self.cloud_cost_microusd_reserved,
        }
