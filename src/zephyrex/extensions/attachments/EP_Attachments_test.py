# SPDX-License-Identifier: AGPL-3.0-or-later
"""Attachments against a real server: uploading a picture, reading it back, attaching it to a
record (a team here) and who may do what. The cloud extension's storage is stood in for by a store
in memory (its own tests cover its providers); what is tested is attachments' own rules: a picture
reads wherever its record does, never elsewhere, and only someone who may change a record attaches
to it."""

import hashlib
import uuid
from typing import Any, Dict

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.attachments.EXT_Attachments import EXT_Attachments
from zephyrex.extensions.cloud.EXT_Cloud import EXT_Cloud
from zephyrex.lib.Environment import env
from zephyrex.testing.factories import add_user_to_team, create_team, if_match_of

PNG = b"\x89PNG\r\n\x1a\n" + b"a small map" * 4


def auth(user: Any) -> Dict[str, str]:
    return {"Authorization": f"Bearer {user.jwt}"}


class TestAttachments(ExtensionServerMixin):
    extension_class = EXT_Attachments

    @pytest.fixture(autouse=True)
    def store(self, monkeypatch: pytest.MonkeyPatch) -> Dict[str, bytes]:
        """The cloud extension's upload and download, kept in memory."""
        files: Dict[str, bytes] = {}

        async def upload_file(
            cls: Any, path: str, content: bytes, provider: Any = None
        ) -> Dict[str, Any]:
            files[path] = content
            return {"provider": "memory", "path": path}

        async def download_file(cls: Any, provider: str, path: str) -> bytes:
            return files[path]

        monkeypatch.setattr(EXT_Cloud, "upload_file", classmethod(upload_file))
        monkeypatch.setattr(EXT_Cloud, "download_file", classmethod(download_file))
        return files

    @staticmethod
    def _upload(
        server: Any,
        user: Any,
        content: bytes = PNG,
        kind: str = "image/png",
        name: str = "map.png",
    ) -> Any:
        return server.post(
            "/v1/asset/upload",
            params={"name": name},
            content=content,
            headers={**auth(user), "Content-Type": kind},
        )

    @pytest.fixture
    def table(self, server: Any, admin_a: Any, user_b: Any) -> Dict[str, Any]:
        """admin_a's team, with user_b a member (so user_b can view it, admin_b can't)."""
        team = create_team(server, admin_a.id, name=f"Table {uuid.uuid4().hex[:8]}")
        add_user_to_team(server, user_b.id, team.id, env("USER_ROLE_ID"))
        return {"id": team.id}

    def _attach(
        self,
        server: Any,
        user: Any,
        asset_id: str,
        team_id: str,
        role: str = "primary_image",
    ) -> Any:
        return server.post(
            "/v1/attachment",
            json={
                "attachment": {
                    "target_entity_type": "team",
                    "target_entity_id": team_id,
                    "asset_id": asset_id,
                    "role": role,
                }
            },
            headers=auth(user),
        )

    def test_an_upload_is_stored_and_read_back_by_its_uploader(
        self, server: Any, admin_a: Any
    ) -> None:
        made = self._upload(server, admin_a)
        assert made.status_code == 201, made.text
        asset = made.json()["asset"]
        assert (asset["size"], asset["sha256"], asset["content_type"]) == (
            len(PNG),
            hashlib.sha256(PNG).hexdigest(),
            "image/png",
        )
        read = server.get(f"/v1/asset/{asset['id']}/content", headers=auth(admin_a))
        assert read.status_code == 200, read.text
        assert read.content == PNG
        assert read.headers["content-type"] == "image/png"

    def test_uploads_that_are_refused(self, server: Any, admin_a: Any) -> None:
        assert self._upload(server, admin_a, kind="text/html").status_code == 415
        assert self._upload(server, admin_a, content=b"").status_code == 422
        assert self._upload(server, admin_a, name=" ").status_code == 422
        assert (
            server.post(
                "/v1/asset/upload", params={"name": "x.png"}, content=PNG
            ).status_code
            == 401
        )

    def test_a_picture_reads_wherever_its_record_does(
        self,
        server: Any,
        admin_a: Any,
        admin_b: Any,
        user_b: Any,
        table: Dict[str, Any],
    ) -> None:
        asset = self._upload(server, admin_a).json()["asset"]
        # Before it's attached, it's its uploader's alone.
        assert (
            server.get(
                f"/v1/asset/{asset['id']}/content", headers=auth(user_b)
            ).status_code
            == 404
        )
        attached = self._attach(server, admin_a, asset["id"], table["id"])
        assert attached.status_code == 201, attached.text

        # A member of the team reads the team's attachments and the picture itself.
        listed = server.get(
            "/v1/attachment/of",
            params={"target_entity_type": "team", "target_entity_id": table["id"]},
            headers=auth(user_b),
        )
        assert listed.status_code == 200, listed.text
        assert [row["asset_id"] for row in listed.json()["attachments"]] == [
            asset["id"]
        ]
        # It isn't user_b's asset, yet they see what the file is: its name, type and size.
        assert listed.json()["attachments"][0]["asset"] == {
            "name": "map.png",
            "content_type": "image/png",
            "size": len(PNG),
        }
        assert (
            server.get(f"/v1/asset/{asset['id']}/content", headers=auth(user_b)).content
            == PNG
        )

        # Someone outside the team reads neither, with the same answer as for nothing at all.
        outside = server.get(
            "/v1/attachment/of",
            params={"target_entity_type": "team", "target_entity_id": table["id"]},
            headers=auth(admin_b),
        )
        assert outside.status_code == 404
        assert (
            server.get(
                f"/v1/asset/{asset['id']}/content", headers=auth(admin_b)
            ).status_code
            == 404
        )

    def test_only_someone_who_may_change_a_record_attaches_to_it(
        self, server: Any, admin_a: Any, admin_b: Any, table: Dict[str, Any]
    ) -> None:
        theirs = self._upload(server, admin_b).json()["asset"]
        # admin_b can't change admin_a's team: the same 404 as for a team that isn't there.
        assert (
            self._attach(server, admin_b, theirs["id"], table["id"]).status_code == 404
        )
        assert (
            self._attach(server, admin_b, theirs["id"], str(uuid.uuid4())).status_code
            == 404
        )
        # Nor attach someone else's asset to their own record.
        mine = self._upload(server, admin_a).json()["asset"]
        own_team = create_team(server, admin_b.id, name=f"Other {uuid.uuid4().hex[:8]}")
        assert self._attach(server, admin_b, mine["id"], own_team.id).status_code == 404
        # A role is a short snake_case name, and the type must be a loaded record type.
        assert (
            self._attach(
                server, admin_a, mine["id"], table["id"], role="Primary Image"
            ).status_code
            == 422
        )
        unknown = server.post(
            "/v1/attachment",
            json={
                "attachment": {
                    "target_entity_type": "no_such_thing",
                    "target_entity_id": "x",
                    "asset_id": mine["id"],
                }
            },
            headers=auth(admin_a),
        )
        assert unknown.status_code == 422

    def test_several_attach_in_one_call_each_checked_first(
        self, server: Any, admin_a: Any, admin_b: Any, table: Dict[str, Any]
    ) -> None:
        """``create(entities=[...])`` (a scenario's pictures copied in bulk)
        checks every attachment as a single one is checked, before writing
        any: one asset that isn't the requester's refuses the lot."""
        from fastapi import HTTPException

        from zephyrex.extensions.attachments.BLL_Attachments import (
            AttachmentManager,
        )

        registry = server.app.state.model_registry
        manager = AttachmentManager(requester_id=admin_a.id, model_registry=registry)
        mine = [self._upload(server, admin_a).json()["asset"]["id"] for _ in range(2)]
        theirs = self._upload(server, admin_b).json()["asset"]["id"]
        target = {"target_entity_type": "team", "target_entity_id": table["id"]}

        with pytest.raises(HTTPException) as refused:
            manager.create(
                entities=[{"asset_id": mine[0]}, {"asset_id": theirs, "role": "token"}],
                **target,
            )
        assert refused.value.status_code == 404
        assert manager.list(target_entity_id=table["id"]) == []

        created = manager.create(
            entities=[
                {"asset_id": mine[0]},
                {"asset_id": mine[1], "role": "token"},
            ],
            **target,
        )
        assert sorted((row.asset_id, row.role) for row in created) == sorted(
            [(mine[0], "primary_image"), (mine[1], "token")]
        )
        listed = server.get(
            "/v1/attachment/of",
            params={"target_entity_type": "team", "target_entity_id": table["id"]},
            headers=auth(admin_a),
        )
        assert listed.status_code == 200, listed.text
        assert len(listed.json()["attachments"]) == 2

    def test_deleting_an_asset_removes_its_attachments(
        self, server: Any, admin_a: Any, table: Dict[str, Any]
    ) -> None:
        asset = self._upload(server, admin_a).json()["asset"]
        assert (
            self._attach(
                server, admin_a, asset["id"], table["id"], role="header_image"
            ).status_code
            == 201
        )
        from zephyrex.extensions.attachments.BLL_Attachments import AttachmentManager

        links = AttachmentManager(
            requester_id=env("SYSTEM_ID"),
            model_registry=server.app.state.model_registry,
        )
        assert len(links.list(asset_id=asset["id"])) == 1
        # Straight through the manager first (a second asset), then through the route.
        from zephyrex.extensions.attachments.BLL_Attachments import AssetManager

        other = self._upload(server, admin_a).json()["asset"]
        assert (
            self._attach(
                server, admin_a, other["id"], table["id"], role="map"
            ).status_code
            == 201
        )
        AssetManager(
            requester_id=admin_a.id, model_registry=server.app.state.model_registry
        ).delete(other["id"])
        assert (
            links.list(asset_id=other["id"]) == []
        ), "the manager's own delete left the rows"
        gone = server.delete(
            f"/v1/asset/{asset['id']}", headers={**auth(admin_a), **if_match_of(asset)}
        )
        assert gone.status_code == 204, gone.text
        # The rows themselves go, not only out of sight.
        from zephyrex.extensions.attachments.BLL_Attachments import AssetModel

        remaining = links.list(asset_id=asset["id"])
        assert (
            remaining == []
        ), f"AssetModel.Manager is {getattr(AssetModel, 'Manager', None)!r}; left {remaining!r}"
        left = server.get(
            "/v1/attachment/of",
            params={
                "target_entity_type": "team",
                "target_entity_id": table["id"],
                "role": "header_image",
            },
            headers=auth(admin_a),
        )
        assert left.status_code == 200, left.text
        assert left.json()["attachments"] == []

    def test_an_asset_gone_without_its_rows_drops_out_of_the_list(
        self, server: Any, admin_a: Any, user_b: Any, table: Dict[str, Any]
    ) -> None:
        from zephyrex.extensions.attachments.BLL_Attachments import AssetManager
        from zephyrex.logic.AbstractLogicManager import AbstractBLLManager

        kept = self._upload(server, admin_a, name="kept.pdf", kind="application/pdf")
        lost = self._upload(server, admin_a, name="lost.png")
        for made in (kept, lost):
            assert (
                self._attach(
                    server,
                    admin_a,
                    made.json()["asset"]["id"],
                    table["id"],
                    role="document",
                ).status_code
                == 201
            )
        # The asset row alone, past AssetManager.delete's removal of its attachments.
        AbstractBLLManager.delete(
            AssetManager(
                requester_id=admin_a.id, model_registry=server.app.state.model_registry
            ),
            lost.json()["asset"]["id"],
        )
        listed = server.get(
            "/v1/attachment/of",
            params={"target_entity_type": "team", "target_entity_id": table["id"]},
            headers=auth(user_b),
        )
        assert listed.status_code == 200, listed.text
        assert [row["asset"] for row in listed.json()["attachments"]] == [
            {"name": "kept.pdf", "content_type": "application/pdf", "size": len(PNG)}
        ]
