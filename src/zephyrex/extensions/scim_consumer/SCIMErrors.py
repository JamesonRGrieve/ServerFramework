# SPDX-License-Identifier: AGPL-3.0-or-later
"""SCIM 2.0 protocol errors (RFC 7644 §3.12): an HTTP status, an optional
``scimType`` naming the kind of failure, and a detail for the client."""

from typing import Any, Dict, Optional

ERROR_URN = "urn:ietf:params:scim:api:messages:2.0:Error"

# The scimType keywords this server uses (RFC 7644 table 9).
INVALID_FILTER = "invalidFilter"
INVALID_PATH = "invalidPath"
INVALID_SYNTAX = "invalidSyntax"
INVALID_VALUE = "invalidValue"
MUTABILITY = "mutability"
NO_TARGET = "noTarget"
TOO_MANY = "tooMany"
UNIQUENESS = "uniqueness"


class ScimError(Exception):
    """A request the SCIM endpoints refuse, answered as a SCIM Error."""

    def __init__(
        self,
        status: int,
        detail: str,
        scim_type: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.scim_type = scim_type
        self.headers: Dict[str, str] = dict(headers or {})

    def body(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "schemas": [ERROR_URN],
            "status": str(self.status),
            "detail": self.detail,
        }
        if self.scim_type:
            payload["scimType"] = self.scim_type
        return payload


def bad_request(scim_type: str, detail: str) -> ScimError:
    return ScimError(400, detail, scim_type)
