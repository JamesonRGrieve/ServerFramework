# SPDX-License-Identifier: AGPL-3.0-or-later
"""Solana, through a JSON-RPC node: ``rpc_url`` decides the cluster
(mainnet-beta by default; devnet and testnet for testing).

The instance's API key is the wallet's secret key (base58, as Solana
wallets export it); without one, its ``address`` setting makes it
watch-only. Payments are signed here; only the signed transaction
reaches the node. Token balances are SPL tokens, by mint address.
"""

import json
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.crypto.EXT_Crypto import (
    CRYPTO_REQUEST_TIMEOUT_SECONDS,
    AbstractCryptoProvider,
    from_units,
)
from zephyrex.extensions.ExternalErrors import (
    BaseExternalError,
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

DEFAULT_RPC_URL = "https://api.mainnet-beta.solana.com"


class PRV_Solana_Crypto(AbstractCryptoProvider):
    name: ClassVar[str] = "solana"
    friendly_name: ClassVar[str] = "Solana"
    description: ClassVar[str] = "A Solana wallet"
    symbol: ClassVar[str] = "SOL"
    decimals: ClassVar[int] = 9
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="solana",
                friendly_name="solana-py",
                semver=">=0.40",
                reason="Solana JSON-RPC",
            ),
            PIP_Dependency(
                name="solders",
                friendly_name="solders",
                semver=">=0.29",
                reason="Solana keys, messages and signing",
            ),
        ]
    )
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting("api_key", "Secret key (base58)", secret=True, field="api_key"),
        InstanceSetting("address", "Address, for a watch-only wallet"),
        InstanceSetting(
            "rpc_url",
            "JSON-RPC node (mainnet-beta, devnet, testnet, or your own)",
            default=DEFAULT_RPC_URL,
        ),
    )

    @classmethod
    def network(cls, instance: ProviderInstanceModel) -> str:
        return str(cls.setting(instance, "rpc_url") or DEFAULT_RPC_URL)

    @classmethod
    def _client(cls, instance: ProviderInstanceModel) -> Any:
        from solana.rpc.async_api import AsyncClient

        url = (
            cls.endpoint(instance, "rpc_url")
            if cls.setting(instance, "rpc_url")
            else DEFAULT_RPC_URL
        )
        return AsyncClient(url, timeout=CRYPTO_REQUEST_TIMEOUT_SECONDS)

    @classmethod
    def _keypair(cls, instance: ProviderInstanceModel) -> Any:
        from solders.keypair import Keypair

        try:
            return Keypair.from_base58_string(cls.private_key(instance))
        except InvalidInputExternalError:
            raise
        except Exception as exc:
            raise InvalidInputExternalError(
                "the wallet's secret key is not a base58 Solana keypair",
                provider=cls.name,
            ) from exc

    @classmethod
    def checked_address(cls, address: str) -> Any:
        from solders.pubkey import Pubkey

        try:
            return Pubkey.from_string(address.strip())
        except Exception as exc:
            raise InvalidInputExternalError(
                f"{address!r} is not a Solana address", provider=cls.name
            ) from exc

    @classmethod
    def generate(cls, network: Optional[str]) -> Dict[str, Any]:
        from solders.keypair import Keypair

        pair = Keypair()
        return {"address": str(pair.pubkey()), "private_key": str(pair)}

    @classmethod
    def address(cls, instance: ProviderInstanceModel) -> str:
        if cls.setting(instance, "api_key"):
            return str(cls._keypair(instance).pubkey())
        watched = cls.setting(instance, "address")
        if not watched:
            raise InvalidInputExternalError(
                "the wallet has neither a key nor an address", provider=cls.name
            )
        return str(cls.checked_address(str(watched)))

    @classmethod
    async def _rpc(
        cls, instance: ProviderInstanceModel, method: str, *args: Any, **kwargs: Any
    ) -> Any:
        """An RPC call's response, its failures typed."""
        from solana.exceptions import SolanaRpcException
        from solana.rpc.core import RPCException

        client = cls._client(instance)
        try:
            return await getattr(client, method)(*args, **kwargs)
        except BaseExternalError:
            raise
        except RPCException as exc:
            raise InvalidInputExternalError(
                f"the node refused: {exc}", provider=cls.name
            ) from exc
        except (SolanaRpcException, OSError, TimeoutError) as exc:
            raise TransientExternalError(
                f"the node did not answer: {type(exc).__name__}", provider=cls.name
            ) from exc
        finally:
            await client.close()

    @classmethod
    async def balance(
        cls, instance: ProviderInstanceModel, address: Optional[str]
    ) -> Dict[str, Any]:
        target = cls.checked_address(address or cls.address(instance))
        found = await cls._rpc(instance, "get_balance", target)
        return {
            "address": str(target),
            **cls.amount(int(found.value)),
            "provider": cls.name,
        }

    @classmethod
    def signed_transfer(
        cls, keypair: Any, to: Any, units: int, blockhash: Any
    ) -> bytes:
        """A signed SOL transfer, serialized."""
        from solders.message import Message
        from solders.system_program import TransferParams, transfer
        from solders.transaction import Transaction

        instruction = transfer(
            TransferParams(from_pubkey=keypair.pubkey(), to_pubkey=to, lamports=units)
        )
        message = Message.new_with_blockhash([instruction], keypair.pubkey(), blockhash)
        return bytes(Transaction([keypair], message, blockhash))

    @classmethod
    async def send(
        cls, instance: ProviderInstanceModel, to: str, units: int
    ) -> Dict[str, Any]:
        keypair, recipient = cls._keypair(instance), cls.checked_address(to)
        latest = await cls._rpc(instance, "get_latest_blockhash")
        raw = cls.signed_transfer(keypair, recipient, units, latest.value.blockhash)
        sent = await cls._rpc(instance, "send_raw_transaction", raw)
        return {"tx_id": str(sent.value)}

    @classmethod
    async def transaction(
        cls, instance: ProviderInstanceModel, tx_id: str
    ) -> Dict[str, Any]:
        from solders.signature import Signature

        try:
            signature = Signature.from_string(tx_id.strip())
        except Exception as exc:
            raise InvalidInputExternalError(
                f"{tx_id!r} is not a Solana signature", provider=cls.name
            ) from exc
        found = await cls._rpc(
            instance,
            "get_transaction",
            signature,
            encoding="json",
            max_supported_transaction_version=0,
        )
        result = json.loads(found.to_json()).get("result")
        if result is None:
            raise InvalidInputExternalError(
                f"no transaction {tx_id}", provider=cls.name, upstream_status=404
            )
        meta = result.get("meta") or {}
        return {
            "tx_id": tx_id,
            "slot": result.get("slot"),
            "block_time": result.get("blockTime"),
            "succeeded": meta.get("err") is None,
            "fee_units": meta.get("fee"),
            "provider": cls.name,
        }

    @classmethod
    async def transactions(
        cls, instance: ProviderInstanceModel, address: Optional[str], limit: int
    ) -> List[Dict[str, Any]]:
        target = cls.checked_address(address or cls.address(instance))
        found = await cls._rpc(
            instance, "get_signatures_for_address", target, limit=limit
        )
        return [
            {
                "tx_id": str(entry.signature),
                "slot": entry.slot,
                "block_time": entry.block_time,
                "succeeded": entry.err is None,
                "provider": cls.name,
            }
            for entry in found.value
        ]

    @classmethod
    async def fee_estimate(cls, instance: ProviderInstanceModel) -> Dict[str, Any]:
        from solders.message import Message
        from solders.pubkey import Pubkey
        from solders.system_program import TransferParams, transfer

        payer = cls.checked_address(cls.address(instance))
        latest = await cls._rpc(instance, "get_latest_blockhash")
        message = Message.new_with_blockhash(
            [
                transfer(
                    TransferParams(
                        from_pubkey=payer, to_pubkey=Pubkey.default(), lamports=1
                    )
                )
            ],
            payer,
            latest.value.blockhash,
        )
        fee = await cls._rpc(instance, "get_fee_for_message", message)
        return {
            "transfer_fee_units": fee.value,
            **cls.amount(int(fee.value or 0)),
            "provider": cls.name,
        }

    @classmethod
    async def token_balance(
        cls, instance: ProviderInstanceModel, token: str, address: Optional[str]
    ) -> Dict[str, Any]:
        from solana.rpc.models import TokenAccountOpts

        owner = cls.checked_address(address or cls.address(instance))
        mint = cls.checked_address(token)
        found = await cls._rpc(
            instance,
            "get_token_accounts_by_owner_json_parsed",
            owner,
            TokenAccountOpts(mint=mint),
        )
        units, decimals = 0, 0
        for account in json.loads(found.to_json())["result"]["value"]:
            amount = account["account"]["data"]["parsed"]["info"]["tokenAmount"]
            units += int(amount["amount"])
            decimals = int(amount["decimals"])
        return {
            "address": str(owner),
            "token": str(mint),
            "amount": from_units(units, decimals),
            "units": units,
            "provider": cls.name,
        }
