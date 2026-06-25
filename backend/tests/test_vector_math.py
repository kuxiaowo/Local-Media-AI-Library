import pytest

from app.services.vector_math import cosine_similarity


def test_cosine_similarity_for_same_vector() -> None:
    assert cosine_similarity([1.0, 0.0, 1.0], [1.0, 0.0, 1.0]) == pytest.approx(1.0)


def test_cosine_similarity_handles_empty_or_mismatched_vectors() -> None:
    assert cosine_similarity([], [1.0]) == 0.0
    assert cosine_similarity([1.0, 2.0], [1.0]) == 0.0


def test_cosine_similarity_handles_zero_norm() -> None:
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0
