from abc import ABC, abstractmethod
from typing import Any, Awaitable, Dict, List, Optional, Union

# Blockchain SDKs backing concrete providers are heterogeneous: Solana's
# client (solana-py 0.40+) is async-only, while Bitcoin (bitcoinlib) and
# Ethereum (web3.py's default HTTPProvider) are synchronous. Concrete
# providers may implement these as either `def` or `async def`; callers
# (EXT_Crypto's abilities) await the result only when it is awaitable.
MaybeAwaitable = Union[str, Awaitable[str]]


class AbstractCryptoProvider(ABC):
    """
    Abstract base class for all cryptocurrency blockchain providers used by
    the Crypto extension (Solana, Bitcoin, Ethereum).

    Concrete providers are lightweight, directly-instantiated clients that
    hold their own credentials/config (wallet address, private key, API
    endpoint) and expose async wallet/transaction operations. EXT_Crypto's
    async abilities await these methods directly.
    """

    def __init__(
        self,
        api_key: str = "",
        api_uri: str = "",
        wallet_address: str = "",
        wallet_private_key: str = "",
        extension_id: Optional[str] = None,
        agent_name: str = "gpt4free",
        ApiClient: Optional[Any] = None,
        conversation_name: Optional[str] = None,
        wait_between_requests: int = 1,
        wait_after_failure: int = 3,
        **kwargs: Any,
    ) -> None:
        self.api_key = api_key
        self.api_uri = api_uri
        self.wallet_address = wallet_address
        self.wallet_private_key = wallet_private_key
        self.extension_id = extension_id
        self.agent_name = agent_name
        self.ApiClient = ApiClient
        self.conversation_name = conversation_name
        self.wait_between_requests = wait_between_requests
        self.wait_after_failure = wait_after_failure
        self.settings: Dict[str, Any] = kwargs

        # Set up common crypto wallet commands that all providers need to implement
        self.commands: Dict[str, Any] = {
            f"Create {self.get_blockchain_name()} Wallet": self.create_wallet,
            f"Get {self.get_blockchain_name()} Wallet Balance": self.get_wallet_balance,
            f"Send {self.get_native_token_name()}": self.send_native_token,
            f"Get Transaction Info from {self.get_blockchain_name()}": self.get_transaction_info,
        }

    @abstractmethod
    def create_wallet(self) -> MaybeAwaitable:
        """
        Create a new cryptocurrency wallet.
        Returns:
            String (or awaitable of a string) with wallet details
        """

    @abstractmethod
    def get_wallet_balance(self, wallet_address: Optional[str] = None) -> MaybeAwaitable:
        """
        Get the balance of a wallet.
        Args:
            wallet_address: Optional wallet address (uses default if not provided)
        Returns:
            String (or awaitable of a string) with wallet balance information
        """

    @abstractmethod
    def send_native_token(
        self,
        from_wallet: Optional[str] = None,
        to_wallet: str = "",
        amount: float = 0.0,
    ) -> MaybeAwaitable:
        """
        Send native tokens from one wallet to another.
        Args:
            from_wallet: Optional sender wallet address (uses default if not provided)
            to_wallet: Recipient wallet address
            amount: Amount of tokens to send
        Returns:
            String (or awaitable of a string) with transaction result
        """

    @abstractmethod
    def get_transaction_info(self, tx_signature: str) -> MaybeAwaitable:
        """
        Get information about a specific transaction.
        Args:
            tx_signature: Transaction signature or hash
        Returns:
            String (or awaitable of a string) with transaction details
        """

    @abstractmethod
    def get_blockchain_name(self) -> str:
        """
        Get the name of the blockchain this provider interacts with.
        Returns:
            String identifier for the blockchain (e.g., "Solana", "Ethereum")
        """

    @abstractmethod
    def get_native_token_name(self) -> str:
        """
        Get the name of the native token for this blockchain.
        Returns:
            String identifier for the native token (e.g., "SOL", "ETH")
        """

    @staticmethod
    def services() -> List[str]:
        """
        Return a list of services provided by this provider.
        Returns:
            List of service identifiers
        """
        return ["crypto", "wallet", "blockchain"]

    def get_extension_info(self) -> Dict[str, Any]:
        """
        Get information about the crypto wallet extension.
        Returns:
            Dict containing extension metadata
        """
        return {
            "name": "Crypto Wallet",
            "type": self.get_blockchain_name(),
            "description": f"Crypto wallet extension for {self.get_blockchain_name()}",
        }
