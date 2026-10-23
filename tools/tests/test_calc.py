import pytest
from agentlens_tools.calc import CalcError, safe_eval


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("1 + 2 * 3", 7),
        ("(1 + 2) * 3", 9),
        ("383,285 - 394,328", -11043),
        ("pct_change(394328, 383285)", pytest.approx(-2.8007, rel=1e-3)),
        ("cagr(100, 121, 2)", pytest.approx(10.0)),
        ("round(2.567, 1)", 2.6),
        ("-5 + +3", -2),
        ("2 ** 10", 1024),
    ],
)
def test_valid(expr, expected):
    assert safe_eval(expr) == expected


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('echo hi')",
        "open('/etc/passwd')",
        "().__class__",
        "1/0",
        "2 ** 100000",
        "x + 1",
        "abs(1, 2)",
        "True + 1",
        "'a' * 3",
        "1 +",
        "9" * 600,
        "sqrt(-1)",
    ],
)
def test_rejected(expr):
    with pytest.raises(CalcError):
        safe_eval(expr)


def test_server_registers_tool():
    from agentlens_tools.calc import build_server

    assert build_server().name == "agentlens-calc"
