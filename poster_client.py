"""Poster API Client"""
import aiohttp
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from config import POSTER_BASE_URL, POSTER_TOKEN, POSTER_USER_ID

logger = logging.getLogger(__name__)


class PosterClient:
    """Client for interacting with Poster API"""

    def __init__(
        self,
        telegram_user_id: Optional[int] = None,
        poster_token: Optional[str] = None,
        poster_user_id: Optional[str] = None,
        poster_base_url: Optional[str] = None
    ):
        """
        Initialize Poster client for a specific user or with explicit credentials

        Args:
            telegram_user_id: Telegram user ID for multi-tenant support
            poster_token: Explicit token (for multi-account mode)
            poster_user_id: Explicit user ID (for multi-account mode)
            poster_base_url: Explicit base URL (for multi-account mode)

        Priority:
            1. If explicit credentials provided, use them (multi-account mode)
            2. Else if telegram_user_id provided, load from database
            3. Else use config values (legacy mode)
        """
        # Multi-account mode with explicit credentials
        if poster_token and poster_user_id and poster_base_url:
            self.base_url = poster_base_url
            self.token = poster_token
            self.user_id = poster_user_id
            self.telegram_user_id = telegram_user_id
        elif telegram_user_id:
            # Load from database (users table)
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

    async def get_cash_shifts(self, date_from: str, date_to: str) -> List[Dict]:
        """
        Get cash register shifts for a date range

        Args:
            date_from: Start date in format "YYYYMMDD"
            date_to: End date in format "YYYYMMDD"

        Returns:
            List of cash shifts with amount_start, amount_end, amount_collection etc.
        """
        result = await self._request('GET', 'finance.getCashShifts', params={
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

    async def update_transaction(
        self,
        transaction_id: int,
        amount: int,
        comment: str = None,
        category_id: int = None
    ) -> bool:
        """
        Update existing finance transaction

        Args:
            transaction_id: ID of transaction to update
            amount: New amount in KZT
            comment: New comment (optional)
            category_id: New category ID (optional)

        Returns:
            True if update was successful
        """
        data = {
            'transaction_id': transaction_id,
            'amount_from': int(amount)
        }

        if comment is not None:
            data['comment'] = comment
        if category_id is not None:
            data['category'] = category_id

        logger.info(f"Updating transaction {transaction_id}: {data}")
        result = await self._request('POST', 'finance.updateTransactions', data=data)

        # Check response
        if isinstance(result, dict):
            response = result.get('response', result)
            # Success typically returns the transaction_id or empty dict
            if response or response == {}:
                logger.info(f"✅ Transaction {transaction_id} updated successfully")
                return True
        return False

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
        result = await self._request('GET', 'storage.getSuppliers')
        return result.get('response', [])

    async def create_supply(
        self,
        supplier_id: int,
        storage_id: int,
        date: str,
        ingredients: List[Dict],  # [{"id": 198, "num": 5, "price": 250, "type": "ingredient"}, ...]
        account_id: int = 1,
        comment: str = ""
    ) -> int:
        """
        Create supply (поставка)

        Args:
            supplier_id: Supplier ID
            storage_id: Warehouse ID (default: 1 for "Продукты")
            date: Date in format "YYYY-MM-DD HH:MM:SS"
            ingredients: List of items with id, num (quantity), price, and optional type
                        type can be 'ingredient' (default), 'semi_product', or 'product'
            account_id: Payment account ID
            comment: Supply comment

        Returns:
            Supply ID

        Note: Uses form-urlencoded format with supply[] wrapper per Poster API docs
        """
        # Poster API storage.createSupply uses supply[] wrapper for parameters
        # and ingredient[] (singular) for items array
        # See: https://dev.joinposter.com/en/docs/v3/web/storage/createSupply

        def _build_supply_data(type_map):
            """Build form data in official Poster API format"""
            data = {
                'supply[date]': date,
                'supply[supplier_id]': supplier_id,
                'supply[storage_id]': storage_id,
                'supply[supply_comment]': comment,
                'supply[account_id]': account_id,
            }

            for idx, item in enumerate(ingredients):
                num = item['num']
                num_for_api = num
                if isinstance(num, float):
                    if num.is_integer():
                        num_for_api = int(num)
                    else:
                        num_for_api = str(num)

                price_for_api = item['price']

                item_type = item.get('type', 'ingredient')
                poster_type = type_map.get(item_type, type_map.get('ingredient', 1))

                data[f'ingredient[{idx}][id]'] = item['id']
                data[f'ingredient[{idx}][type]'] = poster_type
                data[f'ingredient[{idx}][num]'] = num_for_api
                data[f'ingredient[{idx}][sum]'] = price_for_api
                if item.get('packing'):
                    data[f'ingredient[{idx}][packing]'] = item['packing']

            # Payment transaction — without this, Poster creates supply with 0₸ payment
            total_amount = round(sum(
                item['num'] * item['price']
                for item in ingredients
            ), 2)
            data['transactions[0][account_id]'] = account_id
            data['transactions[0][date]'] = date
            data['transactions[0][amount]'] = total_amount
            data['transactions[0][delete]'] = 0

            return data

        def _build_legacy_data(type_map):
            """Build form data in legacy flat format (works for some accounts)"""
            total_amount = round(sum(
                item['num'] * item['price']
                for item in ingredients
            ), 2)

            data = {
                'date': date,
                'supplier_id': supplier_id,
                'storage_id': storage_id,
                'source': 'manage',
                'type': 1,
                'supply_comment': comment
            }

            for idx, item in enumerate(ingredients):
                num = item['num']
                num_for_api = num
                if isinstance(num, float):
                    if num.is_integer():
                        num_for_api = int(num)
                    else:
                        num_for_api = str(num)

                price_for_api = item['price']
                ingredient_sum = round(num * price_for_api, 2)

                item_type = item.get('type', 'ingredient')
                poster_type = type_map.get(item_type, type_map.get('ingredient', 1))

                data[f'ingredients[{idx}][id]'] = item['id']
                data[f'ingredients[{idx}][type]'] = poster_type
                data[f'ingredients[{idx}][num]'] = num_for_api
                data[f'ingredients[{idx}][price]'] = price_for_api
                data[f'ingredients[{idx}][ingredient_sum]'] = ingredient_sum
                data[f'ingredients[{idx}][tax_id]'] = item.get('tax_id', 0)
                data[f'ingredients[{idx}][packing]'] = item.get('packing', 1)

            data['transactions[0][account_id]'] = account_id
            data['transactions[0][date]'] = date
            data['transactions[0][amount]'] = total_amount
            data['transactions[0][delete]'] = 0

            return data

        # Poster API docs type mapping: product=1, ingredient=4, modifier=5
        docs_type_map = {'ingredient': 4, 'semi_product': 4, 'product': 1}
        # Legacy type mapping (worked for some accounts): ingredient=1, semi_product=2, product=4
        legacy_type_map = {'ingredient': 1, 'semi_product': 2, 'product': 4}

        logger.info(f"Creating supply: supplier={supplier_id}, storage={storage_id}, "
                    f"items={len(ingredients)}, account_id={account_id}")

        # Try documented format first (supply[] wrapper, ingredient singular, type: ingredient=4)
        data = _build_supply_data(docs_type_map)
        logger.info(f"Supply data (docs format): {data}")

        try:
            result = await self._request('POST', 'storage.createSupply', data=data, use_json=False)
        except Exception as e1:
            error_msg1 = str(e1)
            logger.warning(f"Docs format failed: {error_msg1}. Trying legacy format...")

            # Try legacy flat format with legacy type mapping (ingredient=1)
            data = _build_legacy_data(legacy_type_map)
            logger.info(f"Supply data (legacy format): {data}")

            try:
                result = await self._request('POST', 'storage.createSupply', data=data, use_json=False)
            except Exception as e2:
                error_msg2 = str(e2)
                logger.warning(f"Legacy format also failed: {error_msg2}. Trying docs format with legacy types...")

                # Try docs format but with legacy type mapping
                data = _build_supply_data(legacy_type_map)
                logger.info(f"Supply data (docs format + legacy types): {data}")

                try:
                    result = await self._request('POST', 'storage.createSupply', data=data, use_json=False)
                except Exception as e3:
                    ingredient_ids = [item['id'] for item in ingredients]
                    logger.error(f"All supply formats failed. IDs: {ingredient_ids}. "
                                f"Errors: docs={error_msg1}, legacy={error_msg2}, mixed={e3}")
                    raise Exception(
                        f"Ошибка Poster API: Не удалось создать поставку. "
                        f"Ингредиенты ID: {ingredient_ids}. "
                        f"Проверьте, что ингредиенты существуют в этом заведении."
                    )

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
        # Calculate total amount preserving decimal precision
        total_amount = round(sum(
            item['num'] * item['price']
            for item in ingredients
        ), 2)

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

            price_for_api = item['price']
            ingredient_sum = round(num * price_for_api, 2)

            # Poster API type: 1=ingredient, 2=semi-product (полуфабрикат), 4=product (товар)
            item_type = item.get('type', 'ingredient')
            type_map = {'ingredient': 1, 'semi_product': 2, 'product': 4}
            poster_type = type_map.get(item_type, 1)

            data[f'ingredients[{idx}][id]'] = item['id']
            data[f'ingredients[{idx}][type]'] = poster_type
            data[f'ingredients[{idx}][num]'] = num_for_api
            data[f'ingredients[{idx}][price]'] = price_for_api
            data[f'ingredients[{idx}][ingredient_sum]'] = ingredient_sum
            data[f'ingredients[{idx}][tax_id]'] = item.get('tax_id', 0)
            data[f'ingredients[{idx}][packing]'] = item.get('packing', 1)

        # Add transaction (payment)
        # Note: transaction_id should be omitted for new transactions
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
