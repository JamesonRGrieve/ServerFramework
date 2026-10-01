from unittest.mock import MagicMock, patch

import pytest

from zephyrex.extensions.crypto.EXT_Crypto import EXT_Crypto


class TestCryptoExtension:
    """Test cases for Crypto Extension."""

    @pytest.fixture
    def extension(self):
        """Create an EXT_Crypto instance for testing."""
        return EXT_Crypto()

    def test_extension_metadata(self, extension):
        """Test extension metadata is correctly set."""
        assert extension.name == "crypto"
        assert extension.version == "1.0.0"
        assert "cryptocurrency" in extension.description.lower()
        assert "blockchain" in extension.description.lower()

    def test_dependencies(self, extension):
        """Test extension dependencies are properly defined."""
        # Check extension dependencies
        ext_deps = {dep.name for dep in extension.ext_dependencies}
        assert "core" in ext_deps
        assert "labels" in ext_deps

        # Check pip dependencies
        pip_deps = {dep.name for dep in extension.pip_dependencies}
        assert "solana" in pip_deps
        assert "bitcoin" in pip_deps
        assert "web3" in pip_deps
        assert "requests" in pip_deps
        assert "cryptography" in pip_deps

        # Check sys dependencies
        assert isinstance(extension.sys_dependencies, list)

    def test_capabilities(self, extension):
        """Test extension capabilities are properly defined."""
        expected_capabilities = {
            "wallet_management",
            "transaction_creation",
            "balance_checking",
            "blockchain_interaction",
            "token_operations",
            "transaction_monitoring",
        }
        assert set(extension.capabilities) == expected_capabilities

    def test_initialization(self, extension):
        """Test extension initialization."""
        assert hasattr(extension, "blockchain")
        assert hasattr(extension, "api_uri")
        assert hasattr(extension, "wallet_address")
        assert hasattr(extension, "wallet_private_key")
        assert hasattr(extension, "provider")
        assert hasattr(extension, "commands")

    def test_default_configuration(self, extension):
        """Test default configuration values."""
        assert extension.blockchain == "solana"
        assert extension.api_uri == ""
        assert extension.wallet_address == ""
        assert extension.wallet_private_key == ""

    @patch("zephyrex.extensions.crypto.EXT_Crypto.logger")
    def test_on_initialize_success(self, mock_logger, extension):
        """Test successful extension initialization."""
        with patch.object(extension, "_create_provider"), patch.object(
            extension, "_register_commands"
        ), patch.object(extension, "register_capability"):

            result = extension.on_initialize()
            assert result is True
            mock_logger.debug.assert_called()

    @patch("zephyrex.extensions.crypto.EXT_Crypto.logger")
    def test_on_initialize_failure(self, mock_logger, extension):
        """Test extension initialization failure."""
        with patch.object(
            extension, "_create_provider", side_effect=Exception("Test error")
        ):
            result = extension.on_initialize()
            assert result is False
            mock_logger.error.assert_called()

    def test_create_provider_solana(self, extension):
        """Test Solana provider creation."""
        extension.blockchain = "solana"

        with patch("zephyrex.extensions.crypto.Solana.SolanaProvider") as mock_provider:
            mock_instance = MagicMock()
            mock_provider.return_value = mock_instance

            extension._create_provider()

            assert extension.provider == mock_instance
            mock_provider.assert_called_once()

    def test_create_provider_bitcoin(self, extension):
        """Test Bitcoin provider creation."""
        extension.blockchain = "bitcoin"

        with patch(
            "zephyrex.extensions.crypto.BitCoin.BitcoinProvider"
        ) as mock_provider:
            mock_instance = MagicMock()
            mock_provider.return_value = mock_instance

            extension._create_provider()

            assert extension.provider == mock_instance
            mock_provider.assert_called_once()

    def test_create_provider_ethereum(self, extension):
        """Test Ethereum provider creation."""
        extension.blockchain = "ethereum"

        with patch(
            "zephyrex.extensions.crypto.Ethereum.EthereumProvider"
        ) as mock_provider:
            mock_instance = MagicMock()
            mock_provider.return_value = mock_instance

            extension._create_provider()

            assert extension.provider == mock_instance
            mock_provider.assert_called_once()

    def test_create_provider_unsupported_blockchain(self, extension):
        """Test provider creation with unsupported blockchain."""
        extension.blockchain = "unsupported_blockchain"

        with patch("zephyrex.extensions.crypto.EXT_Crypto.logger") as mock_logger:
            extension._create_provider()

            assert extension.provider is None
            mock_logger.error.assert_called_with(
                "Unsupported blockchain: unsupported_blockchain"
            )

    def test_create_provider_import_error(self, extension):
        """Test provider creation with import error."""
        extension.blockchain = "solana"

        with patch(
            "zephyrex.extensions.crypto.Solana.SolanaProvider",
            side_effect=ImportError("Module not found"),
        ), patch("zephyrex.extensions.crypto.EXT_Crypto.logger") as mock_logger:

            extension._create_provider()

            assert extension.provider is None
            mock_logger.warning.assert_called()

    def test_register_commands_with_provider(self, extension):
        """Test command registration when provider is available."""
        mock_provider = MagicMock()
        mock_provider.commands = {"test_command": MagicMock()}
        extension.provider = mock_provider

        extension._register_commands()

        assert extension.commands == mock_provider.commands

    def test_register_commands_without_provider(self, extension):
        """Test command registration when provider is not available."""
        extension.provider = None
        extension.blockchain = "solana"

        extension._register_commands()

        assert len(extension.commands) == 4
        assert "Create SOLANA Wallet" in extension.commands
        assert "Get SOLANA Wallet Balance" in extension.commands
        assert "Send SOLANA Token" in extension.commands
        assert "Get SOLANA Transaction" in extension.commands

    @pytest.mark.asyncio
    async def test_no_provider_warning(self, extension):
        """Test warning message when no provider is available."""
        extension.blockchain = "solana"

        result = await extension._no_provider_warning()

        assert "No crypto provider available for solana" in result

    def test_capability_management(self, extension):
        """Test capability management methods."""
        # Test register_capability
        extension.register_capability("test_capability")
        assert "test_capability" in extension.capabilities

        # Test get_registered_capabilities
        capabilities = extension.get_registered_capabilities()
        assert isinstance(capabilities, set)
        assert "test_capability" in capabilities

        # Test get_capabilities
        capabilities = extension.get_capabilities()
        assert isinstance(capabilities, set)

        # Test has_capability
        assert extension.has_capability("test_capability") is True
        assert extension.has_capability("nonexistent_capability") is False

    @pytest.mark.asyncio
    async def test_create_wallet_success(self, extension):
        """Test successful wallet creation."""
        mock_provider = MagicMock()
        mock_provider.create_wallet = MagicMock(
            return_value="wallet_created_successfully"
        )
        extension.provider = mock_provider

        result = await extension.create_wallet()

        assert result["success"] is True
        assert result["result"] == "wallet_created_successfully"
        assert result["blockchain"] == "solana"
        mock_provider.create_wallet.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_wallet_no_provider(self, extension):
        """Test wallet creation without provider."""
        extension.provider = None

        result = await extension.create_wallet()

        assert result["success"] is False
        assert "No crypto provider available" in result["message"]

    @pytest.mark.asyncio
    async def test_get_wallet_balance_success(self, extension):
        """Test successful wallet balance retrieval."""
        mock_provider = MagicMock()
        mock_provider.get_wallet_balance = MagicMock(return_value=1.5)
        extension.provider = mock_provider
        extension.wallet_address = "test_wallet_address"

        result = await extension.get_wallet_balance("test_wallet_address")

        assert result["success"] is True
        assert result["balance"] == 1.5
        assert result["wallet_address"] == "test_wallet_address"
        assert result["blockchain"] == "solana"
        mock_provider.get_wallet_balance.assert_called_once_with("test_wallet_address")

    @pytest.mark.asyncio
    async def test_get_wallet_balance_no_provider(self, extension):
        """Test wallet balance retrieval without provider."""
        extension.provider = None

        result = await extension.get_wallet_balance()

        assert result["success"] is False
        assert "No crypto provider available" in result["message"]

    @pytest.mark.asyncio
    async def test_send_native_token_success(self, extension):
        """Test successful native token sending."""
        mock_provider = MagicMock()
        mock_provider.send_native_token = MagicMock(return_value="transaction_hash_123")
        extension.provider = mock_provider

        result = await extension.send_native_token("recipient_wallet", 0.5)

        assert result["success"] is True
        assert result["result"] == "transaction_hash_123"
        assert result["to_wallet"] == "recipient_wallet"
        assert result["amount"] == 0.5
        assert result["blockchain"] == "solana"
        mock_provider.send_native_token.assert_called_once_with(
            None, "recipient_wallet", 0.5
        )

    @pytest.mark.asyncio
    async def test_send_native_token_no_provider(self, extension):
        """Test native token sending without provider."""
        extension.provider = None

        result = await extension.send_native_token("recipient_wallet", 0.5)

        assert result["success"] is False
        assert "No crypto provider available" in result["message"]

    @pytest.mark.asyncio
    async def test_get_transaction_info_success(self, extension):
        """Test successful transaction info retrieval."""
        mock_provider = MagicMock()
        mock_transaction_info = {
            "hash": "tx_123",
            "status": "confirmed",
            "amount": 1.0,
            "fee": 0.001,
        }
        mock_provider.get_transaction_info = MagicMock(
            return_value=mock_transaction_info
        )
        extension.provider = mock_provider

        result = await extension.get_transaction_info("tx_123")

        assert result["success"] is True
        assert result["result"] == mock_transaction_info
        assert result["tx_signature"] == "tx_123"
        assert result["blockchain"] == "solana"
        mock_provider.get_transaction_info.assert_called_once_with("tx_123")

    @pytest.mark.asyncio
    async def test_get_transaction_info_no_provider(self, extension):
        """Test transaction info retrieval without provider."""
        extension.provider = None

        result = await extension.get_transaction_info("tx_123")

        assert result["success"] is False
        assert "No crypto provider available" in result["message"]

    @pytest.mark.asyncio
    async def test_get_wallet_transactions_success(self, extension):
        """Test successful wallet transactions retrieval."""
        mock_provider = MagicMock()
        mock_transactions = [
            {"hash": "tx_1", "amount": 1.0},
            {"hash": "tx_2", "amount": 0.5},
        ]
        mock_provider.get_wallet_transactions = MagicMock(
            return_value=mock_transactions
        )
        extension.provider = mock_provider
        extension.wallet_address = "test_wallet"

        result = await extension.get_wallet_transactions("test_wallet", 5)

        assert result["success"] is True
        assert result["transactions"] == mock_transactions
        assert result["count"] == 2
        assert result["wallet_address"] == "test_wallet"
        mock_provider.get_wallet_transactions.assert_called_once_with("test_wallet", 5)

    @pytest.mark.asyncio
    async def test_get_wallet_transactions_no_provider(self, extension):
        """Test wallet transactions retrieval without provider."""
        extension.provider = None

        result = await extension.get_wallet_transactions("test_wallet")

        assert result["success"] is False
        assert "No crypto provider available" in result["message"]

    @pytest.mark.asyncio
    async def test_get_wallet_transactions_not_supported(self, extension):
        """Test wallet transactions retrieval when not supported by provider."""
        mock_provider = MagicMock()
        # Provider doesn't have get_wallet_transactions method
        del mock_provider.get_wallet_transactions
        extension.provider = mock_provider
        extension.blockchain = "bitcoin"

        result = await extension.get_wallet_transactions("test_wallet")

        assert result["success"] is False
        assert "Transaction history not supported for bitcoin" in result["message"]

    @pytest.mark.asyncio
    async def test_ability_error_handling(self, extension):
        """Test error handling in abilities."""
        mock_provider = MagicMock()
        mock_provider.create_wallet = MagicMock(side_effect=Exception("Provider error"))
        extension.provider = mock_provider

        result = await extension.create_wallet()

        assert result["success"] is False
        assert "Error creating wallet" in result["message"]

    def test_lifecycle_methods(self, extension):
        """Test extension lifecycle methods."""
        # Test on_start
        assert extension.on_start() is True

        # Test on_stop
        extension.provider = MagicMock()
        assert extension.on_stop() is True
        assert extension.provider is None

        # Test on_startup and on_shutdown
        extension.on_startup()  # Should not raise exception
        extension.on_shutdown()  # Should not raise exception

    def test_validate_config_success(self, extension):
        """Test successful configuration validation."""
        with patch("builtins.__import__"):
            issues = extension.validate_config()
            assert isinstance(issues, list)

    def test_validate_config_missing_requests(self, extension):
        """Test configuration validation with missing requests library."""
        with patch("builtins.__import__", side_effect=ImportError("Module not found")):
            issues = extension.validate_config()

            assert len(issues) > 0
            assert any("Requests library not installed" in issue for issue in issues)

    def test_validate_config_missing_cryptography(self, extension):
        """Test configuration validation with missing cryptography library."""

        def mock_import(name, *args, **kwargs):
            if name == "cryptography":
                raise ImportError("Module not found")
            return MagicMock()

        with patch("builtins.__import__", side_effect=mock_import):
            issues = extension.validate_config()

            assert any(
                "Cryptography library not installed" in issue for issue in issues
            )

    def test_validate_config_no_blockchain(self, extension):
        """Test configuration validation with no blockchain."""
        extension.blockchain = ""

        issues = extension.validate_config()

        assert any("Blockchain not specified" in issue for issue in issues)

    def test_validate_config_unsupported_blockchain(self, extension):
        """Test configuration validation with unsupported blockchain."""
        extension.blockchain = "unsupported"

        issues = extension.validate_config()

        assert any("Unsupported blockchain: unsupported" in issue for issue in issues)

    def test_validate_config_solana_missing_library(self, extension):
        """Test configuration validation when Solana library is missing."""
        extension.blockchain = "solana"

        def mock_import(name, *args, **kwargs):
            if name == "solana":
                raise ImportError("Module not found")
            return MagicMock()

        with patch("builtins.__import__", side_effect=mock_import):
            issues = extension.validate_config()

            assert any("Solana library not installed" in issue for issue in issues)

    def test_validate_config_bitcoin_missing_library(self, extension):
        """Test configuration validation when Bitcoin library is missing."""
        extension.blockchain = "bitcoin"

        def mock_import(name, *args, **kwargs):
            if name == "bitcoin":
                raise ImportError("Module not found")
            return MagicMock()

        with patch("builtins.__import__", side_effect=mock_import):
            issues = extension.validate_config()

            assert any("Bitcoin library not installed" in issue for issue in issues)

    def test_validate_config_ethereum_missing_library(self, extension):
        """Test configuration validation when Ethereum library is missing."""
        extension.blockchain = "ethereum"

        def mock_import(name, *args, **kwargs):
            if name == "web3":
                raise ImportError("Module not found")
            return MagicMock()

        with patch("builtins.__import__", side_effect=mock_import):
            issues = extension.validate_config()

            assert any("Web3 library not installed" in issue for issue in issues)

    def test_validate_config_private_key_warning(self, extension):
        """Test configuration validation with private key warning."""
        extension.wallet_private_key = "test_private_key"

        issues = extension.validate_config()

        assert any("WARNING: Private key detected" in issue for issue in issues)

    def test_get_required_permissions(self, extension):
        """Test required permissions."""
        permissions = extension.get_required_permissions()
        assert isinstance(permissions, list)
        assert len(permissions) > 0

        expected_permissions = [
            "crypto:wallet:create",
            "crypto:wallet:read",
            "crypto:transactions:create",
            "crypto:transactions:read",
            "crypto:balance:read",
            "crypto:token:send",
        ]
        assert set(permissions) == set(expected_permissions)

    def test_custom_configuration(self):
        """Test extension with custom configuration."""
        extension = EXT_Crypto(
            blockchain="ethereum",
            api_uri="https://mainnet.infura.io",
            wallet_address="0x123...",
            wallet_private_key="test_key",
        )

        assert extension.blockchain == "ethereum"
        assert extension.api_uri == "https://mainnet.infura.io"
        assert extension.wallet_address == "0x123..."
        assert extension.wallet_private_key == "test_key"

    def test_provider_with_commands(self, extension):
        """Test provider that has commands attribute."""
        mock_provider = MagicMock()
        mock_provider.commands = {
            "create_wallet": MagicMock(),
            "send_token": MagicMock(),
        }
        extension.provider = mock_provider

        extension._register_commands()

        assert extension.commands == mock_provider.commands

    def test_provider_without_commands(self, extension):
        """Test provider that doesn't have commands attribute."""
        mock_provider = MagicMock()
        del mock_provider.commands  # Remove commands attribute
        extension.provider = mock_provider
        extension.blockchain = "bitcoin"

        extension._register_commands()

        # Should fall back to placeholder commands
        assert "Create BITCOIN Wallet" in extension.commands

    @pytest.mark.asyncio
    async def test_all_abilities_with_different_blockchains(self, extension):
        """Test all abilities work with different blockchains."""
        for blockchain in ["solana", "bitcoin", "ethereum"]:
            extension.blockchain = blockchain
            extension.provider = None

            # All abilities should return provider not available error
            result = await extension.create_wallet()
            assert result["success"] is False
            assert f"No crypto provider available for {blockchain}" in result["message"]

            result = await extension.get_wallet_balance()
            assert result["success"] is False

            result = await extension.send_native_token("test_wallet", 1.0)
            assert result["success"] is False

            result = await extension.get_transaction_info("test_tx")
            assert result["success"] is False

    def test_provider_settings_passed(self, extension):
        """Test that settings are passed to provider."""
        extension.settings = {"custom_setting": "value"}
        extension.blockchain = "solana"

        with patch("zephyrex.extensions.crypto.Solana.SolanaProvider") as mock_provider:
            extension._create_provider()

            # Check that settings were passed to provider
            mock_provider.assert_called_once()
            call_kwargs = mock_provider.call_args[1]
            assert "custom_setting" in call_kwargs
            assert call_kwargs["custom_setting"] == "value"

    def test_provider_with_extension_id(self, extension):
        """Test that extension ID is passed to provider."""
        extension.blockchain = "solana"

        with patch("zephyrex.extensions.crypto.Solana.SolanaProvider") as mock_provider:
            extension._create_provider()

            mock_provider.assert_called_once()
            call_kwargs = mock_provider.call_args[1]
            assert call_kwargs["extension_id"] == "crypto"

    def test_provider_with_wallet_credentials(self, extension):
        """Test that wallet credentials are passed to provider."""
        extension.blockchain = "solana"
        extension.api_uri = "https://api.mainnet-beta.solana.com"
        extension.wallet_address = "test_address"
        extension.wallet_private_key = "test_key"

        with patch("zephyrex.extensions.crypto.Solana.SolanaProvider") as mock_provider:
            extension._create_provider()

            mock_provider.assert_called_once()
            call_kwargs = mock_provider.call_args[1]
            assert call_kwargs["api_uri"] == "https://api.mainnet-beta.solana.com"
            assert call_kwargs["wallet_address"] == "test_address"
            assert call_kwargs["wallet_private_key"] == "test_key"


if __name__ == "__main__":
    pytest.main([__file__])
