"""Orchestration for persisted business reports and personal Telegram delivery."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from account_analytics import KZ_TZ, last_completed_date
from business_analytics import (
    collect_business_report,
    format_telegram_report,
    generate_ai_commentary,
)
from database import get_database


logger = logging.getLogger(__name__)


async def generate_business_report_for_user(
    telegram_user_id: int,
    bot: Optional[Any] = None,
    send_telegram: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    """Generate, persist, and optionally send one complete daily report.

    A report is never sent twice for the same completed day unless ``force`` is
    explicitly requested. Failed/partial Poster fetches are returned but not
    presented as valid analytics.
    """
    db = get_database()
    target_date = last_completed_date(datetime.now(KZ_TZ)).isoformat()
    existing = db.get_latest_business_analytics_report(telegram_user_id)
    if (
        send_telegram
        and not force
        and existing
        and existing.get("report_date") == target_date
        and existing.get("telegram_sent_at")
    ):
        return {"success": True, "skipped": True, "report": existing}

    accounts = db.get_accounts(telegram_user_id)
    if not accounts:
        return {"success": False, "error": "Нет подключённых аккаунтов Poster"}

    report = await collect_business_report(accounts)
    if not report.get("success"):
        return {"success": False, "error": report.get("error"), "report": report}

    ai_commentary = await generate_ai_commentary(report)
    if not db.save_business_analytics_report(telegram_user_id, report, ai_commentary):
        return {"success": False, "error": "Не удалось сохранить отчёт", "report": report}

    sent = False
    if send_telegram:
        if bot is None:
            return {"success": False, "error": "Telegram bot не передан", "report": report}
        text = format_telegram_report(report, ai_commentary)
        try:
            await bot.send_message(chat_id=telegram_user_id, text=text, parse_mode="HTML")
            db.mark_business_report_sent(telegram_user_id, report["report_date"])
            sent = True
        except Exception as exc:
            logger.error("Failed to send business report to %s: %s", telegram_user_id, exc)
            return {
                "success": False,
                "error": f"Отчёт сохранён, но Telegram не отправлен: {exc}",
                "report": report,
            }

    return {
        "success": True,
        "report": report,
        "ai_commentary": ai_commentary,
        "telegram_sent": sent,
    }
