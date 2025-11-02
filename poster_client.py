"""Poster API Client"""
import aiohttp
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from config import POSTER_BASE_URL, POSTER_TOKEN, POSTER_USER_ID

logger = logging.getLogger(__name__)


class PosterClient:
    """Client for interacting with Poster API"""

    def __init__(self, telegram_user_id: Optional[int] = None):
        """
        Initialize Poster client for a specific user

        Args:
            telegram_user_id: Telegram user ID for multi-tenant support.
                             If None, uses config values (legacy mode)
        """
        if telegram_user_id:
            # Multi-tenant mode: load from database
            from database import get_database
            db = get_database()
            user_data = db.get_user(telegram_user_id)

            if not user_data:
                raise ValueError(f"User not found in database: {telegram_user_id}")

            self.base_url = user_data['poster_base_url']
            self.token = user_data['poster_token']
            self.user_id = user_data['poster_user_id']
            self.telegram_user_id = telegram_user_id
        else:
            # Legacy mode: use config
            self.base_url = POSTER_BASE_URL
            self.token = POSTER_TOKEN
            self.user_id = POSTER_USER_ID
            self.telegram_user_id = None

        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        """Close aiohttp session"""
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(self, method: str, endpoint: str, params: Dict = None, data: Dict = None, use_json: bool = True) -> Dict:
        """
        Make API request to Poster

        Args:
            method: HTTP method (GET, POST)
            endpoint: API endpoint
            params: Query parameters
            data: Request body data
            use_json: If True, send as JSON. If False, send as form-urlencoded (default: True)
        """
        session = await self._get_session()
        url = f"{self.base_url}/{endpoint}"

        # Add token to params
        if params is None:
            params = {}
        params['token'] = self.token

        try:
            logger.debug(f"Poster API {method} {endpoint}: params={params}, data={data}")

            if method.upper() == 'GET':
                async with session.get(url, params=params) as response:
                    result = await response.json()
            elif method.upper() == 'POST':
                if use_json:
                    # Send as JSON (Content-Type: application/json)
                    async with session.post(url, params=params, json=data) as response:
                        result = await response.json()
                else:
                    # Send as form-urlencoded (Content-Type: application/x-www-form-urlencoded)
                    async with session.post(url, params=params, data=data) as response:
                        result = await response.json()
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            logger.debug(f"Poster API response: {result}")

            # Check for errors
            if 'error' in result:
                error = result['error']
                # Handle both error formats: int error code or dict with message
                if isinstance(error, dict):
                    error_msg = error.get('message', 'Unknown error')
                    error_code = error.get('code', 0)
                else:
                    # Error is just a code (int)
                    error_code = error
                    error_msg = f"Error code {error_code}"
                raise Exception(f"Poster API error ({error_code}): {error_msg}")

            return result

        except aiohttp.ClientError as e:
            logger.error(f"Poster API request failed: {e}")
            raise Exception(f"Не удалось подключиться к Poster API: {e}")

    # === Finance Methods ===

    async def get_accounts(self) -> List[Dict]:
        """Get list of all accounts (cash/bank)"""
        result = await self._request('GET', 'finance.getAccounts')
        return result.get('response', [])

    async def get_categories(self) -> List[Dict]:
        """Get list of finance categories"""
        result = await self._request('GET', 'finance.getCategories')
        return result.get('response', [])

    async def get_transactions(self, date_from: str, date_to: str) -> List[Dict]:
        """
        Get list of transactions for a date range

        Args:
            date_from: Start date in format "YYYYMMDD"
            date_to: End date in format "YYYYMMDD"

        Returns:
            List of transactions with details (category, account, amount, date, comment)
        """
        result = await self._request('GET', 'finance.getTransactions', params={
            'dateFrom': date_from,
            'dateTo': date_to
        })
        return result.get('response', [])

    async def remove_transaction(self, transaction_id: int) -> bool:
        """
        Delete a transaction (receipt/order) from Poster

        Args:
            transaction_id: ID of the transaction to delete

        Returns:
            True if deletion was successful

        Raises:
            Exception: If deletion fails
        """
        try:
            result = await self._request('POST', 'transactions.removeTransaction', data={
                'transaction_id': transaction_id
            })

            # Проверяем ответ
            response = result.get('response', {})
            err_code = response.get('err_code', -1)

            if err_code == 0:
                logger.info(f"✅ Чек {transaction_id} успешно удалён")
                return True
            else:
                raise Exception(f"Удаление не выполнено, err_code: {err_code}")

        except Exception as e:
            logger.error(f"❌ Ошибка удаления чека {transaction_id}: {e}")
            raise

    async def create_transaction(
        self,
        transaction_type: int,  # 0=expense, 1=income, 2=transfer
        category_id: int,
        account_from_id: int,
        amount: int,  # in KZT (passed directly to API)
        date: Optional[str] = None,
        comment: str = "",
        account_to_id: Optional[int] = None
    ) -> int:
        """
        Create finance transaction

        Args:
            transaction_type: 0=expense, 1=income, 2=transfer
            category_id: Category ID from Poster
            account_from_id: Source account ID
            amount: Amount in KZT (e.g., 7500 = 7,500₸, passed directly to API)
            date: Date in format "YYYY-MM-DD HH:MM:SS", defaults to now
            comment: Transaction comment
            account_to_id: Destination account (for transfers)

        Returns:
            Transaction ID
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Poster API expects amount in KZT, not tiyins
        # (tested: passing 1 creates transaction of 1 KZT, not 0.01 KZT)
        data = {
            'type': transaction_type,
            'account_from': account_from_id,
            'amount_from': int(amount),
            'user_id': self.user_id,
            'date': date,
            'comment': comment
        }

        # Category is only for expenses and income, not for transfers
        if transaction_type != 2:
            data['category'] = category_id

        if transaction_type == 2 and account_to_id:  # transfer
            data['account_to'] = account_to_id
            # For transfers, amount_to is required (same as amount_from for simple transfers)
            data['amount_to'] = int(amount)

        logger.info(f"Creating transaction: {data}")
        result = await self._request('POST', 'finance.createTransactions', data=data)

        # Poster API may return int directly or dict with 'response' key
        if isinstance(result, dict):
            transaction_id = result.get('response')
        else:
            transaction_id = result  # Direct int response

        if transaction_id:
            logger.info(f"✅ Transaction created successfully: ID={transaction_id}")
            return transaction_id
        else:
            raise Exception("Transaction creation failed: no ID returned")

    # === Storage Methods ===

    async def get_storages(self) -> List[Dict]:
        """Get list of warehouses/storages"""
        result = await self._request('GET', 'storage.getStorages')
        return result.get('response', [])

    async def get_supplies(self, date_from: str = None, date_to: str = None) -> List[Dict]:
        """
        Get list of supplies

        Args:
            date_from: Start date in format "YYYYMMDD"
            date_to: End date in format "YYYYMMDD"
        """
        params = {}
        if date_from:
            params['dateFrom'] = date_from
        if date_to:
            params['dateTo'] = date_to

        result = await self._request('GET', 'storage.getSupplies', params=params)
        return result.get('response', [])

    # === Menu Methods ===

    async def get_ingredients(self) -> List[Dict]:
        """Get list of ingredients"""
        result = await self._request('GET', 'menu.getIngredients')
        return result.get('response', [])

    async def get_products(self) -> List[Dict]:
        """Get list of products"""
        result = await self._request('GET', 'menu.getProducts')
        return result.get('response', [])

    async def get_suppliers(self) -> List[Dict]:
        """Get list of suppliers"""
        # Note: Poster API doesn't have a direct getSuppliers endpoint
        # Suppliers are returned from getSupplies with supplier info
        # For now, we'll need to maintain a local mapping
        return []

    async def create_supply(
        self,
        supplier_id: int,
        storage_id: int,
        date: str,
        ingredients: List[Dict],  # [{"id": 198, "num": 5, "price": 250}, ...]
        account_id: int = 1,
        comment: str = ""
    ) -> int:
        """
        Create supply (поставка)

        Args:
            supplier_id: Supplier ID
            storage_id: Warehouse ID (default: 1 for "Продукты")
            date: Date in format "YYYY-MM-DD HH:MM:SS"
            ingredients: List of ingredients with id, num (quantity), price
            account_id: Payment account ID (default: 1 for Kaspi Pay)
            comment: Supply comment

        Returns:
            Supply ID

        Note: Uses form-urlencoded format, not JSON
        """
        # Calculate total amount
        total_amount = sum(
            int(item['num'] * item['price'])
            for item in ingredients
        )

        # Build form data
        data = {
            'date': date,
            'supplier_id': supplier_id,
            'storage_id': storage_id,
            'source': 'manage',
            'type': 1,  # Normal supply
            'supply_comment': comment
        }

        # Add ingredients
        for idx, item in enumerate(ingredients):
            # Poster API num field: convert floats to string to avoid error 701
            # Integer values are kept as int for compatibility
            num = item['num']
            num_for_api = num
            if isinstance(num, float):
                if num.is_integer():
                    num_for_api = int(num)
                else:
                    # Send as string for decimal values (e.g., "4.5", "1.2", "0.38")
                    num_for_api = str(num)

            # For calculations, use original numeric value
            ingredient_sum = int(num * item['price'])
            data[f'ingredients[{idx}][id]'] = item['id']
            data[f'ingredients[{idx}][num]'] = num_for_api
            data[f'ingredients[{idx}][price]'] = int(item['price'])
            data[f'ingredients[{idx}][ingredient_sum]'] = ingredient_sum
            data[f'ingredients[{idx}][tax_id]'] = item.get('tax_id', 0)
            data[f'ingredients[{idx}][packing]'] = item.get('packing', 1)

        # Add transaction (payment)
        data['transactions[0][transaction_id]'] = ''
        data['transactions[0][account_id]'] = account_id
        data['transactions[0][date]'] = date
        data['transactions[0][amount]'] = total_amount
        data['transactions[0][delete]'] = 0

        logger.info(f"Creating supply: supplier={supplier_id}, items={len(ingredients)}, total={total_amount}")
        logger.info(f"Supply data: {data}")

        # storage.createSupply требует form-urlencoded, не JSON!
        result = await self._request('POST', 'storage.createSupply', data=data, use_json=False)

        supply_id = result.get('response')
        if supply_id:
            logger.info(f"✅ Supply created successfully: ID={supply_id}")
            return supply_id
        else:
            raise Exception("Supply creation failed: no ID returned")

    async def update_supply(
        self,
        supply_id: int,
        supplier_id: int,
        storage_id: int,
        date: str,
        ingredients: List[Dict],
        account_id: int = 1,
        comment: str = "",
        status: int = 1  # 0=draft, 1=active
    ) -> bool:
        """
        Update supply and activate it (change status from draft to active)

        Args:
            supply_id: Supply ID to update
            supplier_id: Supplier ID
            storage_id: Warehouse ID
            date: Date in format "YYYY-MM-DD HH:MM:SS"
            ingredients: List of ingredients with id, num (quantity), price
            account_id: Payment account ID
            comment: Supply comment
            status: 0=draft, 1=active (default: 1 to activate)

        Returns:
            True if successful

        Note: Requires ALL supply parameters, not just status
        """
        # Calculate total amount
        total_amount = sum(
            int(item['num'] * item['price'])
            for item in ingredients
        )

        # Build form data (same as create_supply but with supply_id and status)
        data = {
            'supply_id': supply_id,
            'date': date,
            'supplier_id': supplier_id,
            'storage_id': storage_id,
            'source': 'manage',
            'type': 1,
            'status': status,
            'supply_comment': comment
        }

        # Add ingredients
        for idx, item in enumerate(ingredients):
            num = item['num']
            num_for_api = num
            if isinstance(num, float):
                if num.is_integer():
                    num_for_api = int(num)
                else:
                    num_for_api = str(num)

            ingredient_sum = int(num * item['price'])
            data[f'ingredients[{idx}][id]'] = item['id']
            data[f'ingredients[{idx}][num]'] = num_for_api
            data[f'ingredients[{idx}][price]'] = int(item['price'])
            data[f'ingredients[{idx}][ingredient_sum]'] = ingredient_sum
            data[f'ingredients[{idx}][tax_id]'] = item.get('tax_id', 0)
            data[f'ingredients[{idx}][packing]'] = item.get('packing', 1)

        # Add transaction (payment)
        data['transactions[0][transaction_id]'] = ''
        data['transactions[0][account_id]'] = account_id
        data['transactions[0][date]'] = date
        data['transactions[0][amount]'] = total_amount
        data['transactions[0][delete]'] = 0

        logger.info(f"Updating supply #{supply_id}: status={status}, items={len(ingredients)}")

        result = await self._request('POST', 'storage.updateSupply', data=data, use_json=False)

        if result.get('success'):
            logger.info(f"✅ Supply #{supply_id} updated successfully (status={status})")
            return True
        else:
            raise Exception(f"Supply update failed: {result}")


# Cache for user-specific clients
_poster_clients: Dict[Optional[int], PosterClient] = {}


def get_poster_client(telegram_user_id: Optional[int] = None) -> PosterClient:
    """
    Get PosterClient instance for a specific user

    Args:
        telegram_user_id: Telegram user ID. If None, uses legacy config mode

    Returns:
        PosterClient instance for the user
    """
    global _poster_clients

    if telegram_user_id not in _poster_clients:
        _poster_clients[telegram_user_id] = PosterClient(telegram_user_id)

    return _poster_clients[telegram_user_id]
