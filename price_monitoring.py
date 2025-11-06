"""Smart price monitoring system for ingredient purchases"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from database import get_database, DB_TYPE

logger = logging.getLogger(__name__)


class PriceMonitor:
    """Analyzes ingredient price trends and performs ABC analysis"""

    def __init__(self, telegram_user_id: int):
        """
        Initialize price monitor for a specific user

        Args:
            telegram_user_id: Telegram user ID
        """
        self.telegram_user_id = telegram_user_id
        self.db = get_database()

    async def calculate_abc_analysis(self, period_months: int = 3) -> Tuple[Dict[str, List[int]], List[Dict]]:
        """
        Calculate ABC analysis of ingredients based on spending

        Args:
            period_months: Number of months to analyze (default: 3)

        Returns:
            Tuple of (abc_groups, detailed_results)
            - abc_groups: {'A': [ingredient_ids], 'B': [ingredient_ids], 'C': [ingredient_ids]}
            - detailed_results: List of dicts with spending details per ingredient
        """
        date_from = (datetime.now() - timedelta(days=period_months * 30)).strftime('%Y-%m-%d')

        logger.info(f"📊 Calculating ABC analysis for user {self.telegram_user_id} from {date_from}")

        # Get all price history for the period
        conn = self.db._get_connection()

        if DB_TYPE == "sqlite":
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    ingredient_id,
                    ingredient_name,
                    SUM(price * quantity) as total_spent,
                    COUNT(*) as purchase_count,
                    AVG(price) as avg_price
                FROM ingredient_price_history
                WHERE telegram_user_id = ? AND date >= ?
                GROUP BY ingredient_id, ingredient_name
                ORDER BY total_spent DESC
            """, (self.telegram_user_id, date_from))
            rows = cursor.fetchall()
        else:
            from psycopg2.extras import RealDictCursor
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT
                    ingredient_id,
                    ingredient_name,
                    SUM(price * quantity) as total_spent,
                    COUNT(*) as purchase_count,
                    AVG(price) as avg_price
                FROM ingredient_price_history
                WHERE telegram_user_id = %s AND date >= %s
                GROUP BY ingredient_id, ingredient_name
                ORDER BY total_spent DESC
            """, (self.telegram_user_id, date_from))
            rows = cursor.fetchall()

        conn.close()

        if not rows:
            logger.warning("⚠️ No price history data found for ABC analysis")
            return {'A': [], 'B': [], 'C': []}, []

        # Convert rows to dicts
        if DB_TYPE == "sqlite":
            rows_data = [
                {
                    'ingredient_id': row[0],
                    'ingredient_name': row[1],
                    'total_spent': float(row[2]) if row[2] else 0,
                    'purchase_count': row[3],
                    'avg_price': float(row[4]) if row[4] else 0
                }
                for row in rows
            ]
        else:
            rows_data = [dict(row) for row in rows]

        # Calculate total spending
        total_sum = sum(row['total_spent'] for row in rows_data)

        if total_sum == 0:
            logger.warning("⚠️ Total spending is 0, cannot perform ABC analysis")
            return {'A': [], 'B': [], 'C': []}, []

        # Calculate cumulative percentages
        results = []
        cumulative_sum = 0

        for row in rows_data:
            cumulative_sum += row['total_spent']
            cumulative_percent = (cumulative_sum / total_sum) * 100
            spend_percent = (row['total_spent'] / total_sum) * 100

            results.append({
                'ingredient_id': row['ingredient_id'],
                'name': row['ingredient_name'],
                'spent': row['total_spent'],
                'spent_percent': spend_percent,
                'cumulative_percent': cumulative_percent,
                'purchase_count': row['purchase_count'],
                'avg_price': row['avg_price']
            })

        # Categorize into A, B, C groups
        abc_groups = {'A': [], 'B': [], 'C': []}

        for item in results:
            if item['cumulative_percent'] <= 80:
                abc_groups['A'].append(item['ingredient_id'])
            elif item['cumulative_percent'] <= 95:
                abc_groups['B'].append(item['ingredient_id'])
            else:
                abc_groups['C'].append(item['ingredient_id'])

        logger.info(f"✅ ABC analysis complete: A={len(abc_groups['A'])}, B={len(abc_groups['B'])}, C={len(abc_groups['C'])}")

        return abc_groups, results

    async def analyze_price_trends(
        self,
        ingredient_ids: List[int],
        months: int = 6,
        threshold: float = 30.0
    ) -> List[Dict]:
        """
        Analyze price trends for given ingredients

        Args:
            ingredient_ids: List of ingredient IDs to analyze (usually category A from ABC)
            months: Number of months to analyze (default: 6)
            threshold: Percentage change threshold to trigger alert (default: 30%)

        Returns:
            List of ingredients with significant price changes
        """
        if not ingredient_ids:
            logger.warning("⚠️ No ingredients provided for price trend analysis")
            return []

        date_from = (datetime.now() - timedelta(days=months * 30)).strftime('%Y-%m-%d')
        alerts = []

        logger.info(f"📈 Analyzing price trends for {len(ingredient_ids)} ingredients from {date_from}")

        for ingredient_id in ingredient_ids:
            # Get all prices for this ingredient in the period
            conn = self.db._get_connection()

            if DB_TYPE == "sqlite":
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT
                        date,
                        price,
                        supplier_name,
                        quantity,
                        unit,
                        strftime('%Y-%m', date) as month
                    FROM ingredient_price_history
                    WHERE telegram_user_id = ? AND ingredient_id = ? AND date >= ?
                    ORDER BY date
                """, (self.telegram_user_id, ingredient_id, date_from))
                prices = cursor.fetchall()
            else:
                from psycopg2.extras import RealDictCursor
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                cursor.execute("""
                    SELECT
                        date,
                        price,
                        supplier_name,
                        quantity,
                        unit,
                        TO_CHAR(date, 'YYYY-MM') as month
                    FROM ingredient_price_history
                    WHERE telegram_user_id = %s AND ingredient_id = %s AND date >= %s
                    ORDER BY date
                """, (self.telegram_user_id, ingredient_id, date_from))
                prices = cursor.fetchall()

            conn.close()

            if len(prices) < 2:
                continue  # Not enough data

            # Convert to dicts
            if DB_TYPE == "sqlite":
                prices_data = [
                    {
                        'date': row[0],
                        'price': float(row[1]),
                        'supplier_name': row[2],
                        'quantity': float(row[3]) if row[3] else 0,
                        'unit': row[4],
                        'month': row[5],
                        'ingredient_name': None  # Will be set below
                    }
                    for row in prices
                ]
            else:
                prices_data = [dict(row) for row in prices]

            if not prices_data:
                continue

            # Get ingredient name from first record
            ingredient_name = prices_data[0].get('ingredient_name')
            if not ingredient_name:
                # Fetch from a separate query
                conn = self.db._get_connection()
                if DB_TYPE == "sqlite":
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT ingredient_name
                        FROM ingredient_price_history
                        WHERE ingredient_id = ? AND telegram_user_id = ?
                        LIMIT 1
                    """, (ingredient_id, self.telegram_user_id))
                    result = cursor.fetchone()
                    ingredient_name = result[0] if result else f"Ingredient #{ingredient_id}"
                else:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT ingredient_name
                        FROM ingredient_price_history
                        WHERE ingredient_id = %s AND telegram_user_id = %s
                        LIMIT 1
                    """, (ingredient_id, self.telegram_user_id))
                    result = cursor.fetchone()
                    ingredient_name = result[0] if result else f"Ingredient #{ingredient_id}"
                conn.close()

            # Group by month and calculate average
            months_data = {}
            for price_record in prices_data:
                month = price_record['month']
                if month not in months_data:
                    months_data[month] = []
                months_data[month].append(price_record['price'])

            # Get first and last month averages
            sorted_months = sorted(months_data.keys())
            if len(sorted_months) < 2:
                continue

            first_month = sorted_months[0]
            last_month = sorted_months[-1]

            avg_first_month = sum(months_data[first_month]) / len(months_data[first_month])
            avg_last_month = sum(months_data[last_month]) / len(months_data[last_month])

            # Calculate price change percentage
            price_change_percent = ((avg_last_month - avg_first_month) / avg_first_month) * 100

            # Check if exceeds threshold
            if abs(price_change_percent) >= threshold:
                # Get unique suppliers
                suppliers = list(set(p['supplier_name'] for p in prices_data if p['supplier_name']))
                unit = prices_data[0]['unit']

                alerts.append({
                    'ingredient_id': ingredient_id,
                    'ingredient_name': ingredient_name,
                    'price_old': round(avg_first_month, 2),
                    'price_new': round(avg_last_month, 2),
                    'change_percent': round(price_change_percent, 1),
                    'change_amount': round(avg_last_month - avg_first_month, 2),
                    'period': f'{months} месяцев',
                    'first_month': first_month,
                    'last_month': last_month,
                    'suppliers': suppliers,
                    'unit': unit,
                    'data_points': len(prices_data)
                })

                logger.info(
                    f"⚠️ Price alert: {ingredient_name} changed by {price_change_percent:.1f}% "
                    f"({avg_first_month:.2f}₸ → {avg_last_month:.2f}₸)"
                )

        logger.info(f"✅ Price trend analysis complete: {len(alerts)} alerts generated")

        return alerts

    async def get_affected_products(self, ingredient_id: int, poster_client) -> List[Dict]:
        """
        Get list of products/dishes that use this ingredient (stub for now)

        Args:
            ingredient_id: Poster ingredient ID
            poster_client: PosterClient instance

        Returns:
            List of affected products with cost impact
        """
        # TODO: Implement when Poster API provides tech cards (menu.getTechCards)
        # For now, return empty list
        logger.warning(f"⚠️ get_affected_products not implemented yet for ingredient {ingredient_id}")
        return []


def format_price_alert_message(
    alerts: List[Dict],
    abc_results: List[Dict],
    telegram_user_id: int
) -> str:
    """
    Format price alerts into a Telegram message

    Args:
        alerts: List of price change alerts
        abc_results: ABC analysis results
        telegram_user_id: User ID

    Returns:
        Formatted message text
    """
    if not alerts:
        return "✅ Проверка цен завершена. Значительных изменений не обнаружено."

    # Count ABC categories
    abc_a_count = len([r for r in abc_results if r['cumulative_percent'] <= 80])

    message = "⚠️ <b>ВАЖНО: Значительные изменения цен</b>\n\n"
    message += f"📊 <b>ABC-АНАЛИЗ:</b> Проверено {abc_a_count} ключевых ингредиентов (категория A)\n\n"
    message += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    for i, alert in enumerate(alerts, 1):
        # Emoji based on change direction
        trend_emoji = "📈" if alert['change_percent'] > 0 else "📉"
        change_emoji = "⚠️" if abs(alert['change_percent']) >= 30 else "ℹ️"

        message += f"<b>{i}️⃣ {alert['ingredient_name']}</b>\n\n"

        message += f"{trend_emoji} <b>Изменение цены:</b>\n"
        message += f"{alert['first_month']}: {alert['price_old']:,.0f}₸/{alert['unit']}\n"
        message += f"{alert['last_month']}: {alert['price_new']:,.0f}₸/{alert['unit']}\n"

        if alert['change_percent'] > 0:
            message += f"Рост: +{alert['change_amount']:,.0f}₸ (+{alert['change_percent']:.1f}%) {change_emoji}\n\n"
        else:
            message += f"Снижение: {alert['change_amount']:,.0f}₸ ({alert['change_percent']:.1f}%) ✅\n\n"

        if alert['suppliers']:
            message += f"📦 <b>Поставщики:</b> {', '.join(alert['suppliers'])}\n\n"

        message += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    # Summary
    message += f"📊 <b>ИТОГО:</b>\n"
    message += f"✅ Проверено: {abc_a_count} ингредиентов (категория A)\n"
    message += f"⚠️ Критичных изменений: {len(alerts)} (≥30%)\n\n"

    message += f"🔔 Следующая проверка: {(datetime.now() + timedelta(days=7)).strftime('%d.%m.%Y')}\n"

    return message


async def perform_weekly_price_check(telegram_user_id: int, bot) -> None:
    """
    Perform weekly price check and send notification to user

    Args:
        telegram_user_id: User to check prices for
        bot: Telegram bot instance to send messages
    """
    try:
        logger.info(f"🔄 Starting weekly price check for user {telegram_user_id}...")

        monitor = PriceMonitor(telegram_user_id)

        # Step 1: ABC analysis
        abc_groups, abc_results = await monitor.calculate_abc_analysis(period_months=3)
        category_a_ids = abc_groups['A']

        if not category_a_ids:
            logger.info(f"ℹ️ No category A ingredients found for user {telegram_user_id}")
            return

        logger.info(f"📊 Category A: {len(category_a_ids)} ingredients")

        # Step 2: Analyze price trends (6 months, 30% threshold)
        alerts = await monitor.analyze_price_trends(
            ingredient_ids=category_a_ids,
            months=6,
            threshold=30.0
        )

        if not alerts:
            logger.info(f"✅ No significant price changes detected for user {telegram_user_id}")
            # Optionally send "all clear" message
            # await bot.send_message(
            #     chat_id=telegram_user_id,
            #     text="✅ Еженедельная проверка цен завершена. Значительных изменений не обнаружено.",
            #     parse_mode='HTML'
            # )
            return

        # Step 3: Format and send notification
        message = format_price_alert_message(alerts, abc_results, telegram_user_id)

        await bot.send_message(
            chat_id=telegram_user_id,
            text=message,
            parse_mode='HTML'
        )

        logger.info(f"📤 Price alert sent to user {telegram_user_id}: {len(alerts)} alerts")

    except Exception as e:
        logger.error(f"❌ Weekly price check failed for user {telegram_user_id}: {e}", exc_info=True)
