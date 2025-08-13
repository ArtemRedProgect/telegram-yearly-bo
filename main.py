import os
import logging
from pathlib import Path
from datetime import time
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    PicklePersistence,
)

# -------------------- НАСТРОЙКИ --------------------
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_TZ = "Europe/Minsk"
DEFAULT_TIME = time(10, 0)
PERSIST_FILE = BASE_DIR / "bot_data.pkl"
MESSAGES_FILE = BASE_DIR / "messages.txt"

# Логи
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger("yearly-bot")


# -------------------- ЗАГРУЗКА СООБЩЕНИЙ --------------------
def load_messages(path: Path) -> list[str]:
    """Читаем файл messages.txt, по одной фразе в строке."""
    if not path.exists():
        raise FileNotFoundError(f"Не найден {path}. Создай его с одной фразой на строку.")
    msgs = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not msgs:
        raise ValueError("Файл messages.txt пуст.")
    return msgs

MESSAGES = load_messages(MESSAGES_FILE)


# -------------------- ВСПОМОГАТЕЛЬНОЕ --------------------
def get_user_tz(data: dict) -> ZoneInfo:
    """Возвращает часовой пояс пользователя или дефолтный."""
    tz_name = data.get("tz", DEFAULT_TZ)
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)

def get_user_time(data: dict) -> time:
    """Возвращает время отправки с учётом TZ."""
    return time(
        data.get("hour", DEFAULT_TIME.hour),
        data.get("minute", DEFAULT_TIME.minute),
        tzinfo=get_user_tz(data)
    )

def job_name(chat_id: int) -> str:
    return f"daily-{chat_id}"

def cancel_existing_job(job_queue, chat_id: int):
    """Удаляет все задачи с таким chat_id."""
    if job_queue is None:
        logger.warning("JobQueue = None — планировщик не активен.")
        return
    for j in job_queue.get_jobs_by_name(job_name(chat_id)):
        j.schedule_removal()

def schedule_daily_job(job_queue, chat_id: int, data: dict):
    """Ставит ежедневную задачу на отправку сообщения."""
    if job_queue is None:
        logger.warning("JobQueue = None — не могу поставить задачу.")
        return
    cancel_existing_job(job_queue, chat_id)
    t = get_user_time(data)
    job_queue.run_daily(
        send_message_job,
        time=t,
        chat_id=chat_id,
        name=job_name(chat_id),
        data={"chat_id": chat_id},
    )
    logger.info(f"Запланирована отправка для {chat_id} в {t}.")


# -------------------- ЗАДАЧА РАССЫЛКИ --------------------
async def send_message_job(context: ContextTypes.DEFAULT_TYPE):
    """Отправляет очередное сообщение пользователю."""
    chat_id = (context.job.data or {}).get("chat_id")
    if chat_id is None:
        return
    chat_store = context.application.chat_data.get(chat_id)
    if not chat_store:
        return
    idx = chat_store.get("index", 0)
    if idx >= len(MESSAGES):
        cancel_existing_job(context.application.job_queue, chat_id)
        await context.bot.send_message(chat_id, "Это было последнее сообщение. Год завершён. Чтобы начать заново — /reset.")
        return
    await context.bot.send_message(chat_id, MESSAGES[idx])
    chat_store["index"] = idx + 1


# -------------------- КОМАНДЫ --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    context.chat_data.setdefault("tz", DEFAULT_TZ)
    context.chat_data.setdefault("hour", DEFAULT_TIME.hour)
    context.chat_data.setdefault("minute", DEFAULT_TIME.minute)
    context.chat_data.setdefault("index", 0)
    schedule_daily_job(context.application.job_queue, chat_id, context.chat_data)
    t = get_user_time(context.chat_data)
    await update.message.reply_text(
        f"Привет! Буду слать тебе сообщения каждый день в {t.hour:02d}:{t.minute:02d} ({context.chat_data['tz']})."
    )

async def settime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args or len(context.args[0].split(":")) != 2:
        await update.message.reply_text("Формат: /settime HH:MM")
        return
    try:
        hh, mm = map(int, context.args[0].split(":"))
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError
    except ValueError:
        await update.message.reply_text("Неверный формат. Пример: /settime 09:30")
        return
    context.chat_data["hour"] = hh
    context.chat_data["minute"] = mm
    schedule_daily_job(context.application.job_queue, chat_id, context.chat_data)
    await update.message.reply_text(f"Время изменено на {hh:02d}:{mm:02d}.")

async def settz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Формат: /settz Europe/Minsk")
        return
    try:
        ZoneInfo(context.args[0])
    except Exception:
        await update.message.reply_text("Неверный часовой пояс.")
        return
    context.chat_data["tz"] = context.args[0]
    schedule_daily_job(context.application.job_queue, chat_id, context.chat_data)
    await update.message.reply_text(f"Часовой пояс изменён: {context.args[0]}.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tz = context.chat_data.get("tz", DEFAULT_TZ)
    hh = context.chat_data.get("hour", DEFAULT_TIME.hour)
    mm = context.chat_data.get("minute", DEFAULT_TIME.minute)
    idx = context.chat_data.get("index", 0)
    left = max(0, len(MESSAGES) - idx)
    await update.message.reply_text(
        f"- Время: {hh:02d}:{mm:02d}\n- Часовой пояс: {tz}\n- Следующее сообщение №: {idx+1}\n- Осталось: {left}"
    )

async def pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cancel_existing_job(context.application.job_queue, update.effective_chat.id)
    await update.message.reply_text("Пауза. Для возобновления — /resume.")

async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    schedule_daily_job(context.application.job_queue, update.effective_chat.id, context.chat_data)
    await update.message.reply_text("Возобновил отправку.")

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data["index"] = 0
    await update.message.reply_text("Сбросил прогресс. Начнём с первого сообщения.")


# -------------------- ЗАПУСК --------------------
def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Переменная TELEGRAM_BOT_TOKEN не установлена.")
    persistence = PicklePersistence(filepath=str(PERSIST_FILE))
    app = Application.builder().token(token).persistence(persistence).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("settime", settime))
    app.add_handler(CommandHandler("settz", settz))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("pause", pause))
    app.add_handler(CommandHandler("resume", resume))
    app.add_handler(CommandHandler("reset", reset))
    async def on_startup(app_: Application):
        for chat_id, chat_store in app_.chat_data.items():
            if isinstance(chat_store, dict):
                schedule_daily_job(app_.job_queue, int(chat_id), chat_store)
    app.post_init = on_startup
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
