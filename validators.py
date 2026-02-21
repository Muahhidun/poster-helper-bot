"""Pydantic validation models for API endpoints"""
import logging
from datetime import date, datetime
from enum import Enum
from functools import wraps
from typing import List, Literal, Optional

from flask import jsonify, request
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


# ============================================================================
# Enums
# ============================================================================

class ExpenseSource(str, Enum):
    cash = "cash"
    kaspi = "kaspi"
    halyk = "halyk"


class ExpenseType(str, Enum):
    transaction = "transaction"
    supply = "supply"


class CompletionStatus(str, Enum):
    pending = "pending"
    partial = "partial"
    completed = "completed"


class ItemType(str, Enum):
    ingredient = "ingredient"
    semi_product = "semi_product"
    product = "product"


# ============================================================================
# Expense Models
# ============================================================================

class CreateExpenseRequest(BaseModel):
    amount: float = Field(default=0, ge=0, le=100_000_000)
    description: str = Field(default="", max_length=200)
    expense_type: ExpenseType = ExpenseType.transaction
    category: str = Field(default="", max_length=100)
    source: ExpenseSource = ExpenseSource.cash
    account_id: Optional[int] = Field(default=None, ge=1)
    poster_account_id: Optional[int] = Field(default=None, ge=1)


class UpdateExpenseRequest(BaseModel):
    amount: Optional[float] = Field(default=None, ge=0, le=100_000_000)
    description: Optional[str] = Field(default=None, max_length=200)
    category: Optional[str] = Field(default=None, max_length=50)
    source: Optional[ExpenseSource] = None
    account_id: Optional[int] = Field(default=None, ge=1)
    poster_account_id: Optional[int] = Field(default=None, ge=1)
    completion_status: Optional[CompletionStatus] = None


class ProcessExpensesRequest(BaseModel):
    draft_ids: List[int] = Field(..., min_length=1)


class ToggleExpenseTypeRequest(BaseModel):
    expense_type: ExpenseType = ExpenseType.transaction


class UpdateCompletionStatusRequest(BaseModel):
    completion_status: CompletionStatus = CompletionStatus.pending


# ============================================================================
# Supply Models
# ============================================================================

class SupplyItem(BaseModel):
    id: int = Field(..., ge=1)
    quantity: float = Field(..., gt=0, le=100_000)
    price: float = Field(..., ge=0, le=100_000_000)
    name: str = Field(default="", max_length=200)
    unit: str = Field(default="шт", max_length=20)
    item_type: Optional[ItemType] = None
    poster_account_id: Optional[int] = Field(default=None, ge=1)
    poster_account_name: Optional[str] = Field(default=None, max_length=100)
    storage_id: Optional[int] = Field(default=None, ge=1)
    storage_name: Optional[str] = Field(default=None, max_length=100)


class CreateSupplyRequest(BaseModel):
    supplier_id: int = Field(..., ge=1)
    supplier_name: str = Field(default="", max_length=200)
    items: List[SupplyItem] = Field(..., min_length=1)
    source: ExpenseSource = ExpenseSource.cash
    date: Optional[str] = Field(default=None, max_length=20)

    @field_validator("date")
    @classmethod
    def validate_date(cls, v):
        if v is not None:
            try:
                datetime.fromisoformat(v)
            except ValueError:
                raise ValueError("Invalid date format, use ISO format (YYYY-MM-DD)")
        return v


# ============================================================================
# Shift Closing Models
# ============================================================================

class ShiftClosingCalculateRequest(BaseModel):
    wolt: float = Field(default=0, ge=0, le=100_000_000)
    halyk: float = Field(default=0, ge=0, le=100_000_000)
    kaspi: float = Field(default=0, ge=0, le=100_000_000)
    kaspi_cafe: float = Field(default=0, ge=0, le=100_000_000)
    cash_bills: float = Field(default=0, ge=0, le=100_000_000)
    cash_coins: float = Field(default=0, ge=0, le=100_000_000)
    shift_start: float = Field(default=0, ge=0, le=100_000_000)
    expenses: float = Field(default=0, ge=0, le=100_000_000)
    deposits: float = Field(default=0, ge=0, le=100_000_000)
    cash_to_leave: float = Field(default=15000, ge=0, le=100_000_000)
    poster_trade: float = Field(default=0, ge=0)
    poster_bonus: float = Field(default=0, ge=0)
    poster_card: float = Field(default=0, ge=0)


# ============================================================================
# Alias Models
# ============================================================================

class CreateAliasRequest(BaseModel):
    alias_text: str = Field(..., min_length=1, max_length=200)
    poster_item_id: int = Field(..., ge=1)
    poster_item_name: str = Field(..., min_length=1, max_length=200)
    source: str = Field(default="user", max_length=50)
    notes: str = Field(default="", max_length=500)


class UpdateAliasRequest(BaseModel):
    alias_text: str = Field(default="", max_length=200)
    poster_item_id: int = Field(default=0, ge=0)
    poster_item_name: str = Field(default="", max_length=200)
    source: str = Field(default="user", max_length=50)
    notes: str = Field(default="", max_length=500)


# ============================================================================
# Salary Models
# ============================================================================

class CafeSalary(BaseModel):
    role: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=100)
    amount: int = Field(..., ge=0, le=1_000_000)


class CafeSalariesRequest(BaseModel):
    salaries: List[CafeSalary] = Field(..., min_length=1)


class CashierSalaryCalcRequest(BaseModel):
    cashier_count: int = Field(default=2, ge=2, le=3)
    assistant_start_time: Literal["10:00", "12:00", "14:00"] = "10:00"


# ============================================================================
# Reconciliation Model
# ============================================================================

class SaveReconciliationRequest(BaseModel):
    date: Optional[str] = Field(default=None, max_length=20)
    source: ExpenseSource
    opening_balance: Optional[float] = Field(default=None, ge=0, le=100_000_000)
    fact_balance: Optional[float] = Field(default=None, ge=0, le=100_000_000)
    closing_balance: Optional[float] = Field(default=None, ge=0, le=100_000_000)
    total_difference: Optional[float] = Field(default=None, ge=-100_000_000, le=100_000_000)
    notes: Optional[str] = Field(default=None, max_length=500)

    @field_validator("date")
    @classmethod
    def validate_date(cls, v):
        if v is not None:
            try:
                datetime.fromisoformat(v)
            except ValueError:
                raise ValueError("Invalid date format")
        return v


# ============================================================================
# Helper: validate request JSON with Pydantic model
# ============================================================================

def validate_json(model_class):
    """Decorator that validates request JSON against a Pydantic model.

    Injects validated model as first argument after self/cls.
    On validation error returns 400 with error details.
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            data = request.get_json(silent=True)
            if data is None:
                return jsonify({"error": "Invalid or missing JSON body"}), 400
            try:
                validated = model_class(**data)
            except Exception as e:
                logger.warning(f"Validation error on {request.path}: {e}")
                return jsonify({"error": f"Validation error: {e}"}), 400
            kwargs["validated"] = validated
            return f(*args, **kwargs)
        return wrapper
    return decorator
