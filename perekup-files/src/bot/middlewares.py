from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from src.config import ALLOWED_USER_IDS


class AccessMiddleware(BaseMiddleware):
    """Middleware для проверки доступа пользователя"""

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any]
    ) -> Any:
        user_id = event.from_user.id

        if user_id not in ALLOWED_USER_IDS:
            if isinstance(event, Message):
                await event.answer(
                    "❌ У вас нет доступа к этому боту.\n\n"
                    "Обратитесь к администратору для получения доступа."
                )
            elif isinstance(event, CallbackQuery):
                await event.answer("❌ У вас нет доступа", show_alert=True)
            return

        return await handler(event, data)
