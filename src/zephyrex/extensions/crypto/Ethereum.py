import logging
from typing import Any, Optional

from eth_account import Account
from web3 import Web3

from zephyrex.extensions.crypto.PRV_Crypto import AbstractCryptoProvider


class EthereumProvider(AbstractCryptoProvider):
    """
    Ethereum blockchain provider implementation.
    """

    def __init__(
        self,
        api_key: str = "",
        api_uri: str = "https://goerli.infura.io/v3/",
        wallet_address: str = "",
        wallet_private_key: str = "",
        chain_id: int = 5,  # Default to Goerli testnet
        extension_id: Optional[str] = None,
        agent_name: str = "gpt4free",
        ApiClient: Optional[Any] = None,
        conversation_name: Optional[str] = None,
        **kwargs,
    ):
        """
        Initialize the Ethereum provider with configuration parameters.
        """
        self.chain_id = chain_id

        # Set up web3 provider
        if api_uri.startswith("http"):
            self.web3 = Web3(Web3.HTTPProvider(f"{api_uri}{api_key}"))
        elif api_uri.startswith("ws"):
            self.web3 = Web3(Web3.WebsocketProvider(f"{api_uri}{api_key}"))
        else:
            self.web3 = Web3(
                Web3.HTTPProvider(f"https://goerli.infura.io/v3/{api_key}")
            )

        # If a private key was provided, set up the account
        self.account = None
        if wallet_private_key:
            self.account = Account.from_key(wallet_private_key)
            if not wallet_address:
                wallet_address = self.account.address

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

        # Add additional Ethereum-specific commands
        self.commands.update(
            {
                "Get Ethereum Gas Price": self.get_gas_price,
                "Get Ethereum ERC20 Balance": self.get_erc20_balance,
                "Send Ethereum ERC20 Token": self.send_erc20_token,
            }
        )

    def create_wallet(self) -> str:
        """
        Creates a new Ethereum wallet.
        Returns:
            String with wallet details
        """
        try:
            # Enable the unsafe private key export
            Account.enable_unaudited_hdwallet_features()

            # Generate a new Ethereum account
            acct = Account.create()
            self.account = acct
            self.wallet_address = acct.address
            self.wallet_private_key = acct.key.hex()

            # Generate a mnemonic
            mnemonic = Account.create_with_mnemonic()[1]

            return (
                f"Created new Ethereum wallet.\n"
                f"Address: {self.wallet_address}\n"
                f"Private Key: {self.wallet_private_key}\n"
                f"Mnemonic Phrase: {mnemonic}\n\n"
                f"IMPORTANT: Store this information securely! Anyone with access to the private key or mnemonic can control your funds."
            )
        except Exception as e:
            logging.error(f"Error creating Ethereum wallet: {str(e)}")
            return f"Error creating Ethereum wallet: {str(e)}"

    def get_wallet_balance(self, wallet_address: Optional[str] = None) -> str:
        """
        Retrieves the ETH balance for the given wallet address.
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
            # Check if address is valid
            if not self.web3.is_address(wallet_address):
                return f"Invalid Ethereum address: {wallet_address}"

            # Get the balance in wei
            balance_wei = self.web3.eth.get_balance(wallet_address)

            # Convert wei to ether
            balance_eth = self.web3.from_wei(balance_wei, "ether")

            return f"Wallet {wallet_address} balance: {balance_eth} ETH"
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
        Sends a specified amount of ETH from one wallet to another.
        Args:
            from_wallet: Optional sender wallet address (uses default if not provided)
            to_wallet: Recipient wallet address
            amount: Amount of ETH to send
        Returns:
            String with transaction result
        """
        if self.account is None:
            return "No private key available to sign transactions. Please create a wallet or import a private key."

        if (
            from_wallet is not None
            and from_wallet.lower() != self.account.address.lower()
        ):
            return (
                "Can only send from the wallet associated with the loaded private key."
            )

        if not to_wallet:
            return "No recipient wallet address specified."

        try:
            # Check if addresses are valid
            if not self.web3.is_address(to_wallet):
                return f"Invalid recipient Ethereum address: {to_wallet}"

            # Convert ETH to wei
            amount_wei = self.web3.to_wei(amount, "ether")

            # Get the sender's nonce
            nonce = self.web3.eth.get_transaction_count(self.account.address)

            # Get gas price
            gas_price = self.web3.eth.gas_price

            # Build transaction
            tx = {
                "nonce": nonce,
                "to": to_wallet,
                "value": amount_wei,
                "gas": 21000,  # Standard gas limit for ETH transfer
                "gasPrice": gas_price,
                "chainId": self.chain_id,
            }

            # Sign the transaction
            signed_tx = self.web3.eth.account.sign_transaction(
                tx, self.wallet_private_key
            )

            # Send the transaction
            tx_hash = self.web3.eth.send_raw_transaction(signed_tx.rawTransaction)

            return f"Transaction submitted: {tx_hash.hex()}\nSent {amount} ETH to {to_wallet}"
        except Exception as e:
            logging.error(f"Error sending ETH: {str(e)}")
            return f"Error sending ETH: {str(e)}"

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
            # Check if the hash is valid hex
            if not tx_hash.startswith("0x"):
                tx_hash = f"0x{tx_hash}"

            # Get transaction details
            tx = self.web3.eth.get_transaction(tx_hash)
            if not tx:
                return f"Transaction {tx_hash} not found."

            # Get transaction receipt to check if confirmed
            try:
                receipt = self.web3.eth.get_transaction_receipt(tx_hash)
                status = "Success" if receipt.status == 1 else "Failed"
                confirmations = self.web3.eth.block_number - receipt.blockNumber
                gas_used = receipt.gasUsed if receipt else "Unknown"
            except:
                status = "Pending"
                confirmations = "Pending"
                gas_used = "Pending"

            # Format transaction details
            from_addr = tx.get("from", "Unknown")
            to_addr = tx.get("to", "Unknown")
            value = self.web3.from_wei(tx.get("value", 0), "ether")
            gas_price = self.web3.from_wei(tx.get("gasPrice", 0), "gwei")

            return (
                f"Transaction: {tx_hash}\n"
                f"Status: {status}\n"
                f"Confirmations: {confirmations}\n"
                f"From: {from_addr}\n"
                f"To: {to_addr}\n"
                f"Value: {value} ETH\n"
                f"Gas Price: {gas_price} Gwei\n"
                f"Gas Used: {gas_used}\n"
                f"Nonce: {tx.get('nonce', 'Unknown')}"
            )
        except Exception as e:
            logging.error(f"Error retrieving transaction info: {str(e)}")
            return f"Error retrieving transaction info: {str(e)}"

    def get_gas_price(self) -> str:
        """
        Get the current gas price on the Ethereum network.
        Returns:
            String with gas price information
        """
        try:
            gas_price_wei = self.web3.eth.gas_price

            # Convert to different units
            gas_price_gwei = self.web3.from_wei(gas_price_wei, "gwei")
            gas_price_eth = self.web3.from_wei(gas_price_wei, "ether")

            # Calculate cost for typical operations
            eth_transfer_gas = 21000
            erc20_transfer_gas = 65000
            swap_gas = 150000

            eth_transfer_cost = eth_transfer_gas * gas_price_eth
            erc20_transfer_cost = erc20_transfer_gas * gas_price_eth
            swap_cost = swap_gas * gas_price_eth

            return (
                f"Current Gas Price: {gas_price_gwei} Gwei ({gas_price_wei} Wei)\n\n"
                f"Estimated transaction costs:\n"
                f"ETH Transfer: {eth_transfer_cost:.8f} ETH\n"
                f"ERC20 Transfer: {erc20_transfer_cost:.8f} ETH\n"
                f"Token Swap: {swap_cost:.8f} ETH"
            )
        except Exception as e:
            logging.error(f"Error getting gas price: {str(e)}")
            return f"Error getting gas price: {str(e)}"

    def get_erc20_balance(
        self, token_address: str, wallet_address: Optional[str] = None
    ) -> str:
        """
        Get the balance of an ERC20 token for a wallet.
        Args:
            token_address: The address of the ERC20 token contract
            wallet_address: Optional wallet address (uses default if not provided)
        Returns:
            String with token balance information
        """
        if not token_address:
            return "No token address provided."

        if wallet_address is None:
            wallet_address = self.wallet_address

        if wallet_address is None:
            return "No wallet address specified."

        if not self.web3.is_address(token_address):
            return f"Invalid Ethereum token address: {token_address}"

        if not self.web3.is_address(wallet_address):
            return f"Invalid Ethereum wallet address: {wallet_address}"

        # ERC20 standard balanceOf function signature
        erc20_abi = [
            {
                "constant": True,
                "inputs": [{"name": "_owner", "type": "address"}],
                "name": "balanceOf",
                "outputs": [{"name": "balance", "type": "uint256"}],
                "type": "function",
            },
            {
                "constant": True,
                "inputs": [],
                "name": "decimals",
                "outputs": [{"name": "", "type": "uint8"}],
                "type": "function",
            },
            {
                "constant": True,
                "inputs": [],
                "name": "symbol",
                "outputs": [{"name": "", "type": "string"}],
                "type": "function",
            },
        ]

        try:
            # Create contract instance
            token_contract = self.web3.eth.contract(
                address=token_address, abi=erc20_abi
            )

            # Get token balance
            balance = token_contract.functions.balanceOf(wallet_address).call()

            # Get token metadata
            try:
                decimals = token_contract.functions.decimals().call()
                symbol = token_contract.functions.symbol().call()

                # Format balance with correct decimals
                formatted_balance = balance / (10**decimals)

                return f"Token Balance for {wallet_address}:\n{formatted_balance} {symbol} ({token_address})"
            except Exception:
                # If metadata retrieval fails, show raw balance
                return f"Token Balance for {wallet_address}:\n{balance} tokens ({token_address})"
        except Exception as e:
            logging.error(f"Error getting ERC20 balance: {str(e)}")
            return f"Error getting ERC20 balance: {str(e)}"

    def send_erc20_token(
        self,
        token_address: str,
        to_wallet: str = "",
        amount: float = 0.0,
        from_wallet: Optional[str] = None,
    ) -> str:
        """
        Send ERC20 tokens from one wallet to another.
        Args:
            token_address: The address of the ERC20 token contract
            to_wallet: Recipient wallet address
            amount: Amount of tokens to send
            from_wallet: Optional sender wallet address (uses default if not provided)
        Returns:
            String with transaction result
        """
        if self.account is None:
            return "No private key available to sign transactions. Please create a wallet or import a private key."

        if (
            from_wallet is not None
            and from_wallet.lower() != self.account.address.lower()
        ):
            return (
                "Can only send from the wallet associated with the loaded private key."
            )

        if not token_address or not to_wallet:
            return "Token address and recipient wallet address are required."

        if not self.web3.is_address(token_address) or not self.web3.is_address(
            to_wallet
        ):
            return "Invalid Ethereum addresses provided."

        # ERC20 transfer function signature
        erc20_abi = [
            {
                "constant": False,
                "inputs": [
                    {"name": "_to", "type": "address"},
                    {"name": "_value", "type": "uint256"},
                ],
                "name": "transfer",
                "outputs": [{"name": "", "type": "bool"}],
                "type": "function",
            },
            {
                "constant": True,
                "inputs": [],
                "name": "decimals",
                "outputs": [{"name": "", "type": "uint8"}],
                "type": "function",
            },
        ]

        try:
            # Create contract instance
            token_contract = self.web3.eth.contract(
                address=token_address, abi=erc20_abi
            )

            # Try to get actual decimals
            try:
                decimals = token_contract.functions.decimals().call()
            except Exception:
                # Use default if failed
                decimals = 18

            # Convert amount to token units
            amount_in_units = int(amount * (10**decimals))

            # Build transaction
            from_address = self.account.address
            nonce = self.web3.eth.get_transaction_count(from_address)

            # Estimate gas
            gas_estimate = token_contract.functions.transfer(
                to_wallet, amount_in_units
            ).estimate_gas({"from": from_address})

            # Get gas price
            gas_price = self.web3.eth.gas_price

            # Build transaction for token transfer
            token_tx = token_contract.functions.transfer(
                to_wallet, amount_in_units
            ).build_transaction(
                {
                    "chainId": self.chain_id,
                    "gas": gas_estimate,
                    "gasPrice": gas_price,
                    "nonce": nonce,
                }
            )

            # Sign the transaction
            signed_tx = self.web3.eth.account.sign_transaction(
                token_tx, self.wallet_private_key
            )

            # Send the transaction
            tx_hash = self.web3.eth.send_raw_transaction(signed_tx.rawTransaction)

            return f"ERC20 token transaction submitted: {tx_hash.hex()}\nSent {amount} tokens to {to_wallet}"
        except Exception as e:
            logging.error(f"Error sending ERC20 tokens: {str(e)}")
            return f"Error sending ERC20 tokens: {str(e)}"

    def get_blockchain_name(self) -> str:
        """
        Get the name of the blockchain this provider interacts with.
        Returns:
            Name of the blockchain
        """
        return "Ethereum"

    def get_native_token_name(self) -> str:
        """
        Get the name of the native token for this blockchain.
        Returns:
            Name of the native token
        """
        return "ETH"
