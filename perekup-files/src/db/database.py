from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from src.config import DATABASE_URL
from src.db.models import Base

# Создаем движок
engine = create_async_engine(
    DATABASE_URL,
    echo=False,  # Установите True для отладки SQL запросов
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

# Фабрика сессий
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def init_db():
    """Инициализация базы данных (создание таблиц)"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Получить сессию БД"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def get_db_session():
    """Context manager для получения сессии"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
