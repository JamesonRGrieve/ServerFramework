# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cryptocurrency wallets on Bitcoin, Ethereum and EVM-compatible chains,
and Solana: create wallets, read balances, transactions and fees, and
send coins and tokens.

Each provider instance is one wallet on one network: its private key a
write-only setting (or only an ``address``, for a watch-only wallet).
Every ability names the wallet (the instance's name or id) and acts with
it alone. Who may create, read and send is decided by permissions, as
for every extension: these abilities move real money.

Amounts are decimal strings in the chain's coin (``"0.0005"`` BTC) and
are converted exactly to its smallest unit; more decimals than the coin
has are refused rather than rounded.
"""

from abc import abstractmethod
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar, Dict, List, Optional, Set, Union

from fastapi import HTTPException, status

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ability,
)
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    PermanentExternalError,
    TransientExternalError,
)
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.lib.ProviderHTTPClient import SSRFGuardError, validate_outbound_url
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

CRYPTO_REQUEST_TIMEOUT_SECONDS = 30.0
DEFAULT_TRANSACTIONS = 10
MAX_TRANSACTIONS = 100

Amount = Union[str, int, Decimal]


def to_units(amount: Amount, decimals: int) -> int:
    """``amount`` of a coin with ``decimals`` places, in its smallest unit,
    exactly. Positive amounts only; no rounding."""
    try:
        value = Decimal(str(amount).strip())
    except (InvalidOperation, ValueError) as exc:
        raise InvalidInputExternalError(f"{amount!r} is not an amount") from exc
    if not value.is_finite() or value <= 0:
        raise InvalidInputExternalError("an amount must be a positive number")
    scaled = value.scaleb(decimals)
    if scaled != scaled.to_integral_value():
        raise InvalidInputExternalError(
            f"{amount} has more than {decimals} decimal places"
        )
    return int(scaled)


def from_units(units: int, decimals: int) -> str:
    """Smallest units as a plain decimal string of the coin."""
    text = format(Decimal(units).scaleb(-decimals), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def transaction_limit(limit: int) -> int:
    if not 1 <= limit <= MAX_TRANSACTIONS:
        raise InvalidInputExternalError(f"limit must be 1-{MAX_TRANSACTIONS}")
    return limit


class AbstractCryptoProvider(AbstractStaticProvider):
    """A blockchain; each instance is one wallet on one of its networks."""

    name: ClassVar[str] = ""
    friendly_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    symbol: ClassVar[str] = ""
    decimals: ClassVar[int] = 0
    _abilities: ClassVar[Set[str]] = {
        "wallet_address",
        "get_balance",
        "send",
        "get_transaction",
        "list_transactions",
        "estimate_fee",
        "get_token_balance",
        "send_token",
    }
    _env: ClassVar[Dict[str, Any]] = {}
    http_timeout_seconds: ClassVar[float] = CRYPTO_REQUEST_TIMEOUT_SECONDS

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    def private_key(cls, instance: ProviderInstanceModel) -> str:
        key = cls.setting(instance, "api_key")
        if not key:
            raise InvalidInputExternalError(
                "This wallet is watch-only (it holds no private key)", provider=cls.name
            )
        return str(key)

    @classmethod
    def endpoint(cls, instance: ProviderInstanceModel, key: str) -> str:
        """A configured node or API address, held to the SSRF guard."""
        url = str(cls.setting(instance, key) or "").rstrip("/")
        if not url:
            raise TransientExternalError(
                f"{cls.friendly_name} {key} not configured", provider=cls.name
            )
        try:
            validate_outbound_url(url)
        except SSRFGuardError as exc:
            raise InvalidInputExternalError(str(exc), provider=cls.name) from exc
        return url

    @classmethod
    def amount(cls, units: int) -> Dict[str, Any]:
        return {
            "amount": from_units(units, cls.decimals),
            "units": units,
            "symbol": cls.symbol,
        }

    @classmethod
    def no_tokens(cls) -> PermanentExternalError:
        return PermanentExternalError(
            f"{cls.friendly_name} has no tokens this extension handles",
            provider=cls.name,
        )

    @classmethod
    @abstractmethod
    def generate(cls, network: Optional[str]) -> Dict[str, Any]:
        """A new key pair: ``address`` and ``private_key`` (and a
        ``mnemonic`` where the chain has one)."""

    @classmethod
    @abstractmethod
    def address(cls, instance: ProviderInstanceModel) -> str:
        """The wallet's address (from its key, or its watch-only address)."""

    @classmethod
    @abstractmethod
    async def balance(
        cls, instance: ProviderInstanceModel, address: Optional[str]
    ) -> Dict[str, Any]: ...

    @classmethod
    @abstractmethod
    async def send(
        cls, instance: ProviderInstanceModel, to: str, units: int
    ) -> Dict[str, Any]:
        """Sign and broadcast a payment of ``units``; its transaction id."""

    @classmethod
    @abstractmethod
    async def transaction(
        cls, instance: ProviderInstanceModel, tx_id: str
    ) -> Dict[str, Any]: ...

    @classmethod
    @abstractmethod
    async def transactions(
        cls, instance: ProviderInstanceModel, address: Optional[str], limit: int
    ) -> List[Dict[str, Any]]: ...

    @classmethod
    @abstractmethod
    async def fee_estimate(cls, instance: ProviderInstanceModel) -> Dict[str, Any]: ...

    @classmethod
    def network(cls, instance: ProviderInstanceModel) -> str:
        """The network the wallet is on, as configured."""
        return str(cls.setting(instance, "network") or "")

    @classmethod
    async def describe(cls, instance: ProviderInstanceModel) -> Dict[str, Any]:
        return {
            "address": cls.address(instance),
            "chain": cls.name,
            "network": cls.network(instance),
            "symbol": cls.symbol,
            "watch_only": not cls.setting(instance, "api_key"),
        }

    @classmethod
    async def send_amount(
        cls, instance: ProviderInstanceModel, to: str, amount: Amount
    ) -> Dict[str, Any]:
        units = to_units(amount, cls.decimals)
        sent = await cls.send(instance, to, units)
        return {**sent, **cls.amount(units), "to": to, "provider": cls.name}

    @classmethod
    async def token_balance(
        cls, instance: ProviderInstanceModel, token: str, address: Optional[str]
    ) -> Dict[str, Any]:
        raise cls.no_tokens()

    @classmethod
    async def send_token(
        cls, instance: ProviderInstanceModel, token: str, to: str, amount: str
    ) -> Dict[str, Any]:
        raise cls.no_tokens()

    @classmethod
    def services(cls) -> List[str]:
        return ["crypto_wallet"]


class EXT_Crypto(AbstractStaticExtension):
    name: ClassVar[str] = "crypto"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Cryptocurrency wallets on Bitcoin, Ethereum (and EVM chains) and Solana"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[Set[str]] = {
        "list_wallets",
        "create_wallet",
        *AbstractCryptoProvider._abilities,
    }

    @classmethod
    def _chain(cls, chain: str) -> Any:
        for provider in cls.providers:
            if provider.name == chain:
                return provider
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"chain must be one of {', '.join(p.name for p in cls.providers)}",
        )

    @classmethod
    @ability("list_wallets")
    async def list_wallets(cls) -> List[Dict[str, Any]]:
        """The configured wallets: id, name and chain."""
        return cls.instances_of()

    @classmethod
    @ability("create_wallet")
    async def create_wallet(
        cls, chain: str, network: Optional[str] = None
    ) -> Dict[str, Any]:
        """A new key pair on ``chain`` (``network`` for Bitcoin: bitcoin,
        testnet, testnet4 or signet). The private key is shown once: keep
        it in a wallet instance's write-only key setting."""
        created: Dict[str, Any] = cls._chain(chain).generate(network)
        return {**created, "chain": chain}

    @classmethod
    @ability("wallet_address")
    async def wallet_address(cls, wallet: str) -> Dict[str, Any]:
        result: Dict[str, Any] = await cls.rotate_on_instance(wallet, "describe")
        return result

    @classmethod
    @ability("get_balance")
    async def get_balance(
        cls, wallet: str, address: Optional[str] = None
    ) -> Dict[str, Any]:
        """The wallet's balance, or ``address``'s on the wallet's network."""
        result: Dict[str, Any] = await cls.rotate_on_instance(
            wallet, "balance", address
        )
        return result

    @classmethod
    @ability("send")
    async def send(cls, wallet: str, to: str, amount: str) -> Dict[str, Any]:
        """Send ``amount`` of the chain's coin from the wallet to ``to``."""
        result: Dict[str, Any] = await cls.rotate_on_instance(
            wallet, "send_amount", to, amount
        )
        return result

    @classmethod
    @ability("get_transaction")
    async def get_transaction(cls, wallet: str, tx_id: str) -> Dict[str, Any]:
        result: Dict[str, Any] = await cls.rotate_on_instance(
            wallet, "transaction", tx_id
        )
        return result

    @classmethod
    @ability("list_transactions")
    async def list_transactions(
        cls,
        wallet: str,
        address: Optional[str] = None,
        limit: int = DEFAULT_TRANSACTIONS,
    ) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = await cls.rotate_on_instance(
            wallet, "transactions", address, transaction_limit(limit)
        )
        return result

    @classmethod
    @ability("estimate_fee")
    async def estimate_fee(cls, wallet: str) -> Dict[str, Any]:
        result: Dict[str, Any] = await cls.rotate_on_instance(wallet, "fee_estimate")
        return result

    @classmethod
    @ability("get_token_balance")
    async def get_token_balance(
        cls, wallet: str, token: str, address: Optional[str] = None
    ) -> Dict[str, Any]:
        """An ERC-20 (EVM) or SPL (Solana) token balance, by the token's
        contract or mint address."""
        result: Dict[str, Any] = await cls.rotate_on_instance(
            wallet, "token_balance", token, address
        )
        return result

    @classmethod
    @ability("send_token")
    async def send_token(
        cls, wallet: str, token: str, to: str, amount: str
    ) -> Dict[str, Any]:
        """Send an ERC-20 token (EVM chains)."""
        result: Dict[str, Any] = await cls.rotate_on_instance(
            wallet, "send_token", token, to, amount
        )
        return result
