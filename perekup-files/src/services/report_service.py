from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from decimal import Decimal
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple
from src.db.models import Project, Expense, Income, CapitalOperation, ProjectStatus, CapitalOperationType
from src.config import INITIAL_CAPITAL, get_partner_name


class ReportService:
    """Сервис для генерации отчетов"""

    @staticmethod
    async def get_capital_balance(session: AsyncSession) -> Dict:
        """
        Рассчитать текущий баланс капитала

        Формула:
        Баланс = Начальный капитал + Пополнения - Инкассации - Вложено в активные проекты - Общие расходы
        """
        # Получаем начальный капитал из БД или используем константу
        initial_query = select(CapitalOperation).where(
            CapitalOperation.type == CapitalOperationType.initial
        )
        initial_result = await session.execute(initial_query)
        initial_op = initial_result.scalar_one_or_none()

        initial_capital = Decimal(str(initial_op.amount)) if initial_op else Decimal(str(INITIAL_CAPITAL))

        # Пополнения капитала
        deposits_query = select(func.sum(CapitalOperation.amount)).where(
            CapitalOperation.type == CapitalOperationType.deposit
        )
        deposits_result = await session.execute(deposits_query)
        deposits = deposits_result.scalar() or Decimal("0")

        # Инкассации (выводы)
        withdrawals_query = select(func.sum(CapitalOperation.amount)).where(
            CapitalOperation.type == CapitalOperationType.withdrawal
        )
        withdrawals_result = await session.execute(withdrawals_query)
        withdrawals = withdrawals_result.scalar() or Decimal("0")

        # Получаем активные проекты
        active_projects_query = select(Project).where(Project.status == ProjectStatus.active)
        active_projects_result = await session.execute(active_projects_query)
        active_projects = active_projects_result.scalars().all()

        # Вложено в активные проекты
        total_invested_in_projects = Decimal("0")
        projects_breakdown = []

        for project in active_projects:
            # Стоимость покупки
            invested = project.buy_price

            # Расходы на проект
            expenses_query = select(func.sum(Expense.amount)).where(
                Expense.project_id == project.id
            )
            expenses_result = await session.execute(expenses_query)
            project_expenses = expenses_result.scalar() or Decimal("0")

            invested += project_expenses
            total_invested_in_projects += invested

            projects_breakdown.append({
                "id": project.id,
                "title": project.title,
                "buy_date": project.buy_date,
                "invested": invested,
            })

        # Общие расходы (не привязанные к проектам)
        common_expenses_query = select(func.sum(Expense.amount)).where(
            Expense.project_id.is_(None)
        )
        common_expenses_result = await session.execute(common_expenses_query)
        common_expenses = common_expenses_result.scalar() or Decimal("0")

        # Итоговый баланс
        balance = initial_capital + deposits - withdrawals - total_invested_in_projects - common_expenses

        return {
            "balance": balance,
            "initial_capital": initial_capital,
            "deposits": deposits,
            "withdrawals": withdrawals,
            "invested_in_projects": total_invested_in_projects,
            "common_expenses": common_expenses,
            "active_projects": projects_breakdown,
            "active_projects_count": len(active_projects),
        }

    @staticmethod
    async def get_sold_projects_stats(session: AsyncSession) -> Dict:
        """Статистика по проданным проектам"""
        sold_projects_query = select(Project).where(Project.status == ProjectStatus.sold)
        sold_projects_result = await session.execute(sold_projects_query)
        sold_projects = sold_projects_result.scalars().all()

        total_profit = Decimal("0")
        count = 0

        for project in sold_projects:
            if project.sell_price:
                # Расходы на проект
                expenses_query = select(func.sum(Expense.amount)).where(
                    Expense.project_id == project.id
                )
                expenses_result = await session.execute(expenses_query)
                project_expenses = expenses_result.scalar() or Decimal("0")

                # Прибыль = продажа - (покупка + расходы)
                profit = project.sell_price - (project.buy_price + project_expenses)
                total_profit += profit
                count += 1

        return {
            "sold_count": count,
            "total_profit": total_profit,
        }

    @staticmethod
    async def get_withdrawals_by_partner(session: AsyncSession) -> Dict:
        """Инкассации по партнерам"""
        # Инкассации автора
        author_query = select(func.sum(CapitalOperation.amount)).where(
            CapitalOperation.type == CapitalOperationType.withdrawal,
            CapitalOperation.who == "author"
        )
        author_result = await session.execute(author_query)
        author_withdrawals = author_result.scalar() or Decimal("0")

        # Инкассации Серика
        serik_query = select(func.sum(CapitalOperation.amount)).where(
            CapitalOperation.type == CapitalOperationType.withdrawal,
            CapitalOperation.who == "serik"
        )
        serik_result = await session.execute(serik_query)
        serik_withdrawals = serik_result.scalar() or Decimal("0")

        return {
            "author": author_withdrawals,
            "serik": serik_withdrawals,
            "total": author_withdrawals + serik_withdrawals,
        }

    @staticmethod
    async def get_project_report(session: AsyncSession, project_id: int) -> Optional[Dict]:
        """Детальный отчет по проекту"""
        query = select(Project).where(Project.id == project_id)
        result = await session.execute(query)
        project = result.scalar_one_or_none()

        if not project:
            return None

        # Получаем все расходы проекта
        expenses_query = select(Expense).where(Expense.project_id == project_id).order_by(Expense.date)
        expenses_result = await session.execute(expenses_query)
        expenses = expenses_result.scalars().all()

        total_expenses = sum(e.amount for e in expenses)
        total_invested = project.buy_price + total_expenses

        # Расчет прибыли (если продано)
        profit = None
        profit_per_partner = None
        duration_days = None

        if project.status == ProjectStatus.sold and project.sell_price:
            profit = project.sell_price - total_invested
            profit_per_partner = profit / 2

            if project.sell_date:
                duration_days = (project.sell_date - project.buy_date).days

        return {
            "project": project,
            "expenses": expenses,
            "total_expenses": total_expenses,
            "total_invested": total_invested,
            "profit": profit,
            "profit_per_partner": profit_per_partner,
            "duration_days": duration_days,
        }

    @staticmethod
    def format_daily_report(balance_data: Dict, sold_stats: Dict, withdrawals: Dict) -> str:
        """Форматировать ежедневный отчет"""
        from src.utils.formatters import format_money

        today = datetime.now().strftime("%d.%m.%Y")

        report = f"📊 Ежедневный отчет — {today}\n\n"
        report += f"💰 БАЛАНС КАПИТАЛА: {format_money(balance_data['balance'])}\n\n"

        # Активные проекты
        if balance_data['active_projects']:
            report += f"🚗 Активные проекты ({balance_data['active_projects_count']}):\n"
            for proj in balance_data['active_projects']:
                buy_date_str = proj['buy_date'].strftime("%d.%m")
                report += f"├ {proj['title']}\n"
                report += f"│ └ Вложено: {format_money(proj['invested'])} (куплено {buy_date_str})\n"
            report += "\n"

        # Итоговая статистика
        report += f"💼 Итого в проектах: {format_money(balance_data['invested_in_projects'])}\n"
        report += f"📦 Общие расходы: {format_money(balance_data['common_expenses'])}\n"

        if withdrawals['total'] > 0:
            report += f"\n💸 Инкассировано всего: {format_money(withdrawals['total'])}\n"
            report += f"   └ Жандос: {format_money(withdrawals['author'])}\n"
            report += f"   └ Серик: {format_money(withdrawals['serik'])}\n"

        # Статистика по проданным
        if sold_stats['sold_count'] > 0:
            report += f"\n📊 Проданных проектов: {sold_stats['sold_count']}\n"
            report += f"💵 Общая прибыль: {format_money(sold_stats['total_profit'])}\n"

        return report
