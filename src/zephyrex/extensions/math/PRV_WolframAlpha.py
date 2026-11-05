# SPDX-License-Identifier: AGPL-3.0-or-later
"""Wolfram|Alpha's Full Results API. The instance's API key is a
Wolfram|Alpha AppID (else ``WOLFRAM_ALPHA_APPID``). Each operation is
phrased as a query and its answer read from the result pods."""

from typing import Any, Callable, ClassVar, Dict, List, Optional, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.extensions.math.EXT_Math import AbstractMathProvider
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

QUERY_URL = "https://api.wolframalpha.com/v2/query"
# Full Results API error codes for a missing or refused AppID.
_APPID_ERRORS = {"1", "2"}
_INPUT_PODS = {"Input", "Input interpretation"}

Pod = Dict[str, Any]


def pod_texts(pods: List[Pod], wanted: Callable[[Pod], bool]) -> List[str]:
    """The plain text of every subpod of the pods ``wanted`` picks."""
    return [
        str(subpod["plaintext"])
        for pod in pods
        if wanted(pod)
        for subpod in pod.get("subpods", [])
        if subpod.get("plaintext")
    ]


def main_text(pods: List[Pod]) -> str:
    """The primary pod's text, else the first pod after the input echo."""
    texts = pod_texts(pods, lambda pod: bool(pod.get("primary")))
    if not texts:
        texts = pod_texts(pods, lambda pod: pod.get("title") not in _INPUT_PODS)
    return texts[0] if texts else ""


def titled(*words: str) -> Callable[[Pod], bool]:
    return lambda pod: any(word in str(pod.get("title", "")).lower() for word in words)


class PRV_WolframAlpha_Math(AbstractMathProvider):
    name: ClassVar[str] = "wolfram_alpha"
    friendly_name: ClassVar[str] = "Wolfram|Alpha"
    description: ClassVar[str] = "Wolfram|Alpha computational knowledge engine"
    _env: ClassVar[Dict[str, Any]] = {"WOLFRAM_ALPHA_APPID": ""}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "Wolfram|Alpha AppID",
            env="WOLFRAM_ALPHA_APPID",
            secret=True,
            field="api_key",
        ),
    )

    @classmethod
    async def _pods(cls, instance: ProviderInstanceModel, query: str) -> List[Pod]:
        appid = cls.setting(instance, "api_key")
        if not appid:
            raise TransientExternalError(
                "Wolfram|Alpha AppID not configured", provider=cls.name
            )
        answer = await cls.get_json(
            QUERY_URL, {"appid": appid, "input": query, "output": "json"}
        )
        result = answer.get("queryresult", {})
        error = result.get("error")
        if error:
            code = str(error.get("code", "")) if isinstance(error, dict) else ""
            detail = error.get("msg", "") if isinstance(error, dict) else ""
            if code in _APPID_ERRORS:
                raise AuthExternalError(f"Wolfram|Alpha: {detail}", provider=cls.name)
            raise TransientExternalError(
                f"Wolfram|Alpha error {code}: {detail}", provider=cls.name
            )
        if not result.get("success"):
            raise InvalidInputExternalError(
                f"Wolfram|Alpha could not interpret {query!r}", provider=cls.name
            )
        pods: List[Pod] = result.get("pods", [])
        return pods

    @classmethod
    async def calculate(
        cls, instance: ProviderInstanceModel, expression: str
    ) -> Dict[str, Any]:
        pods = await cls._pods(instance, expression)
        decimal = pod_texts(pods, titled("decimal approximation"))
        result = main_text(pods)
        return {
            "result": result,
            "decimal": decimal[0] if decimal else result,
            "provider": cls.name,
        }

    @classmethod
    async def solve(
        cls, instance: ProviderInstanceModel, equation: str, variable: Optional[str]
    ) -> Dict[str, Any]:
        query = f"solve {equation} for {variable}" if variable else f"solve {equation}"
        pods = await cls._pods(instance, query)
        return {
            "variable": variable or "",
            "solutions": pod_texts(pods, titled("solution")),
            "provider": cls.name,
        }

    @classmethod
    async def differentiate(
        cls, instance: ProviderInstanceModel, expression: str, variable: str, order: int
    ) -> Dict[str, Any]:
        operator = f"d/d{variable}" if order == 1 else f"d^{order}/d{variable}^{order}"
        pods = await cls._pods(instance, f"{operator} ({expression})")
        derivative = pod_texts(pods, titled("derivative"))
        return {
            "result": derivative[0] if derivative else main_text(pods),
            "provider": cls.name,
        }

    @classmethod
    async def integrate(
        cls,
        instance: ProviderInstanceModel,
        expression: str,
        variable: str,
        lower: Optional[str],
        upper: Optional[str],
    ) -> Dict[str, Any]:
        query = f"integrate {expression} d{variable}"
        if lower is not None and upper is not None:
            query += f" from {lower} to {upper}"
        pods = await cls._pods(instance, query)
        integral = pod_texts(pods, titled("integral"))
        return {
            "result": integral[0] if integral else main_text(pods),
            "evaluated": bool(integral),
            "provider": cls.name,
        }
