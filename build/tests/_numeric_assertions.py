"""Compare fixed numerical baselines across NumPy/SciPy and BLAS builds."""

import math


def assert_numeric_baseline(actual, expected, *, rel_tol=1e-11, abs_tol=1e-14, path="result"):
    """Compare floating point leaves while requiring the original result structure."""
    assert type(actual) is type(expected), (
        f"{path}: expected {type(expected).__name__}, got {type(actual).__name__}"
    )
    tolerances = {"rel_tol": rel_tol, "abs_tol": abs_tol}
    if isinstance(expected, dict):
        assert actual.keys() == expected.keys(), f"{path}: dictionary keys differ"
        for key, value in expected.items():
            assert_numeric_baseline(actual[key], value, path=f"{path}[{key!r}]", **tolerances)
    elif isinstance(expected, (list, tuple)):
        assert len(actual) == len(expected), f"{path}: sequence lengths differ"
        for index, value in enumerate(expected):
            assert_numeric_baseline(actual[index], value, path=f"{path}[{index}]", **tolerances)
    elif isinstance(expected, float):
        assert math.isclose(actual, expected, **tolerances), (
            f"{path}: {actual!r} differs from {expected!r} "
            f"(rel_tol={rel_tol:g}, abs_tol={abs_tol:g})"
        )
    else:
        assert actual == expected, f"{path}: {actual!r} != {expected!r}"
