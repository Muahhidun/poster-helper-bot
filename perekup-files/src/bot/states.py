from aiogram.fsm.state import State, StatesGroup


class ProjectCreation(StatesGroup):
    """Состояния создания проекта"""
    title = State()
    buy_date = State()
    buy_price = State()
    vin = State()
    confirm = State()


class ExpenseCreation(StatesGroup):
    """Состояния добавления расходов"""
    expenses_text = State()  # Ввод списка расходов
    project = State()        # Выбор проекта
    confirm = State()        # Подтверждение


class ProjectSale(StatesGroup):
    """Состояния продажи проекта"""
    project = State()
    sell_date = State()
    sell_price = State()
    confirm = State()


class CapitalDeposit(StatesGroup):
    """Состояния пополнения капитала"""
    amount = State()
    notes = State()
    confirm = State()


class CapitalWithdrawal(StatesGroup):
    """Состояния инкассации"""
    who = State()
    amount = State()
    notes = State()
    confirm = State()
