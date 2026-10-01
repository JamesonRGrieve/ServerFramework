import logging
from typing import Any, Optional

from solana.rpc.async_api import AsyncClient
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.message import Message
from solders.signature import Signature
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

from zephyrex.extensions.crypto.PRV_Crypto import AbstractCryptoProvider


class SolanaProvider(AbstractCryptoProvider):
    """
    Solana blockchain provider implementation.
    """

    def __init__(
        self,
        api_key: str = "",
        api_uri: str = "https://api.devnet.solana.com",
        wallet_address: str = "",
        wallet_private_key: str = "",
        extension_id: Optional[str] = None,
        agent_name: str = "gpt4free",
        ApiClient: Optional[Any] = None,
        conversation_name: Optional[str] = None,
        **kwargs,
    ):
        """
        Initialize the Solana provider with configuration parameters.
        """
        self.client = AsyncClient(api_uri)
        self.wallet_keypair = None

        # If an existing wallet private key is provided, load the keypair
        if wallet_private_key:
            # Here we assume the private key is a base58-encoded string.
            self.wallet_keypair = Keypair.from_base58_string(wallet_private_key)
            self.wallet_address = self.wallet_keypair.pubkey().to_string()
        else:
            self.wallet_address = wallet_address

        super().__init__(
            api_key=api_key,
            api_uri=api_uri,
            wallet_address=wallet_address,
            wallet_private_key=wallet_private_key,
            extension_id=extension_id,
            agent_name=agent_name,
            ApiClient=ApiClient,
            conversation_name=conversation_name,
            **kwargs,
        )

        # Add additional Solana-specific commands
        self.commands.update(
            {
                "Get Recent Solana Transactions": self.get_recent_transactions,
                "Get Solana Token Balance": self.get_token_balance,
                "Airdrop SOL": self.airdrop_sol,
                "Get Solana Token List": self.get_token_list,
            }
        )

    async def create_wallet(self) -> str:
        """
        Creates a new Solana wallet by generating a new keypair.
        Returns:
            String with wallet details
        """
        try:
            new_keypair = Keypair()
            self.wallet_keypair = new_keypair
            self.wallet_address = self.wallet_keypair.pubkey().to_string()
            secret_hex = (
                new_keypair.secret().hex()
            )  # for display; store securely in practice

            return (
                f"Created new Solana wallet.\n"
                f"Public Key: {self.wallet_address}\n"
                f"Secret Key (hex): {secret_hex}"
            )
        except Exception as e:
            logging.error(f"Error creating Solana wallet: {str(e)}")
            return f"Error creating Solana wallet: {str(e)}"

    async def get_wallet_balance(self, wallet_address: Optional[str] = None) -> str:
        """
        Retrieves the SOL balance for the given wallet address.
        Args:
            wallet_address: Optional wallet address (uses default if not provided)
        Returns:
            String with wallet balance information
        """
        if wallet_address is None:
            wallet_address = self.wallet_address

        if wallet_address is None:
            return "No wallet address specified."

        try:
            # Convert the wallet address string to a Pubkey object
            pubkey = Pubkey.from_string(wallet_address)
            response = await self.client.get_balance(pubkey)
            balance_lamports = response.value
            sol_balance = balance_lamports / 1e9
            return f"Wallet {wallet_address} balance: {sol_balance} SOL."
        except Exception as e:
            logging.error(f"Error retrieving balance: {str(e)}")
            return f"Error retrieving balance: {str(e)}"

    async def send_native_token(
        self,
        from_wallet: Optional[str] = None,
        to_wallet: str = "",
        amount: float = 0.0,
    ) -> str:
        """
        Sends a specified amount of SOL from one wallet to another.
        Args:
            from_wallet: Optional sender wallet address (uses default if not provided)
            to_wallet: Recipient wallet address
            amount: Amount of SOL to send
        Returns:
            String with transaction result
        """
        if from_wallet is None:
            from_wallet = self.wallet_address

        if from_wallet is None or self.wallet_keypair is None:
            return "No sender wallet or keypair available."

        if not to_wallet:
            return "No recipient wallet address specified."

        try:
            lamports_amount = int(amount * 1e9)
            from_pubkey = Pubkey.from_string(from_wallet)
            to_pubkey = Pubkey.from_string(to_wallet)

            instruction = transfer(
                TransferParams(
                    from_pubkey=from_pubkey,
                    to_pubkey=to_pubkey,
                    lamports=lamports_amount,
                )
            )
            latest_blockhash = await self.client.get_latest_blockhash()
            message = Message.new_with_blockhash(
                [instruction],
                from_pubkey,
                latest_blockhash.value.blockhash,
            )
            tx = VersionedTransaction(message, [self.wallet_keypair])

            # Send the signed transaction
            response = await self.client.send_transaction(tx)
            return f"Transaction submitted: {response.value}"
        except Exception as e:
            logging.error(f"Error sending SOL: {str(e)}")
            return f"Error sending SOL: {str(e)}"

    async def get_transaction_info(self, tx_signature: str) -> str:
        """
        Retrieves information about a specific transaction using its signature.
        Args:
            tx_signature: Transaction signature
        Returns:
            String with transaction details
        """
        if not tx_signature:
            return "No transaction signature provided."

        try:
            signature = Signature.from_string(tx_signature)
            response = await self.client.get_transaction(signature)
            return f"Transaction info: {response.value}"
        except Exception as e:
            logging.error(f"Error retrieving transaction info: {str(e)}")
            return f"Error retrieving transaction info: {str(e)}"

    async def get_recent_transactions(
        self, wallet_address: Optional[str] = None, limit: int = 10
    ) -> str:
        """
        Retrieves the most recent transaction signatures for the given wallet address.
        Args:
            wallet_address: Optional wallet address (uses default if not provided)
            limit: Maximum number of transactions to retrieve
        Returns:
            String with recent transactions
        """
        if wallet_address is None:
            wallet_address = self.wallet_address

        if wallet_address is None:
            return "No wallet address specified."

        try:
            pubkey = Pubkey.from_string(wallet_address)
            response = await self.client.get_signatures_for_address(pubkey, limit=limit)
            return f"Recent transactions for wallet {wallet_address}: {response.value}"
        except Exception as e:
            logging.error(f"Error retrieving recent transactions: {str(e)}")
            return f"Error retrieving recent transactions: {str(e)}"

    async def get_token_balance(
        self, wallet_address: Optional[str] = None, token_mint: str = ""
    ) -> str:
        """
        Retrieves the balance of a specific SPL token for the given wallet.
        Args:
            wallet_address: Optional wallet address (uses default if not provided)
            token_mint: Token mint address
        Returns:
            String with token balance information
        """
        if wallet_address is None:
            wallet_address = self.wallet_address

        if wallet_address is None:
            return "No wallet address specified."

        if not token_mint:
            return "No token mint address specified."

        return f"Token balance for token {token_mint} in wallet {wallet_address}: [Not implemented]."

    async def airdrop_sol(
        self, wallet_address: Optional[str] = None, amount: float = 0.0
    ) -> str:
        """
        Request a SOL airdrop from the devnet/testnet faucet.
        Args:
            wallet_address: Optional wallet address (uses default if not provided)
            amount: Amount of SOL to airdrop
        Returns:
            String with airdrop result
        """
        if wallet_address is None:
            wallet_address = self.wallet_address

        if wallet_address is None:
            return "No wallet address specified."

        try:
            pubkey = Pubkey.from_string(wallet_address)
            lamports_amount = int(amount * 1e9)
            response = await self.client.request_airdrop(pubkey, lamports_amount)
            return f"Airdrop requested: {response.value}"
        except Exception as e:
            logging.error(f"Error requesting airdrop: {str(e)}")
            return f"Error requesting airdrop: {str(e)}"

    async def get_token_list(self) -> str:
        """
        Returns a simulated list of popular tokens on the Solana network.
        Returns:
            String with token list
        """
        tokens = ["SOL", "USDC", "USDT", "SRM", "RAY"]
        return "Token list: " + ", ".join(tokens)

    def get_blockchain_name(self) -> str:
        """
        Get the name of the blockchain this provider interacts with.
        Returns:
            Name of the blockchain
        """
        return "Solana"

    def get_native_token_name(self) -> str:
        """
        Get the name of the native token for this blockchain.
        Returns:
            Name of the native token
        """
        return "SOL"
