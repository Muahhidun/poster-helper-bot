from datetime import datetime, date
from decimal import Decimal
from sqlalchemy import String, Integer, Date, Numeric, Text, Enum as SQLEnum, DateTime, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from typing import Optional, List
import enum

class Base(DeclarativeBase):
    pass


class ProjectStatus(enum.Enum):
    """Статус проекта"""
    active = "active"
    sold = "sold"
    archived = "archived"


class Payer(enum.Enum):
    """Кто оплатил расход"""
    author = "author"
    serik = "serik"
    common = "common"


class CapitalOperationType(enum.Enum):
    """Тип операции с капиталом"""
    initial = "initial"      # Начальный капитал
    deposit = "deposit"      # Пополнение
    withdrawal = "withdrawal"  # Инкассация (вывод)


class Project(Base):
    """Проект (автомобиль)"""
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    vin: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    buy_date: Mapped[date] = mapped_column(Date, nullable=False)
    buy_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[ProjectStatus] = mapped_column(SQLEnum(ProjectStatus), default=ProjectStatus.active)
    sell_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    sell_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Отношения
    expenses: Mapped[List["Expense"]] = relationship("Expense", back_populates="project")
    incomes: Mapped[List["Income"]] = relationship("Income", back_populates="project")

    def __repr__(self):
        return f"<Project(id={self.id}, title='{self.title}', status={self.status.value})>"


class Expense(Base):
    """Расход"""
    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("projects.id"), nullable=True)
    payer: Mapped[Payer] = mapped_column(SQLEnum(Payer), nullable=False)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Отношения
    project: Mapped[Optional["Project"]] = relationship("Project", back_populates="expenses")

    def __repr__(self):
        return f"<Expense(id={self.id}, amount={self.amount}, category='{self.category}')>"


class Income(Base):
    """Доход (продажа авто)"""
    __tablename__ = "incomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False, default="Продажа авто")
    project_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("projects.id"), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Отношения
    project: Mapped[Optional["Project"]] = relationship("Project", back_populates="incomes")

    def __repr__(self):
        return f"<Income(id={self.id}, amount={self.amount}, source='{self.source}')>"


class CapitalOperation(Base):
    """Операции с капиталом (пополнение, инкассация)"""
    __tablename__ = "capital_operations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    type: Mapped[CapitalOperationType] = mapped_column(SQLEnum(CapitalOperationType), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    who: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # author/serik для withdrawal/deposit
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<CapitalOperation(id={self.id}, type={self.type.value}, amount={self.amount})>"
