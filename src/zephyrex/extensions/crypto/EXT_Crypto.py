import inspect
from typing import Any, Dict, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension, ability
from zephyrex.lib.Dependencies import EXT_Dependency, PIP_Dependency
from zephyrex.lib.Logging import logger


class EXT_Crypto(AbstractStaticExtension):
    """
    Cryptocurrency extension for AGInfrastructure.
    Provides blockchain interaction functionality for multiple blockchains
    including Solana, Bitcoin, and Ethereum.

    Component loading (DB, BLL, EP) is handled automatically by the import system
    based on file naming conventions.
    """

    # Extension metadata
    name = "crypto"
    version = "1.0.0"
    description = "Cryptocurrency extension for blockchain interactions"

    # Define dependencies
    ext_dependencies = [
        EXT_Dependency(
            name="core",
            friendly_name="Core Extension",
            optional=False,
            reason="Required for base cryptocurrency functionality",
        ),
        EXT_Dependency(
            name="labels",
            friendly_name="Labels Extension",
            optional=True,
            reason="Optional for labeling and organizing crypto transactions",
        ),
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="solana",
            friendly_name="Solana Python SDK",
            optional=True,
            reason="Required for Solana blockchain integration",
            semver=">=0.30.0",
        ),
        PIP_Dependency(
            name="bitcoin",
            friendly_name="Bitcoin Python Library",
            optional=True,
            reason="Required for Bitcoin blockchain integration",
            semver=">=1.1.42",
        ),
        PIP_Dependency(
            name="web3",
            friendly_name="Web3.py Ethereum Library",
            optional=True,
            reason="Required for Ethereum blockchain integration",
            semver=">=6.0.0",
        ),
        PIP_Dependency(
            name="requests",
            friendly_name="HTTP Requests Library",
            optional=False,
            reason="Required for API communications with blockchain networks",
            semver=">=2.28.0",
        ),
        PIP_Dependency(
            name="cryptography",
            friendly_name="Cryptography Library",
            optional=False,
            reason="Required for secure wallet operations",
            semver=">=3.0.0",
        ),
    ]

    sys_dependencies = []

    # Define database tables (none for this extension)
    db_tables = []

    # Define what capabilities this extension provides
    capabilities = [
        "wallet_management",
        "transaction_creation",
        "balance_checking",
        "blockchain_interaction",
        "token_operations",
        "transaction_monitoring",
    ]

    def __init__(
        self,
        blockchain: str = "solana",
        api_uri: str = "",
        wallet_address: str = "",
        wallet_private_key: str = "",
        **kwargs,
    ):
        """
        Initialize the cryptocurrency extension.
        """
        super().__init__(**kwargs)

        self.blockchain = blockchain.lower()
        self.api_uri = api_uri
        self.wallet_address = wallet_address
        self.wallet_private_key = wallet_private_key
        self.provider = None
        self.commands = {}

    def on_initialize(self) -> bool:
        """Initialize the cryptocurrency extension with the appropriate provider."""
        logger.debug("Initializing Cryptocurrency Extension...")

        try:
            self._create_provider()
            self._register_commands()

            # Register capabilities
            for capability in self.capabilities:
                self.register_capability(capability)

            logger.debug("Cryptocurrency extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Cryptocurrency extension: {str(e)}")
            return False

    def _create_provider(self):
        """Create the appropriate cryptocurrency provider based on blockchain."""
        try:
            if self.blockchain == "solana":
                try:
                    from zephyrex.extensions.crypto.Solana import SolanaProvider

                    self.provider = SolanaProvider(
                        api_uri=self.api_uri,
                        wallet_address=self.wallet_address,
                        wallet_private_key=self.wallet_private_key,
                        extension_id=self.name,
                        **getattr(self, "settings", {}),
                    )
                    logger.debug("Solana provider created successfully")

                except ImportError as e:
                    logger.warning(f"Could not import Solana provider: {e}")
                    self.provider = None

            elif self.blockchain == "bitcoin":
                try:
                    from zephyrex.extensions.crypto.BitCoin import BitcoinProvider

                    self.provider = BitcoinProvider(
                        api_uri=self.api_uri,
                        wallet_address=self.wallet_address,
                        wallet_private_key=self.wallet_private_key,
                        extension_id=self.name,
                        **getattr(self, "settings", {}),
                    )
                    logger.debug("Bitcoin provider created successfully")

                except ImportError as e:
                    logger.warning(f"Could not import Bitcoin provider: {e}")
                    self.provider = None

            elif self.blockchain == "ethereum":
                try:
                    from zephyrex.extensions.crypto.Ethereum import EthereumProvider

                    self.provider = EthereumProvider(
                        api_uri=self.api_uri,
                        wallet_address=self.wallet_address,
                        wallet_private_key=self.wallet_private_key,
                        extension_id=self.name,
                        **getattr(self, "settings", {}),
                    )
                    logger.debug("Ethereum provider created successfully")

                except ImportError as e:
                    logger.warning(f"Could not import Ethereum provider: {e}")
                    self.provider = None

            else:
                logger.error(f"Unsupported blockchain: {self.blockchain}")
                self.provider = None

        except Exception as e:
            logger.error(f"Error creating cryptocurrency provider: {str(e)}")
            self.provider = None

    def _register_commands(self):
        """Register commands based on available provider."""
        if self.provider and hasattr(self.provider, "commands"):
            self.commands = self.provider.commands
        else:
            # Provide placeholder commands that warn about missing provider
            blockchain_name = self.blockchain.upper()
            self.commands = {
                f"Create {blockchain_name} Wallet": self._no_provider_warning,
                f"Get {blockchain_name} Wallet Balance": self._no_provider_warning,
                f"Send {blockchain_name} Token": self._no_provider_warning,
                f"Get {blockchain_name} Transaction": self._no_provider_warning,
            }

    async def _no_provider_warning(self, *args, **kwargs) -> str:
        """Warning message when a provider is not available."""
        return f"No crypto provider available for {self.blockchain}. Please check your configuration."

    @staticmethod
    async def _call_provider(method: Any, *args: Any, **kwargs: Any) -> Any:
        """Invoke a provider method, awaiting it only if it returns an awaitable.

        Blockchain providers are heterogeneous: Solana's SDK is async-only
        while Bitcoin/Ethereum's SDKs are synchronous. This lets callers
        treat every provider ability uniformly regardless of which kind
        backs `self.provider`.
        """
        result = method(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    def register_capability(self, capability: str):
        """Register a new capability."""
        if capability not in self.capabilities:
            self.capabilities.append(capability)

    def get_registered_capabilities(self) -> Set[str]:
        """Return currently registered capabilities."""
        return set(self.capabilities)

    def get_capabilities(self) -> Set[str]:
        """Return the capabilities this extension provides."""
        return set(self.capabilities)

    @ability("create_wallet")
    async def create_wallet(self) -> Dict[str, Any]:
        """Create a new cryptocurrency wallet."""
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            result = await self._call_provider(self.provider.create_wallet)
            return {"success": True, "result": result, "blockchain": self.blockchain}

        except Exception as e:
            logger.error(f"Error creating wallet: {e}")
            return {"success": False, "message": f"Error creating wallet: {str(e)}"}

    @ability("get_wallet_balance")
    async def get_wallet_balance(
        self, wallet_address: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get the balance of a wallet."""
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            balance = await self._call_provider(
                self.provider.get_wallet_balance, wallet_address
            )
            return {
                "success": True,
                "balance": balance,
                "wallet_address": wallet_address or self.wallet_address,
                "blockchain": self.blockchain,
            }

        except Exception as e:
            logger.error(f"Error getting wallet balance: {e}")
            return {
                "success": False,
                "message": f"Error getting wallet balance: {str(e)}",
            }

    @ability("send_native_token")
    async def send_native_token(self, to_wallet: str, amount: float) -> Dict[str, Any]:
        """Send native tokens from one wallet to another."""
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            result = await self._call_provider(
                self.provider.send_native_token, None, to_wallet, amount
            )
            return {
                "success": True,
                "result": result,
                "to_wallet": to_wallet,
                "amount": amount,
                "blockchain": self.blockchain,
            }

        except Exception as e:
            logger.error(f"Error sending native token: {e}")
            return {
                "success": False,
                "message": f"Error sending native token: {str(e)}",
            }

    @ability("get_transaction_info")
    async def get_transaction_info(self, tx_signature: str) -> Dict[str, Any]:
        """Get information about a specific transaction."""
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            result = await self._call_provider(
                self.provider.get_transaction_info, tx_signature
            )
            return {
                "success": True,
                "result": result,
                "tx_signature": tx_signature,
                "blockchain": self.blockchain,
            }

        except Exception as e:
            logger.error(f"Error getting transaction info: {e}")
            return {
                "success": False,
                "message": f"Error getting transaction info: {str(e)}",
            }

    @ability("get_wallet_transactions")
    async def get_wallet_transactions(
        self, wallet_address: Optional[str] = None, limit: int = 10
    ) -> Dict[str, Any]:
        """Get recent transactions for a wallet."""
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            # Check if provider has this method
            if hasattr(self.provider, "get_wallet_transactions"):
                transactions = await self._call_provider(
                    self.provider.get_wallet_transactions, wallet_address, limit
                )
                return {
                    "success": True,
                    "transactions": transactions,
                    "count": len(transactions) if transactions else 0,
                    "wallet_address": wallet_address or self.wallet_address,
                }
            else:
                return {
                    "success": False,
                    "message": f"Transaction history not supported for {self.blockchain}",
                }

        except Exception as e:
            logger.error(f"Error getting wallet transactions: {e}")
            return {
                "success": False,
                "message": f"Error getting wallet transactions: {str(e)}",
            }

    def on_start(self) -> bool:
        """Start the Cryptocurrency extension."""
        try:
            logger.debug("Cryptocurrency extension started successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to start Cryptocurrency extension: {e}")
            return False

    def on_stop(self) -> bool:
        """Stop the Cryptocurrency extension."""
        try:
            if self.provider:
                # Clean up provider resources if needed
                self.provider = None

            logger.debug("Cryptocurrency extension stopped successfully")
            return True
        except Exception as e:
            logger.error(f"Error stopping Cryptocurrency extension: {e}")
            return False

    def validate_config(self) -> List[str]:
        """Validate the extension configuration."""
        issues = []

        # Check for required Python packages
        try:
            import requests
        except ImportError:
            issues.append(
                "Requests library not installed - API communications will not work"
            )

        try:
            import cryptography
        except ImportError:
            issues.append(
                "Cryptography library not installed - secure operations will not work"
            )

        # Blockchain-specific validation
        if not self.blockchain:
            issues.append("Blockchain not specified")
        elif self.blockchain not in ["solana", "bitcoin", "ethereum"]:
            issues.append(f"Unsupported blockchain: {self.blockchain}")

        # Check blockchain-specific libraries
        if self.blockchain == "solana":
            try:
                import solana
            except ImportError:
                issues.append(
                    "Solana library not installed - Solana operations will not work"
                )

        elif self.blockchain == "bitcoin":
            try:
                import bitcoin
            except ImportError:
                issues.append(
                    "Bitcoin library not installed - Bitcoin operations will not work"
                )

        elif self.blockchain == "ethereum":
            try:
                import web3
            except ImportError:
                issues.append(
                    "Web3 library not installed - Ethereum operations will not work"
                )

        # Security warnings for private keys
        if self.wallet_private_key:
            issues.append(
                "WARNING: Private key detected in configuration - ensure secure storage"
            )

        return issues

    def get_required_permissions(self) -> List[str]:
        """Return the list of permissions required by this extension."""
        return [
            "crypto:wallet:create",
            "crypto:wallet:read",
            "crypto:transactions:create",
            "crypto:transactions:read",
            "crypto:balance:read",
            "crypto:token:send",
        ]

    def on_startup(self):
        """Called during application startup."""
        logger.debug("Cryptocurrency extension startup hook called")

    def on_shutdown(self):
        """Called during application shutdown."""
        logger.debug("Cryptocurrency extension shutdown hook called")

    def has_capability(self, capability: str) -> bool:
        """Check if this extension has a specific capability."""
        return capability in self.capabilities
