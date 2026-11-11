import pytest

from aegis_ai.builtins import calculate


def test_calculator_handles_arithmetic_and_math_functions():
    assert calculate("19 * 23") == 437
    assert calculate("sqrt(81) + pi") == pytest.approx(12.141592653589793)


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('echo unsafe')",
        "open('/etc/passwd').read()",
        "(1).__class__",
        "True + 1",
        "2 ** 101",
        "1 / 0",
    ],
)
def test_calculator_rejects_unsafe_or_invalid_expressions(expression):
    with pytest.raises((ValueError, TypeError, ZeroDivisionError, OverflowError)):
        calculate(expression)
