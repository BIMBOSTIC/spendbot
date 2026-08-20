from datetime import date
from telegram import Update
from telegram.ext import ContextTypes
from config import ADMIN_TELEGRAM_ID
from db.queries import get_admin_stats


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_TELEGRAM_ID:
        return  # silent — don't reveal the command exists

    today = date.today().isoformat()
    s     = get_admin_stats(today)

    top = "\n".join(
        f"  {i+1}. {name} ({count}x)"
        for i, (name, count) in enumerate(s["top_items"])
    ) or "  (none yet)"

    per_user_lines = "\n".join(
        f"  …{str(u['telegram_id'])[-4:]} ({u['currency']})  {u['entries']} entr{'y' if u['entries'] == 1 else 'ies'}"
        for u in s["per_user"]
    ) or "  (none yet)"

    await update.message.reply_text(
        f"Admin snapshot — {today}\n"
        f"{'─' * 30}\n"
        f"Total registered users: {s['total_users']}\n"
        f"Active today:           {s['active_users']}\n"
        f"Entries logged today:   {s['total_messages']}\n"
        f"Parse failures today:   {s['parse_failures']}\n\n"
        f"Per-user today:\n{per_user_lines}\n\n"
        f"Top items today:\n{top}"
    )
