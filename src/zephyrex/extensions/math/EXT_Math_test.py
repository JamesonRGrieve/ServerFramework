# SPDX-License-Identifier: AGPL-3.0-or-later
"""The math extension: argument checks, statistics, graphs, SymPy through
the rotation, Wolfram|Alpha's pod reading, its refusal of a bad AppID
(a real call; xfails offline), and live queries with a test AppID."""

import base64

import httpx
import pytest

from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    PermanentExternalError,
    TransientExternalError,
)
from zephyrex.extensions.math.EXT_Math import (
    MAX_DATA_POINTS,
    EXT_Math,
    checked_variable,
    describe,
    line_graph,
)
from zephyrex.extensions.math.PRV_SymPy import PRV_SymPy_Math
from zephyrex.extensions.math.PRV_WolframAlpha import (
    PRV_WolframAlpha_Math,
    main_text,
    pod_texts,
    titled,
)
from zephyrex.extensions.math.Symbolic import SymbolicWorker

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# The shape of a Full Results answer for "x^2 = 4" (abridged).
SOLVE_PODS = [
    {"title": "Input", "subpods": [{"plaintext": "x^2 = 4"}]},
    {"title": "Plot", "subpods": [{"plaintext": ""}]},
    {
        "title": "Real solutions",
        "primary": True,
        "subpods": [{"plaintext": "x = -2"}, {"plaintext": "x = 2"}],
    },
]


def _online(url: str) -> bool:
    try:
        httpx.head(url, timeout=5)
        return True
    except httpx.HTTPError:
        return False


@pytest.fixture
def sympy_rotation(provider_instance, rotation_over, monkeypatch):
    monkeypatch.setattr(
        EXT_Math,
        "_root_rotation_cache",
        rotation_over(provider_instance(PRV_SymPy_Math)),
    )


class TestExtension:
    def test_providers(self):
        assert {p.name for p in EXT_Math.providers} == {"sympy", "wolfram_alpha"}

    def test_abilities(self):
        assert {
            "calculate_expression",
            "solve_equation",
            "differentiate",
            "integrate",
            "analyze_statistics",
            "create_graph",
        } <= set(EXT_Math.get_abilities())

    @pytest.mark.parametrize("name", ["x", "theta", "x_1"])
    def test_variable_names(self, name):
        assert checked_variable(name) == name

    @pytest.mark.parametrize("name", ["", "1x", "x y", "__class__", "x" * 17])
    def test_a_bad_variable_name_is_refused(self, name):
        with pytest.raises(InvalidInputExternalError):
            checked_variable(name)

    async def test_argument_checks_come_before_any_provider(self):
        with pytest.raises(InvalidInputExternalError):
            await EXT_Math.differentiate("x^2", order=0)
        with pytest.raises(InvalidInputExternalError):
            await EXT_Math.integrate("x", lower="0")
        with pytest.raises(InvalidInputExternalError):
            await EXT_Math.calculate_expression("   ")


class TestStatistics:
    def test_describe(self):
        stats = describe([2, 4, 4, 4, 5, 5, 7, 9])
        assert stats["count"] == 8 and stats["sum"] == 40
        assert stats["mean"] == 5 and stats["median"] == 4.5
        assert stats["min"] == 2 and stats["max"] == 9
        assert stats["population_std_dev"] == 2
        assert stats["sample_variance"] == pytest.approx(32 / 7)

    def test_a_single_value_has_no_sample_spread(self):
        stats = describe([3])
        assert stats["population_std_dev"] == 0
        assert stats["sample_std_dev"] is None

    @pytest.mark.parametrize(
        "data", [[], [1, float("nan")], [float("inf")], [0.0] * (MAX_DATA_POINTS + 1)]
    )
    def test_refused_data(self, data):
        with pytest.raises(InvalidInputExternalError):
            describe(data)

    async def test_the_ability(self):
        assert (await EXT_Math.analyze_statistics([1, 2, 3]))["mean"] == 2


class TestGraph:
    def test_a_png(self):
        assert line_graph([0, 1, 2], [0, 1, 4], "Squares").startswith(PNG_SIGNATURE)

    @pytest.mark.parametrize(
        "x_data, y_data, title",
        [
            ([0, 1], [0], "t"),
            ([], [], "t"),
            ([0], [float("nan")], "t"),
            ([0], [0], "t" * 201),
        ],
    )
    def test_refused(self, x_data, y_data, title):
        with pytest.raises(InvalidInputExternalError):
            line_graph(x_data, y_data, title)

    async def test_the_ability_returns_the_image_not_a_server_path(self):
        graph = await EXT_Math.create_graph([0, 1], [1, 0], "Line")
        assert graph["media_type"] == "image/png"
        assert base64.b64decode(graph["image_base64"]).startswith(PNG_SIGNATURE)


class TestSymPyThroughTheRotation:
    async def test_calculate(self, sympy_rotation):
        assert await EXT_Math.calculate_expression("2^10 + sqrt(4)") == {
            "result": "1026",
            "decimal": "1026.00000000000",
            "provider": "sympy",
        }

    async def test_solve(self, sympy_rotation):
        solved = await EXT_Math.solve_equation("x^2 - 5*x + 6 = 0")
        assert solved["solutions"] == ["2", "3"]

    async def test_calculus(self, sympy_rotation):
        assert (await EXT_Math.differentiate("sin(x)"))["result"] == "cos(x)"
        assert (await EXT_Math.integrate("x", lower="0", upper="2"))["result"] == "2"

    async def test_injected_code_is_refused_not_run(self, sympy_rotation):
        """The old solve_equation handed this to sympify, which ran it."""
        with pytest.raises(InvalidInputExternalError):
            await EXT_Math.solve_equation("__import__('os').getpid()")

    async def test_a_runaway_computation_is_stopped(self, sympy_rotation, monkeypatch):
        """The old code ran SymPy on the event loop with no limit."""
        monkeypatch.setattr(
            PRV_SymPy_Math, "_worker", SymbolicWorker(timeout_seconds=2.0)
        )
        try:
            with pytest.raises(PermanentExternalError):
                await EXT_Math.integrate("exp(x^x)*sin(x)^7/(1+x^9)")
        finally:
            PRV_SymPy_Math._worker.stop()


class TestWolframPods:
    def test_solutions_come_from_every_solution_subpod(self):
        assert pod_texts(SOLVE_PODS, titled("solution")) == ["x = -2", "x = 2"]

    def test_the_primary_pod_is_the_main_answer(self):
        assert main_text(SOLVE_PODS) == "x = -2"

    def test_without_a_primary_pod_the_input_echo_is_skipped(self):
        pods = [
            {"title": "Input", "subpods": [{"plaintext": "2+2"}]},
            {"title": "Result", "subpods": [{"plaintext": "4"}]},
        ]
        assert main_text(pods) == "4"


class TestWolframAlpha:
    async def test_without_an_appid(self, provider_instance, set_env):
        set_env("WOLFRAM_ALPHA_APPID", "")
        with pytest.raises(TransientExternalError):
            await PRV_WolframAlpha_Math.calculate(
                provider_instance(PRV_WolframAlpha_Math), "2+2"
            )

    @pytest.mark.xfail(
        not _online("https://api.wolframalpha.com"),
        reason="Wolfram|Alpha is unreachable",
    )
    async def test_a_bad_appid_is_refused(self, provider_instance):
        instance = provider_instance(PRV_WolframAlpha_Math, api_key="NOT-A-REAL-APPID")
        with pytest.raises(AuthExternalError):
            await PRV_WolframAlpha_Math.calculate(instance, "2+2")


@pytest.mark.external_api(provider="wolfram_alpha")
class TestWolframAlphaLive:
    @pytest.fixture
    def instance(self, provider_instance, sandbox_credentials_for):
        return provider_instance(
            PRV_WolframAlpha_Math,
            api_key=sandbox_credentials_for("wolfram_alpha")["WOLFRAM_ALPHA_APPID"],
        )

    async def test_calculate_and_solve(self, instance):
        assert (await PRV_WolframAlpha_Math.calculate(instance, "6*7"))[
            "result"
        ] == "42"
        solved = await PRV_WolframAlpha_Math.solve(instance, "x^2 = 4", "x")
        assert {"x = -2", "x = 2"} <= set(solved["solutions"])

    async def test_calculus(self, instance):
        derivative = await PRV_WolframAlpha_Math.differentiate(instance, "x^3", "x", 1)
        assert "3 x^2" in derivative["result"]
        integral = await PRV_WolframAlpha_Math.integrate(instance, "x^2", "x", "0", "3")
        assert integral["evaluated"] and "9" in integral["result"]
