# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ethereum and EVM-compatible chains (Polygon, Arbitrum, Base, BNB
Chain, …), through a JSON-RPC node: the ``rpc_url`` setting decides the
chain and network (mainnet, Sepolia, …).

The instance's API key is the wallet's private key (hex); without one,
its ``address`` setting makes it watch-only. Payments and ERC-20
transfers are EIP-1559 transactions signed here; only the signed
transaction reaches the node. A node does not index an address's
history, so listing transactions is refused (use a block explorer).
"""

from typing import Any, ClassVar, Dict, List, Optional, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.crypto.EXT_Crypto import (
    CRYPTO_REQUEST_TIMEOUT_SECONDS,
    AbstractCryptoProvider,
    from_units,
    to_units,
)
from zephyrex.extensions.ExternalErrors import (
    BaseExternalError,
    InvalidInputExternalError,
    PermanentExternalError,
    TransientExternalError,
)
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

# Two base fees of headroom: the transaction stays valid through a run of
# full blocks.
BASE_FEE_HEADROOM = 2
ERC20_ABI = [
    {
        "name": "balanceOf",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "owner", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "decimals",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint8"}],
    },
    {
        "name": "symbol",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "string"}],
    },
    {
        "name": "transfer",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
]


class PRV_Ethereum_Crypto(AbstractCryptoProvider):
    name: ClassVar[str] = "ethereum"
    friendly_name: ClassVar[str] = "Ethereum"
    description: ClassVar[str] = "An Ethereum or EVM-compatible wallet"
    symbol: ClassVar[str] = "ETH"
    decimals: ClassVar[int] = 18
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="web3",
                friendly_name="web3.py",
                semver=">=7.0",
                reason="EVM JSON-RPC and ERC-20 contracts",
            ),
            PIP_Dependency(
                name="eth-account",
                friendly_name="eth-account",
                semver=">=0.13",
                reason="EVM keys and transaction signing",
            ),
        ]
    )
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting("api_key", "Private key (hex)", secret=True, field="api_key"),
        InstanceSetting("address", "Address, for a watch-only wallet"),
        InstanceSetting(
            "rpc_url", "JSON-RPC node (its chain: mainnet, Sepolia, Polygon, …)"
        ),
        InstanceSetting("symbol", "The chain's coin (ETH, POL, BNB, …)", default="ETH"),
    )

    @classmethod
    def network(cls, instance: ProviderInstanceModel) -> str:
        return str(cls.setting(instance, "rpc_url") or "")

    @classmethod
    def _web3(cls, instance: ProviderInstanceModel) -> Any:
        from web3 import AsyncWeb3

        return AsyncWeb3(
            AsyncWeb3.AsyncHTTPProvider(
                cls.endpoint(instance, "rpc_url"),
                request_kwargs={"timeout": CRYPTO_REQUEST_TIMEOUT_SECONDS},
            )
        )

    @classmethod
    def _account(cls, instance: ProviderInstanceModel) -> Any:
        from eth_account import Account

        try:
            return Account.from_key(cls.private_key(instance))
        except InvalidInputExternalError:
            raise
        except Exception as exc:
            raise InvalidInputExternalError(
                "the wallet's private key is not a 32-byte hex key", provider=cls.name
            ) from exc

    @classmethod
    def checked_address(cls, address: str) -> str:
        from web3 import Web3

        if not Web3.is_address(address.strip()):
            raise InvalidInputExternalError(
                f"{address!r} is not an EVM address", provider=cls.name
            )
        return str(Web3.to_checksum_address(address.strip()))

    @classmethod
    def generate(cls, network: Optional[str]) -> Dict[str, Any]:
        from eth_account import Account

        Account.enable_unaudited_hdwallet_features()
        account, mnemonic = Account.create_with_mnemonic()
        return {
            "address": account.address,
            "private_key": "0x" + account.key.hex().removeprefix("0x"),
            "mnemonic": mnemonic,
        }

    @classmethod
    def address(cls, instance: ProviderInstanceModel) -> str:
        if cls.setting(instance, "api_key"):
            return str(cls._account(instance).address)
        watched = cls.setting(instance, "address")
        if not watched:
            raise InvalidInputExternalError(
                "the wallet has neither a key nor an address", provider=cls.name
            )
        return cls.checked_address(str(watched))

    @classmethod
    def _coin(cls, instance: ProviderInstanceModel) -> str:
        return str(cls.setting(instance, "symbol") or cls.symbol)

    @classmethod
    async def _node(cls, call: Any) -> Any:
        """A node call's result, its failures typed: a refused request
        (insufficient funds, a bad nonce) is the caller's; a network fault
        is transient."""
        from web3.exceptions import Web3Exception

        try:
            return await call
        except BaseExternalError:
            raise
        except (TimeoutError, ConnectionError, OSError) as exc:
            raise TransientExternalError(
                f"the node did not answer: {type(exc).__name__}"
            ) from exc
        except (Web3Exception, ValueError) as exc:
            raise InvalidInputExternalError(f"the node refused: {exc}") from exc

    @classmethod
    async def balance(
        cls, instance: ProviderInstanceModel, address: Optional[str]
    ) -> Dict[str, Any]:
        target = (
            cls.address(instance) if address is None else cls.checked_address(address)
        )
        units = int(await cls._node(cls._web3(instance).eth.get_balance(target)))
        return {
            "address": target,
            **cls.amount(units),
            "symbol": cls._coin(instance),
            "provider": cls.name,
        }

    @classmethod
    async def _sign_and_send(
        cls, instance: ProviderInstanceModel, transaction: Dict[str, Any]
    ) -> Dict[str, Any]:
        web3, account = cls._web3(instance), cls._account(instance)
        latest = await cls._node(web3.eth.get_block("latest"))
        tip = int(await cls._node(web3.eth.max_priority_fee))
        filled = {
            **transaction,
            "from": account.address,
            "nonce": int(
                await cls._node(
                    web3.eth.get_transaction_count(account.address, "pending")
                )
            ),
            "chainId": int(await cls._node(web3.eth.chain_id)),
            "maxPriorityFeePerGas": tip,
            "maxFeePerGas": BASE_FEE_HEADROOM * int(latest["baseFeePerGas"]) + tip,
            "type": 2,
        }
        filled["gas"] = int(await cls._node(web3.eth.estimate_gas(filled)))
        signed = account.sign_transaction(filled)
        sent = await cls._node(web3.eth.send_raw_transaction(signed.raw_transaction))
        return {"tx_id": "0x" + bytes(sent).hex(), "gas_limit": filled["gas"]}

    @classmethod
    async def send(
        cls, instance: ProviderInstanceModel, to: str, units: int
    ) -> Dict[str, Any]:
        return await cls._sign_and_send(
            instance, {"to": cls.checked_address(to), "value": units}
        )

    @classmethod
    async def transaction(
        cls, instance: ProviderInstanceModel, tx_id: str
    ) -> Dict[str, Any]:
        from web3.exceptions import TransactionNotFound

        web3 = cls._web3(instance)
        found = await cls._node(web3.eth.get_transaction(tx_id))
        try:
            receipt = await cls._node(web3.eth.get_transaction_receipt(tx_id))
        except InvalidInputExternalError as exc:
            if not isinstance(exc.__cause__, TransactionNotFound):
                raise
            receipt = None  # Not mined yet.
        return {
            "tx_id": tx_id,
            "from": found.get("from"),
            "to": found.get("to"),
            **cls.amount(int(found.get("value", 0))),
            "symbol": cls._coin(instance),
            "block_number": found.get("blockNumber"),
            "confirmed": receipt is not None,
            "succeeded": receipt.get("status") == 1 if receipt else None,
            "gas_used": receipt.get("gasUsed") if receipt else None,
            "provider": cls.name,
        }

    @classmethod
    async def transactions(
        cls, instance: ProviderInstanceModel, address: Optional[str], limit: int
    ) -> List[Dict[str, Any]]:
        raise PermanentExternalError(
            "an EVM node does not index an address's transactions; use a block explorer",
            provider=cls.name,
        )

    @classmethod
    async def fee_estimate(cls, instance: ProviderInstanceModel) -> Dict[str, Any]:
        web3 = cls._web3(instance)
        latest = await cls._node(web3.eth.get_block("latest"))
        tip = int(await cls._node(web3.eth.max_priority_fee))
        base = int(latest["baseFeePerGas"])
        max_fee = BASE_FEE_HEADROOM * base + tip
        return {
            "base_fee_gwei": from_units(base, 9),
            "priority_fee_gwei": from_units(tip, 9),
            "max_fee_gwei": from_units(max_fee, 9),
            "transfer_cost": from_units(max_fee * 21_000, cls.decimals),
            "symbol": cls._coin(instance),
            "provider": cls.name,
        }

    @classmethod
    def _contract(cls, instance: ProviderInstanceModel, token: str) -> Any:
        return cls._web3(instance).eth.contract(
            address=cls.checked_address(token), abi=ERC20_ABI
        )

    @classmethod
    async def token_balance(
        cls, instance: ProviderInstanceModel, token: str, address: Optional[str]
    ) -> Dict[str, Any]:
        target = (
            cls.address(instance) if address is None else cls.checked_address(address)
        )
        contract = cls._contract(instance, token)
        units = int(await cls._node(contract.functions.balanceOf(target).call()))
        decimals = int(await cls._node(contract.functions.decimals().call()))
        return {
            "address": target,
            "token": cls.checked_address(token),
            "symbol": str(await cls._node(contract.functions.symbol().call())),
            "amount": from_units(units, decimals),
            "units": units,
            "provider": cls.name,
        }

    @classmethod
    async def send_token(
        cls, instance: ProviderInstanceModel, token: str, to: str, amount: str
    ) -> Dict[str, Any]:
        contract = cls._contract(instance, token)
        decimals = int(await cls._node(contract.functions.decimals().call()))
        units = to_units(amount, decimals)
        recipient = cls.checked_address(to)
        data = contract.encode_abi("transfer", args=[recipient, units])
        sent = await cls._sign_and_send(
            instance, {"to": cls.checked_address(token), "value": 0, "data": data}
        )
        return {
            **sent,
            "token": cls.checked_address(token),
            "to": recipient,
            "amount": from_units(units, decimals),
            "units": units,
            "provider": cls.name,
        }
