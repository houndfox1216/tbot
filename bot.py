import logging
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

# Must run before importing database, which reads DB_PATH at import time
load_dotenv()

from telegram import Update, ChatMember
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ChatMemberHandler,
    ContextTypes,
    filters,
)

import database as db

BOT_TOKEN = os.environ["BOT_TOKEN"]
DEFAULT_INACTIVE_DAYS = int(os.getenv("DEFAULT_INACTIVE_DAYS", "30"))
SCAN_INTERVAL_HOURS = float(os.getenv("SCAN_INTERVAL_HOURS", "24"))

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ── helpers ──────────────────────────────────────────────────────────────────

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
    return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)


async def kick_user(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int):
    """Ban then immediately unban = kick (user can rejoin via invite link)."""
    await context.bot.ban_chat_member(chat_id, user_id)
    await context.bot.unban_chat_member(chat_id, user_id)


# ── activity tracking ─────────────────────────────────────────────────────────

async def track_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Record any message as proof of activity."""
    user = update.effective_user
    chat = update.effective_chat
    if user and chat and chat.type in ("group", "supergroup"):
        await db.update_activity(
            chat.id, user.id,
            user.username,
            user.full_name,
        )


async def track_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Record when a user joins or is promoted — counts as activity."""
    result = update.chat_member or update.my_chat_member
    if not result:
        return
    new = result.new_chat_member
    if new.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR):
        await db.update_activity(
            result.chat.id,
            new.user.id,
            new.user.username,
            new.user.full_name,
        )


# ── commands ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text(
            "👋 我是非活躍踢除 Bot！\n"
            "請將我加入群組並賦予管理員權限（封禁成員），然後用以下指令管理：\n\n"
            "/setkickdays <天數> — 設定幾天沒活動就踢除\n"
            "/kickinactive — 立即掃描並踢除非活躍成員\n"
            "/status — 查看目前設定與活躍統計\n"
            "/help — 顯示說明"
        )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 指令說明：\n\n"
        "/setkickdays <天數>\n  設定幾天沒有任何發言即視為非活躍（預設 30 天）\n\n"
        "/listinactive\n  列出所有非活躍成員名單（不執行踢除，可先預覽）\n\n"
        "/kickinactive\n  立即掃描此群組，踢除所有非活躍成員\n\n"
        "/status\n  顯示目前門檻天數與資料庫中記錄的活躍人數\n\n"
        "⚠️ 以上指令僅限群組管理員使用。\n"
        "⚠️ Bot 必須擁有「封禁成員」管理權限才能執行踢除。"
    )


async def cmd_set_kick_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("此指令只能在群組中使用。")
        return
    if not await is_admin(update, context):
        await update.message.reply_text("❌ 只有管理員可以使用此指令。")
        return

    args = context.args
    if not args or not args[0].isdigit() or int(args[0]) < 1:
        await update.message.reply_text("用法：/setkickdays <天數>  （例如：/setkickdays 30）")
        return

    days = int(args[0])
    await db.set_inactive_days(chat.id, days)
    await update.message.reply_text(f"✅ 已設定：超過 {days} 天沒有發言的成員將被踢除。")


async def cmd_list_inactive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("此指令只能在群組中使用。")
        return
    if not await is_admin(update, context):
        await update.message.reply_text("❌ 只有管理員可以使用此指令。")
        return

    days = await db.get_inactive_days(chat.id, DEFAULT_INACTIVE_DAYS)
    inactive = await db.get_inactive_users(chat.id, days)

    if not inactive:
        await update.message.reply_text(f"✅ 沒有超過 {days} 天未活躍的成員。")
        return

    lines = [f"📋 非活躍成員列表（門檻：{days} 天）\n共 {len(inactive)} 人：\n"]
    for u in inactive:
        name = f"@{u['username']}" if u["username"] else u["full_name"]
        lines.append(f"• {name} — {u['days_inactive']} 天未發言")

    # Telegram message limit is 4096 chars; split if needed
    text = "\n".join(lines)
    if len(text) <= 4096:
        await update.message.reply_text(text)
    else:
        # Send header + as many entries as fit, then append overflow notice
        chunks, current = [], lines[0]
        for line in lines[1:]:
            if len(current) + len(line) + 1 > 4090:
                chunks.append(current)
                current = line
            else:
                current += "\n" + line
        chunks.append(current)
        for chunk in chunks:
            await update.message.reply_text(chunk)


async def cmd_kick_inactive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("此指令只能在群組中使用。")
        return
    if not await is_admin(update, context):
        await update.message.reply_text("❌ 只有管理員可以使用此指令。")
        return

    days = await db.get_inactive_days(chat.id, DEFAULT_INACTIVE_DAYS)
    inactive = await db.get_inactive_users(chat.id, days)

    if not inactive:
        await update.message.reply_text(f"✅ 沒有超過 {days} 天未活躍的成員。")
        return

    msg = await update.message.reply_text(f"🔍 找到 {len(inactive)} 位非活躍成員，開始踢除...")

    kicked, skipped = 0, 0
    for user in inactive:
        try:
            member = await context.bot.get_chat_member(chat.id, user["user_id"])
            # 不踢管理員或 Bot 自身
            if member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER,
                                  ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
                skipped += 1
                continue
            await kick_user(context, chat.id, user["user_id"])
            await db.remove_user(chat.id, user["user_id"])
            kicked += 1
            name = user["username"] and f"@{user['username']}" or user["full_name"]
            logger.info("Kicked %s (user_id=%s) from chat %s", name, user["user_id"], chat.id)
        except Exception as e:
            logger.warning("Could not kick user %s: %s", user["user_id"], e)
            skipped += 1

    await msg.edit_text(
        f"✅ 踢除完成！\n"
        f"  踢除：{kicked} 人\n"
        f"  略過（管理員/已離開/錯誤）：{skipped} 人\n"
        f"  門檻：{days} 天未活躍"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("此指令只能在群組中使用。")
        return

    days = await db.get_inactive_days(chat.id, DEFAULT_INACTIVE_DAYS)
    inactive = await db.get_inactive_users(chat.id, days)

    await update.message.reply_text(
        f"📊 群組狀態\n"
        f"  踢除門檻：{days} 天未活躍\n"
        f"  目前非活躍人數：{len(inactive)} 人\n"
        f"  自動掃描間隔：每 {SCAN_INTERVAL_HOURS:.0f} 小時"
    )


# ── scheduled job ─────────────────────────────────────────────────────────────

async def scheduled_kick(context: ContextTypes.DEFAULT_TYPE):
    """Auto-kick inactive users in all known chats."""
    chat_ids = await db.get_all_chat_ids()
    for chat_id in chat_ids:
        try:
            days = await db.get_inactive_days(chat_id, DEFAULT_INACTIVE_DAYS)
            inactive = await db.get_inactive_users(chat_id, days)
            for user in inactive:
                try:
                    member = await context.bot.get_chat_member(chat_id, user["user_id"])
                    if member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER,
                                         ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
                        continue
                    await kick_user(context, chat_id, user["user_id"])
                    await db.remove_user(chat_id, user["user_id"])
                    logger.info("[Auto] Kicked user %s from chat %s", user["user_id"], chat_id)
                except Exception as e:
                    logger.warning("[Auto] Skip user %s in chat %s: %s", user["user_id"], chat_id, e)
        except Exception as e:
            logger.warning("[Auto] Error processing chat %s: %s", chat_id, e)


# ── main ──────────────────────────────────────────────────────────────────────

async def post_init(application: Application):
    await db.init_db()
    logger.info("Database initialised.")


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("setkickdays", cmd_set_kick_days))
    app.add_handler(CommandHandler("kickinactive", cmd_kick_inactive))
    app.add_handler(CommandHandler("listinactive", cmd_list_inactive))
    app.add_handler(CommandHandler("status", cmd_status))

    # Track all messages in groups
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & ~filters.COMMAND, track_message))
    # Also count commands as activity
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.COMMAND, track_message))

    # Track join events
    app.add_handler(ChatMemberHandler(track_member_update, ChatMemberHandler.CHAT_MEMBER))

    # Scheduled auto-kick
    interval_seconds = int(SCAN_INTERVAL_HOURS * 3600)
    app.job_queue.run_repeating(scheduled_kick, interval=interval_seconds, first=60)
    logger.info("Scheduled auto-kick every %s hours.", SCAN_INTERVAL_HOURS)

    logger.info("Bot started. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
