# SPDX-License-Identifier: AGPL-3.0-or-later
"""Crypto wallets: exact amounts, Bitcoin coin selection, key generation
that round-trips, transactions signed here (checked by parsing them
back), address checks per chain and network, watch-only wallets that
cannot send, real reads on Bitcoin mainnet, Sepolia and Solana devnet,
and live sends from funded test wallets."""

from decimal import Decimal

import httpx
import pytest

from zephyrex.extensions.crypto.EXT_Crypto import EXT_Crypto, from_units, to_units
from zephyrex.extensions.crypto.PRV_Bitcoin import (
    DUST_SATS,
    PRV_Bitcoin_Crypto,
    select_coins,
    vsize,
)
from zephyrex.extensions.crypto.PRV_Ethereum import PRV_Ethereum_Crypto
from zephyrex.extensions.crypto.PRV_Solana import PRV_Solana_Crypto
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError

GENESIS = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
SEPOLIA_RPC = "https://ethereum-sepolia-rpc.publicnode.com"
SOLANA_DEVNET = "https://api.devnet.solana.com"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def _online(url: str) -> bool:
    try:
        httpx.head(url, timeout=5)
        return True
    except httpx.HTTPError:
        return False


def reachable(url: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(not _online(url), reason=f"{url} is unreachable")


def utxo(value: int, confirmed: bool = True, n: int = 0) -> dict:
    return {
        "txid": f"{n:064x}",
        "vout": 0,
        "value": value,
        "status": {"confirmed": confirmed},
    }


class TestAmounts:
    @pytest.mark.parametrize(
        "amount, decimals, units",
        [
            ("0.0005", 8, 50_000),
            ("1", 18, 10**18),
            (Decimal("0.1"), 9, 100_000_000),
            ("0.000000001", 9, 1),
            (3, 8, 300_000_000),
        ],
    )
    def test_exact(self, amount, decimals, units):
        assert to_units(amount, decimals) == units

    @pytest.mark.parametrize("amount", ["0.000000001", "0", "-1", "abc", "nan", "inf"])
    def test_refused(self, amount):
        with pytest.raises(InvalidInputExternalError):
            to_units(amount, 8)

    def test_display(self):
        assert from_units(50_000, 8) == "0.0005"
        assert from_units(10**18, 18) == "1"
        assert from_units(0, 9) == "0"


class TestCoinSelection:
    def test_largest_first_with_change(self):
        inputs, fee, change = select_coins(
            [utxo(10_000, n=1), utxo(90_000, n=2), utxo(50_000, False, n=3)],
            50_000,
            2.0,
        )
        assert [u["value"] for u in inputs] == [90_000]
        assert fee == 2 * vsize(1, 2)
        assert change == 90_000 - 50_000 - fee

    def test_dust_change_goes_to_the_fee(self):
        fee_rate = 1.0
        amount = 100_000 - vsize(1, 2) - DUST_SATS + 1
        inputs, fee, change = select_coins([utxo(100_000)], amount, fee_rate)
        assert change == 0 and fee == 100_000 - amount

    def test_unconfirmed_funds_do_not_count(self):
        with pytest.raises(InvalidInputExternalError, match="insufficient"):
            select_coins([utxo(1_000_000, confirmed=False)], 1_000, 1.0)


class TestKeys:
    def test_bitcoin(self, provider_instance):
        created = EXT_Crypto._chain("bitcoin").generate("testnet")
        assert created["address"].startswith("tb1q")
        instance = provider_instance(
            PRV_Bitcoin_Crypto,
            api_key=created["private_key"],
            settings={"network": "testnet"},
        )
        assert PRV_Bitcoin_Crypto.address(instance) == created["address"]

    def test_ethereum(self, provider_instance):
        created = PRV_Ethereum_Crypto.generate(None)
        assert len(created["mnemonic"].split()) == 12
        instance = provider_instance(
            PRV_Ethereum_Crypto, api_key=created["private_key"]
        )
        assert PRV_Ethereum_Crypto.address(instance) == created["address"]

    def test_solana(self, provider_instance):
        created = PRV_Solana_Crypto.generate(None)
        instance = provider_instance(PRV_Solana_Crypto, api_key=created["private_key"])
        assert PRV_Solana_Crypto.address(instance) == created["address"]


class TestSigning:
    def test_a_bitcoin_payment_signs_and_parses_back(self):
        from bitcoinlib.keys import Key
        from bitcoinlib.transactions import Transaction

        key = Key(network="testnet")
        to = Key(network="testnet").address(encoding="bech32", script_type="p2wpkh")
        raw = PRV_Bitcoin_Crypto.sign(
            key, "testnet", [utxo(100_000, n=7)], to, 60_000, 39_000
        )
        parsed = Transaction.parse_hex(raw, network="testnet")
        assert [(o.address, o.value) for o in parsed.outputs] == [
            (to, 60_000),
            (key.address(encoding="bech32", script_type="p2wpkh"), 39_000),
        ]
        assert parsed.witness_type == "segwit"

    def test_a_solana_transfer_signs(self):
        from solders.hash import Hash
        from solders.keypair import Keypair
        from solders.transaction import Transaction

        payer, to = Keypair(), Keypair().pubkey()
        raw = PRV_Solana_Crypto.signed_transfer(payer, to, 5_000, Hash.default())
        Transaction.from_bytes(raw).verify()


class TestAddresses:
    def test_a_testnet_address_is_refused_on_mainnet(self):
        testnet = PRV_Bitcoin_Crypto.generate("testnet")["address"]
        with pytest.raises(InvalidInputExternalError):
            PRV_Bitcoin_Crypto.checked_address(testnet, "bitcoin")
        assert PRV_Bitcoin_Crypto.checked_address(GENESIS, "bitcoin") == GENESIS

    @pytest.mark.parametrize(
        "check, address",
        [
            (PRV_Ethereum_Crypto.checked_address, "0x123"),
            (PRV_Ethereum_Crypto.checked_address, GENESIS),
            (PRV_Solana_Crypto.checked_address, "0xabc"),
        ],
    )
    def test_malformed(self, check, address):
        with pytest.raises(InvalidInputExternalError):
            check(address)

    async def test_a_watch_only_wallet_cannot_send(self, provider_instance):
        instance = provider_instance(PRV_Bitcoin_Crypto, settings={"address": GENESIS})
        with pytest.raises(InvalidInputExternalError, match="watch-only"):
            await PRV_Bitcoin_Crypto.send_amount(instance, GENESIS, "0.0001")

    def test_an_rpc_url_on_the_private_network_is_refused(self, provider_instance):
        instance = provider_instance(
            PRV_Ethereum_Crypto, settings={"rpc_url": "http://169.254.169.254/"}
        )
        with pytest.raises(InvalidInputExternalError):
            PRV_Ethereum_Crypto.endpoint(instance, "rpc_url")


class TestChainReads:
    @reachable("https://blockstream.info")
    async def test_bitcoin_mainnet(self, provider_instance, rotation_over, monkeypatch):
        wallet = provider_instance(PRV_Bitcoin_Crypto, settings={"address": GENESIS})
        monkeypatch.setattr(EXT_Crypto, "_root_rotation_cache", rotation_over(wallet))
        balance = await EXT_Crypto.get_balance(wallet.name)
        assert balance["address"] == GENESIS and balance["units"] >= 50 * 10**8
        fees = await EXT_Crypto.estimate_fee(wallet.name)
        assert fees["sats_per_vbyte"] > 0
        history = await EXT_Crypto.list_transactions(wallet.name, limit=2)
        assert history and history[0]["tx_id"]

    @reachable(SEPOLIA_RPC)
    async def test_sepolia(self, provider_instance):
        wallet = provider_instance(
            PRV_Ethereum_Crypto,
            settings={"address": ZERO_ADDRESS, "rpc_url": SEPOLIA_RPC},
        )
        balance = await PRV_Ethereum_Crypto.balance(wallet, None)
        assert balance["units"] >= 0
        fees = await PRV_Ethereum_Crypto.fee_estimate(wallet)
        assert Decimal(fees["max_fee_gwei"]) > 0

    @reachable(SOLANA_DEVNET)
    async def test_solana_devnet(self, provider_instance):
        fresh = PRV_Solana_Crypto.generate(None)["address"]
        wallet = provider_instance(
            PRV_Solana_Crypto, settings={"address": fresh, "rpc_url": SOLANA_DEVNET}
        )
        assert (await PRV_Solana_Crypto.balance(wallet, None))["units"] == 0
        assert (await PRV_Solana_Crypto.fee_estimate(wallet))["transfer_fee_units"] > 0


class TestLiveSends:
    """A funded test wallet pays itself the smallest amount."""

    @pytest.mark.external_api(provider="bitcoin_testnet_wallet")
    async def test_bitcoin(self, provider_instance, sandbox_credentials_for):
        creds = sandbox_credentials_for("bitcoin_testnet_wallet")
        wallet = provider_instance(
            PRV_Bitcoin_Crypto,
            api_key=creds["BITCOIN_TESTNET_WIF"],
            settings={"network": "testnet"},
        )
        sent = await PRV_Bitcoin_Crypto.send_amount(
            wallet, PRV_Bitcoin_Crypto.address(wallet), "0.00001"
        )
        assert len(sent["tx_id"]) == 64

    @pytest.mark.external_api(provider="sepolia_wallet")
    async def test_sepolia(self, provider_instance, sandbox_credentials_for):
        creds = sandbox_credentials_for("sepolia_wallet")
        wallet = provider_instance(
            PRV_Ethereum_Crypto,
            api_key=creds["SEPOLIA_PRIVATE_KEY"],
            settings={"rpc_url": creds["SEPOLIA_RPC_URL"]},
        )
        sent = await PRV_Ethereum_Crypto.send_amount(
            wallet, PRV_Ethereum_Crypto.address(wallet), "0.000001"
        )
        assert sent["tx_id"].startswith("0x")

    @pytest.mark.external_api(provider="solana_devnet_wallet")
    async def test_solana(self, provider_instance, sandbox_credentials_for):
        creds = sandbox_credentials_for("solana_devnet_wallet")
        wallet = provider_instance(
            PRV_Solana_Crypto,
            api_key=creds["SOLANA_DEVNET_SECRET"],
            settings={"rpc_url": SOLANA_DEVNET},
        )
        sent = await PRV_Solana_Crypto.send_amount(
            wallet, PRV_Solana_Crypto.address(wallet), "0.000001"
        )
        assert sent["tx_id"]
