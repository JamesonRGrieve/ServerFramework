import logging
from typing import Any, Optional

from bitcoinlib.keys import Key
from bitcoinlib.wallets import Wallet, wallet_create_or_open

from zephyrex.extensions.crypto.PRV_Crypto import AbstractCryptoProvider


class BitcoinProvider(AbstractCryptoProvider):
    """
    Bitcoin blockchain provider implementation.
    """

    def __init__(
        self,
        api_key: str = "",
        api_uri: str = "",
        wallet_address: str = "",
        wallet_private_key: str = "",
        wallet_name: str = "default_wallet",
        wallet_password: str = "",
        network: str = "testnet",
        extension_id: Optional[str] = None,
        agent_name: str = "gpt4free",
        ApiClient: Optional[Any] = None,
        conversation_name: Optional[str] = None,
        **kwargs,
    ):
        """
        Initialize the Bitcoin provider with configuration parameters.
        """
        self.wallet_name = wallet_name
        self.wallet_password = wallet_password
        self.network = network
        self.wallet = None

        # Try to open existing wallet if wallet_name is provided
        if wallet_name:
            try:
                self.wallet = wallet_create_or_open(
                    wallet_name, network=network, password=wallet_password
                )
                if not wallet_address:
                    # Get the first address if none was provided
                    key = self.wallet.get_key()
                    wallet_address = key.address
            except Exception as e:
                logging.warning(f"Could not open existing wallet: {str(e)}")
                self.wallet = None

        # If wallet_private_key is provided and no wallet is loaded, create a wallet from private key
        if wallet_private_key and self.wallet is None:
            try:
                self.wallet = Wallet.create(
                    wallet_name or "imported_wallet",
                    keys=wallet_private_key,
                    network=network,
                )
                if not wallet_address:
                    key = self.wallet.get_key()
                    wallet_address = key.address
            except Exception as e:
                logging.warning(f"Could not create wallet from private key: {str(e)}")
                self.wallet = None

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

        # Add additional Bitcoin-specific commands
        self.commands.update(
            {
                "Get Bitcoin Network Fee Estimate": self.get_fee_estimate,
                "Get Bitcoin UTXO List": self.get_utxo_list,
                "Import Bitcoin Private Key": self.import_private_key,
            }
        )

    def create_wallet(self) -> str:
        """
        Creates a new Bitcoin wallet.
        Returns:
            String with wallet details
        """
        try:
            # Generate a new wallet with a random name if not provided
            import time

            wallet_name = self.wallet_name or f"wallet_{int(time.time())}"
            self.wallet = Wallet.create(
                wallet_name, network=self.network, password=self.wallet_password
            )
            # Get the first address and private key
            key = self.wallet.get_key()
            self.wallet_address = key.address
            private_key = key.wif

            return (
                f"Created new Bitcoin wallet on {self.network} network.\n"
                f"Wallet Name: {wallet_name}\n"
                f"Address: {self.wallet_address}\n"
                f"Private Key: {private_key}\n"
                f"IMPORTANT: Store this private key securely! Anyone with access to this key can control your funds."
            )
        except Exception as e:
            logging.error(f"Error creating Bitcoin wallet: {str(e)}")
            return f"Error creating Bitcoin wallet: {str(e)}"

    def get_wallet_balance(self, wallet_address: Optional[str] = None) -> str:
        """
        Retrieves the BTC balance for the given wallet address.
        Args:
            wallet_address: Optional wallet address (uses default if not provided)
        Returns:
            String with wallet balance information
        """
        if wallet_address is None:
            wallet_address = self.wallet_address

        if wallet_address is None and self.wallet is None:
            return "No wallet address specified and no default wallet available."

        try:
            # If using the active wallet
            if self.wallet and (
                wallet_address is None or wallet_address == self.wallet_address
            ):
                # Update wallet to get latest transactions
                self.wallet.utxos_update()
                balance_satoshi = self.wallet.balance()
                balance_btc = balance_satoshi / 100000000  # Convert satoshi to BTC
                return f"Wallet {self.wallet_address} balance: {balance_btc} BTC ({balance_satoshi} satoshi)"
            else:
                key = Key(wallet_address, network=self.network)
                balance_satoshi = key.balance()
                balance_btc = balance_satoshi / 100000000  # Convert satoshi to BTC
                return f"Wallet {wallet_address} balance: {balance_btc} BTC ({balance_satoshi} satoshi)"
        except Exception as e:
            logging.error(f"Error retrieving balance: {str(e)}")
            return f"Error retrieving balance: {str(e)}"

    def send_native_token(
        self,
        from_wallet: Optional[str] = None,
        to_wallet: str = "",
        amount: float = 0.0,
    ) -> str:
        """
        Sends a specified amount of BTC from one wallet to another.
        Args:
            from_wallet: Optional sender wallet address (uses default if not provided)
            to_wallet: Recipient wallet address
            amount: Amount of BTC to send
        Returns:
            String with transaction result
        """
        if not self.wallet:
            return "No wallet available to send funds. Please create or import a wallet first."

        if from_wallet is not None and from_wallet != self.wallet_address:
            return "Can only send from the loaded wallet. The specified 'from_wallet' doesn't match the loaded wallet."

        if not to_wallet:
            return "No recipient wallet address specified."

        try:
            # Convert BTC to satoshi
            amount_satoshi = int(amount * 100000000)

            # Create and send transaction
            # For simplicity, we're using the wallet's send method which handles UTXO selection
            tx = self.wallet.send_to(to_wallet, amount_satoshi, fee=None, offline=False)
            tx_id = tx.hash

            return f"Transaction submitted: {tx_id}\nSent {amount} BTC to {to_wallet}"
        except Exception as e:
            logging.error(f"Error sending BTC: {str(e)}")
            return f"Error sending BTC: {str(e)}"

    def get_transaction_info(self, tx_hash: str) -> str:
        """
        Retrieves information about a specific transaction using its hash.
        Args:
            tx_hash: Transaction hash
        Returns:
            String with transaction details
        """
        if not tx_hash:
            return "No transaction hash provided."

        try:
            wallet = None
            if not self.wallet:
                # Create a temporary wallet to access the service provider
                wallet = Wallet.create(
                    "temp_wallet", network=self.network, db_uri=":memory:"
                )
            else:
                wallet = self.wallet

            # Get transaction details
            service = wallet.services.get_service_provider(network=self.network)
            tx_info = service.gettransaction(tx_hash)

            # Format transaction details
            if not tx_info:
                return f"Transaction {tx_hash} not found."

            status = "Confirmed" if tx_info.get("confirmations", 0) > 0 else "Pending"
            fee = tx_info.get("fee", 0) / 100000000  # Convert to BTC
            confirmations = tx_info.get("confirmations", 0)
            size = tx_info.get("size", 0)

            # Extract input and output information
            inputs = tx_info.get("inputs", [])
            outputs = tx_info.get("outputs", [])

            inputs_str = "\n".join(
                [
                    f"  - From: {inp.get('address', 'Unknown')} Amount: {inp.get('value', 0)/100000000} BTC"
                    for inp in inputs
                ]
            )

            outputs_str = "\n".join(
                [
                    f"  - To: {out.get('address', 'Unknown')} Amount: {out.get('value', 0)/100000000} BTC"
                    for out in outputs
                ]
            )

            return (
                f"Transaction: {tx_hash}\n"
                f"Status: {status} ({confirmations} confirmations)\n"
                f"Size: {size} bytes\n"
                f"Fee: {fee} BTC\n"
                f"Inputs:\n{inputs_str}\n"
                f"Outputs:\n{outputs_str}"
            )
        except Exception as e:
            logging.error(f"Error retrieving transaction info: {str(e)}")
            return f"Error retrieving transaction info: {str(e)}"

    def get_fee_estimate(self, blocks_target: int = 6) -> str:
        """
        Get an estimate of the current Bitcoin transaction fee.
        Args:
            blocks_target: Target number of blocks for confirmation
        Returns:
            String with fee estimate
        """
        try:
            wallet = None
            if not self.wallet:
                # Create a temporary wallet to access the service provider
                wallet = Wallet.create(
                    "temp_wallet", network=self.network, db_uri=":memory:"
                )
            else:
                wallet = self.wallet

            # Get fee estimate from service provider
            service = wallet.services.get_service_provider(network=self.network)
            fee_per_kb = service.estimatefee(blocks_target)

            # Calculate fee for typical transaction sizes
            typical_tx_size = 250  # bytes for a typical 1-input, 2-output transaction
            typical_fee_satoshi = int((fee_per_kb / 1024) * typical_tx_size)
            typical_fee_btc = typical_fee_satoshi / 100000000

            return (
                f"Estimated fee for confirmation within {blocks_target} blocks:\n"
                f"Fee rate: {fee_per_kb} satoshi/KB\n"
                f"Typical transaction fee: {typical_fee_satoshi} satoshi ({typical_fee_btc} BTC)"
            )
        except Exception as e:
            logging.error(f"Error getting fee estimate: {str(e)}")
            return f"Error getting fee estimate: {str(e)}"

    def get_utxo_list(self, wallet_address: Optional[str] = None) -> str:
        """
        Get a list of unspent transaction outputs (UTXOs) for a wallet.
        Args:
            wallet_address: Optional wallet address (uses default if not provided)
        Returns:
            String with UTXO information
        """
        if wallet_address is None:
            wallet_address = self.wallet_address

        if wallet_address is None and self.wallet is None:
            return "No wallet address specified and no default wallet available."

        try:
            # If using the active wallet
            if self.wallet and (
                wallet_address is None or wallet_address == self.wallet_address
            ):
                # Update UTXO information
                self.wallet.utxos_update()
                utxos = self.wallet.utxos()

                if not utxos:
                    return f"No UTXOs found for wallet {self.wallet_address}"

                utxo_details = []
                for utxo in utxos:
                    utxo_details.append(
                        f"  - TXID: {utxo.hash} Index: {utxo.output_n} "
                        f"Amount: {utxo.value/100000000} BTC Confirmations: {utxo.confirmations}"
                    )

                return f"UTXOs for wallet {self.wallet_address}:\n" + "\n".join(
                    utxo_details
                )
            else:
                # For an external address, create a key to query UTXOs
                key = Key(wallet_address, network=self.network)
                utxos = key.utxos()

                if not utxos:
                    return f"No UTXOs found for wallet {wallet_address}"

                utxo_details = []
                for utxo in utxos:
                    utxo_details.append(
                        f"  - TXID: {utxo.hash} Index: {utxo.output_n} "
                        f"Amount: {utxo.value/100000000} BTC Confirmations: {utxo.confirmations}"
                    )

                return f"UTXOs for wallet {wallet_address}:\n" + "\n".join(utxo_details)
        except Exception as e:
            logging.error(f"Error retrieving UTXOs: {str(e)}")
            return f"Error retrieving UTXOs: {str(e)}"

    def import_private_key(self, private_key: str) -> str:
        """
        Import a Bitcoin private key and set it as the default wallet.
        Args:
            private_key: The private key in WIF format
        Returns:
            String with result of importing the key
        """
        if not private_key:
            return "No private key provided."

        try:
            # Create a new wallet from the private key
            import time

            wallet_name = f"imported_wallet_{int(time.time())}"

            new_wallet = Wallet.create(
                wallet_name,
                keys=private_key,
                network=self.network,
                password=self.wallet_password,
            )

            # Get the address and replace the current wallet
            key = new_wallet.get_key()
            address = key.address
            self.wallet = new_wallet
            self.wallet_address = address

            return (
                f"Successfully imported private key.\n"
                f"Address: {address}\n"
                f"Network: {self.network}"
            )
        except Exception as e:
            logging.error(f"Error importing private key: {str(e)}")
            return f"Error importing private key: {str(e)}"

    def get_blockchain_name(self) -> str:
        """
        Get the name of the blockchain this provider interacts with.
        Returns:
            Name of the blockchain
        """
        return "Bitcoin"

    def get_native_token_name(self) -> str:
        """
        Get the name of the native token for this blockchain.
        Returns:
            Name of the native token
        """
        return "BTC"
