# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bitcoin: a native SegWit (P2WPKH) wallet on mainnet, testnet, testnet4
or signet, reading the chain through an Esplora API (Blockstream's or
mempool.space's by default, or your own).

The instance's API key is the wallet's private key (WIF); without one,
its ``address`` setting makes it watch-only. A payment is built and
signed here from the wallet's confirmed outputs, largest first, at
Esplora's fee rate for confirmation within about six blocks, with change
back to the wallet; only the signed transaction leaves the server.
"""

import math
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.crypto.EXT_Crypto import AbstractCryptoProvider
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.lib.ProviderHTTPClient import path_segment
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

NETWORKS = ("bitcoin", "testnet", "testnet4", "signet")
DEFAULT_NETWORK = "bitcoin"
ESPLORA = {
    "bitcoin": "https://blockstream.info/api",
    "testnet": "https://blockstream.info/testnet/api",
    "testnet4": "https://mempool.space/testnet4/api",
    "signet": "https://mempool.space/signet/api",
}
FEE_TARGET_BLOCKS = "6"
# A P2WPKH transaction's virtual size: overhead, then per input and output.
OVERHEAD_VBYTES = 10.5
INPUT_VBYTES = 68
OUTPUT_VBYTES = 31
# Change below this is uneconomic to spend (P2WPKH dust); it goes to fees.
DUST_SATS = 294


def vsize(inputs: int, outputs: int) -> int:
    return math.ceil(OVERHEAD_VBYTES + INPUT_VBYTES * inputs + OUTPUT_VBYTES * outputs)


def select_coins(
    utxos: List[Mapping[str, Any]], amount: int, fee_rate: float
) -> Tuple[List[Mapping[str, Any]], int, int]:
    """Confirmed outputs, largest first, enough for ``amount`` and the fee:
    ``(inputs, fee, change)``. Change under the dust limit is left as fee."""
    spendable = sorted(
        (u for u in utxos if u.get("status", {}).get("confirmed")),
        key=lambda u: int(u["value"]),
        reverse=True,
    )
    chosen: List[Mapping[str, Any]] = []
    total = 0
    for utxo in spendable:
        chosen.append(utxo)
        total += int(utxo["value"])
        fee = math.ceil(vsize(len(chosen), 2) * fee_rate)
        if total >= amount + fee:
            change = total - amount - fee
            if change < DUST_SATS:
                # No change output: everything over the amount is the fee
                # (at least the two-output fee, so above the one-output one).
                return chosen, total - amount, 0
            return chosen, fee, change
    raise InvalidInputExternalError(
        f"insufficient confirmed funds: {total} sats for {amount} plus fees"
    )


class PRV_Bitcoin_Crypto(AbstractCryptoProvider):
    name: ClassVar[str] = "bitcoin"
    friendly_name: ClassVar[str] = "Bitcoin"
    description: ClassVar[str] = "A Bitcoin wallet (native SegWit)"
    symbol: ClassVar[str] = "BTC"
    decimals: ClassVar[int] = 8
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="bitcoinlib",
                friendly_name="bitcoinlib",
                semver=">=0.7.9",
                reason="Bitcoin keys, addresses and transaction signing",
            )
        ]
    )
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting("api_key", "Private key (WIF)", secret=True, field="api_key"),
        InstanceSetting("address", "Address, for a watch-only wallet"),
        InstanceSetting(
            "network",
            "bitcoin, testnet, testnet4 or signet",
            default=DEFAULT_NETWORK,
        ),
        InstanceSetting(
            "esplora_url",
            "Esplora API (empty: Blockstream's or mempool.space's for the network)",
        ),
    )

    @classmethod
    def network(cls, instance: ProviderInstanceModel) -> str:
        network = str(cls.setting(instance, "network") or DEFAULT_NETWORK)
        if network not in NETWORKS:
            raise InvalidInputExternalError(
                f"Bitcoin network must be one of {', '.join(NETWORKS)}",
                provider=cls.name,
            )
        return network

    @classmethod
    def _esplora(cls, instance: ProviderInstanceModel) -> str:
        if cls.setting(instance, "esplora_url"):
            return cls.endpoint(instance, "esplora_url")
        return ESPLORA[cls.network(instance)]

    @classmethod
    def _key(cls, instance: ProviderInstanceModel) -> Any:
        from bitcoinlib.keys import Key

        try:
            return Key(cls.private_key(instance), network=cls.network(instance))
        except InvalidInputExternalError:
            raise
        except Exception as exc:
            raise InvalidInputExternalError(
                "the wallet's private key is not a WIF key for its network",
                provider=cls.name,
            ) from exc

    @classmethod
    def checked_address(cls, address: str, network: str) -> str:
        """``address``, refused unless it is a Bitcoin address of the
        wallet's network family (mainnet, or the test networks)."""
        from bitcoinlib.keys import Address

        try:
            parsed = Address.parse(address.strip())
        except Exception as exc:
            raise InvalidInputExternalError(
                f"{address!r} is not a Bitcoin address", provider=cls.name
            ) from exc
        mainnet = parsed.network.name == "bitcoin"
        if mainnet != (network == "bitcoin"):
            raise InvalidInputExternalError(
                f"{address!r} is not an address on {network}", provider=cls.name
            )
        return address.strip()

    @classmethod
    def generate(cls, network: Optional[str]) -> Dict[str, Any]:
        from bitcoinlib.keys import Key

        network = network or DEFAULT_NETWORK
        if network not in NETWORKS:
            raise InvalidInputExternalError(
                f"Bitcoin network must be one of {', '.join(NETWORKS)}"
            )
        key = Key(network=network)
        return {
            "network": network,
            "address": key.address(encoding="bech32", script_type="p2wpkh"),
            "private_key": key.wif(),
        }

    @classmethod
    def address(cls, instance: ProviderInstanceModel) -> str:
        if cls.setting(instance, "api_key"):
            return str(
                cls._key(instance).address(encoding="bech32", script_type="p2wpkh")
            )
        watched = cls.setting(instance, "address")
        if not watched:
            raise InvalidInputExternalError(
                "the wallet has neither a key nor an address", provider=cls.name
            )
        return cls.checked_address(str(watched), cls.network(instance))

    @classmethod
    def _target(cls, instance: ProviderInstanceModel, address: Optional[str]) -> str:
        if address is None:
            return cls.address(instance)
        return path_segment(
            cls.checked_address(address, cls.network(instance)), "address"
        )

    @classmethod
    async def balance(
        cls, instance: ProviderInstanceModel, address: Optional[str]
    ) -> Dict[str, Any]:
        target = cls._target(instance, address)
        found = await cls.get_json(f"{cls._esplora(instance)}/address/{target}")
        chain, pool = found.get("chain_stats", {}), found.get("mempool_stats", {})
        confirmed = int(chain.get("funded_txo_sum", 0)) - int(
            chain.get("spent_txo_sum", 0)
        )
        pending = int(pool.get("funded_txo_sum", 0)) - int(pool.get("spent_txo_sum", 0))
        return {
            "address": target,
            **cls.amount(confirmed),
            "pending_units": pending,
            "provider": cls.name,
        }

    @classmethod
    async def _fee_rate(cls, instance: ProviderInstanceModel) -> float:
        rates = await cls.get_json(f"{cls._esplora(instance)}/fee-estimates")
        return float(rates.get(FEE_TARGET_BLOCKS) or min(rates.values(), default=1.0))

    @classmethod
    def sign(
        cls,
        key: Any,
        network: str,
        inputs: List[Mapping[str, Any]],
        to: str,
        units: int,
        change: int,
    ) -> str:
        """A signed P2WPKH transaction, as raw hex."""
        from bitcoinlib.transactions import Transaction

        transaction = Transaction(network=network, witness_type="segwit")
        for utxo in inputs:
            transaction.add_input(
                utxo["txid"],
                int(utxo["vout"]),
                keys=key,
                value=int(utxo["value"]),
                witness_type="segwit",
            )
        transaction.add_output(units, to)
        if change:
            transaction.add_output(
                change, key.address(encoding="bech32", script_type="p2wpkh")
            )
        transaction.sign()
        if not transaction.verify():
            raise InvalidInputExternalError("the transaction did not verify")
        return str(transaction.raw_hex())

    @classmethod
    async def send(
        cls, instance: ProviderInstanceModel, to: str, units: int
    ) -> Dict[str, Any]:
        network = cls.network(instance)
        to = cls.checked_address(to, network)
        key = cls._key(instance)
        base = cls._esplora(instance)
        utxos = await cls.get_json(f"{base}/address/{cls.address(instance)}/utxo")
        inputs, fee, change = select_coins(utxos, units, await cls._fee_rate(instance))
        raw = cls.sign(key, network, inputs, to, units, change)
        tx_id = await cls.http().request(
            "POST", f"{base}/tx", content=raw, headers={"Content-Type": "text/plain"}
        )
        return {"tx_id": str(tx_id).strip(), "fee_units": fee}

    @classmethod
    def _summary(cls, tx: Mapping[str, Any]) -> Dict[str, Any]:
        state = tx.get("status", {})
        return {
            "tx_id": tx.get("txid"),
            "confirmed": bool(state.get("confirmed")),
            "block_height": state.get("block_height"),
            "block_time": state.get("block_time"),
            "fee_units": tx.get("fee"),
            "inputs": [
                {
                    "address": (vin.get("prevout") or {}).get("scriptpubkey_address"),
                    "units": (vin.get("prevout") or {}).get("value"),
                }
                for vin in tx.get("vin", [])
            ],
            "outputs": [
                {"address": out.get("scriptpubkey_address"), "units": out.get("value")}
                for out in tx.get("vout", [])
            ],
            "provider": cls.name,
        }

    @classmethod
    async def transaction(
        cls, instance: ProviderInstanceModel, tx_id: str
    ) -> Dict[str, Any]:
        found = await cls.get_json(
            f"{cls._esplora(instance)}/tx/{path_segment(tx_id, 'transaction id')}"
        )
        return cls._summary(found)

    @classmethod
    async def transactions(
        cls, instance: ProviderInstanceModel, address: Optional[str], limit: int
    ) -> List[Dict[str, Any]]:
        target = cls._target(instance, address)
        found = await cls.get_json(f"{cls._esplora(instance)}/address/{target}/txs")
        return [cls._summary(tx) for tx in found[:limit]]

    @classmethod
    async def fee_estimate(cls, instance: ProviderInstanceModel) -> Dict[str, Any]:
        rate = await cls._fee_rate(instance)
        return {
            "sats_per_vbyte": rate,
            "target_blocks": int(FEE_TARGET_BLOCKS),
            "typical_payment_units": math.ceil(vsize(1, 2) * rate),
            "provider": cls.name,
        }
