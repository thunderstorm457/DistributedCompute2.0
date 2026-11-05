from decimal import Decimal, InvalidOperation, ROUND_CEILING
from typing import Iterable, Tuple, Union


Number = Union[Decimal, int, float, str]
CREDIT_QUANTUM = Decimal("0.01")
SECONDS_PER_MINUTE = Decimal("60")


def _decimal(value: Number, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be a finite number")
    return result


def _credits(reference_cpu_seconds: Decimal, reserved_gib_seconds: Decimal) -> Decimal:
    if reference_cpu_seconds < 0 or reserved_gib_seconds < 0:
        raise ValueError("Resource usage cannot be negative")
    raw_credits = max(reference_cpu_seconds, reserved_gib_seconds) / SECONDS_PER_MINUTE
    return raw_credits.quantize(CREDIT_QUANTUM, rounding=ROUND_CEILING)


def quote_credits(
    vcpu_count: int,
    wall_seconds: Number,
    reserved_memory_gib: Number,
    benchmark_score: Number,
    reference_score: Number,
) -> Decimal:
    """Quote a fully utilized CPU and reserved-memory allocation."""
    duration = _decimal(wall_seconds, "wall_seconds")
    memory = _decimal(reserved_memory_gib, "reserved_memory_gib")
    score = _decimal(benchmark_score, "benchmark_score")
    reference = _decimal(reference_score, "reference_score")

    if isinstance(vcpu_count, bool) or not isinstance(vcpu_count, int) or vcpu_count < 1:
        raise ValueError("vcpu_count must be a positive integer")
    if duration <= 0 or memory <= 0 or score <= 0 or reference <= 0:
        raise ValueError("duration, memory, and benchmark scores must be positive")

    reference_cpu_seconds = Decimal(vcpu_count) * duration * score / reference
    reserved_gib_seconds = memory * duration
    return _credits(reference_cpu_seconds, reserved_gib_seconds)


def settle_credits(
    cpu_seconds: Number,
    reserved_gib_seconds: Number,
    benchmark_score: Number,
    reference_score: Number,
) -> Decimal:
    """Settle a completed allocation from measured CPU and reserved RAM time."""
    cpu_time = _decimal(cpu_seconds, "cpu_seconds")
    memory_time = _decimal(reserved_gib_seconds, "reserved_gib_seconds")
    score = _decimal(benchmark_score, "benchmark_score")
    reference = _decimal(reference_score, "reference_score")

    if score <= 0 or reference <= 0:
        raise ValueError("benchmark scores must be positive")

    reference_cpu_seconds = cpu_time * score / reference
    return _credits(reference_cpu_seconds, memory_time)


def quote_distributed_credits(
    allocations: Iterable[Tuple[int, Number, Number, Number, Number]],
) -> Decimal:
    """Sum quotes for per-node (vCPUs, duration, RAM, score, reference) allocations."""
    return sum(
        (
            quote_credits(vcpus, duration, memory, score, reference)
            for vcpus, duration, memory, score, reference in allocations
        ),
        Decimal("0.00"),
    )


def settle_distributed_credits(
    allocations: Iterable[Tuple[Number, Number, Number, Number]],
) -> Decimal:
    """Sum settlements for per-node (CPU seconds, RAM seconds, score, reference) usage."""
    return sum(
        (
            settle_credits(cpu, memory, score, reference)
            for cpu, memory, score, reference in allocations
        ),
        Decimal("0.00"),
    )