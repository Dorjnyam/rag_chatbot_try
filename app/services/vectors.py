"""Shared dense-vector validation helpers."""
import math


def normalize_vector(values, dimensions):
    if values is None or len(values) != dimensions or any(not math.isfinite(value) for value in values):
        raise ValueError("Embedding has invalid dimensions or nonfinite values.")
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        raise ValueError("Embedding has zero norm.")
    return [value / norm for value in values]
