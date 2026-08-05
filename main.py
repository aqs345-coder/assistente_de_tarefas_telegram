import logging
import re
from datetime import date, datetime, time as dtime, timedelta

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config
import db
import web

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

TEXT, DURATION, TIME, CONFIRM = range(4)
CANCEL = 0

START_TEXT = """Ola! Eu sou o assistente de tarefas.

Comandos disponiveis:
/lembrete - criar um novo lembrete (tarefa, duracao e horario)
/meus_lembretes - listar seus lembretes ativos
/cancelar_lembrete - cancelar um lembrete existente
/help - ver esta mensagem

Durante a criacao de um lembrete, use /cancelar a qualquer momento para abortar."""

DURATION_UNITS = {
    "dia": 1,
    "dias": 1,
    "semana": 7,
    "semanas": 7,
    "mes": 30,
    "meses": 30,
    "mês": 30,
    "ano": 365,
    "anos": 365,
}

TIME_PATTERN = re.compile(r"([01]?\d|2[0-3])\s*[:hH.]\s*([0-5]\d)\s*")


def parse_duration(text):
    match = re.fullmatch(r"\s*(\d+)\s*([a-zà-ú]*)\s*", text.strip().lower())
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2)
    if value <= 0 or value > 3650:
        return None
    if unit == "":
        return value
    days_per_unit = DURATION_UNITS.get(unit)
    if days_per_unit is None:
        return None
    return value * days_per_unit


def parse_time(text):
    match = TIME_PATTERN.fullmatch(text.strip().lower())
    if not match:
        return None
    return dtime(int(match.group(1)), int(match.group(2)))


def schedule_reminder(job_queue, reminder_id, tzinfo):
    row = db.get_reminder(reminder_id)
    if row is None:
        return
    hour, minute = map(int, row["time"].split(":"))
    job_queue.run_daily(
        send_reminder,
        time=dtime(hour, minute, tzinfo=tzinfo),
        days=tuple(range(7)),
        name=f"reminder_{reminder_id}",
        data=reminder_id,
    )


async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    reminder_id = job.data
    row = db.get_reminder(reminder_id)
    if row is None or not row["active"]:
        job.schedule_removal()
        return
    await context.bot.send_message(
        chat_id=row["chat_id"], text=f"Lembrete: {row['text']}"
    )
    today = datetime.now(config.TIMEZONE).date()
    if today >= date.fromisoformat(row["end_date"]):
        db.deactivate_reminder(reminder_id)
        job.schedule_removal()


async def post_init(application: Application):
    await application.bot.delete_webhook(drop_pending_updates=True)
    reschedule_all(application.job_queue, config.TIMEZONE)


def reschedule_all(job_queue, tzinfo):
    today = date.today()
    for row in db.list_all_active_reminders():
        if date.fromisoformat(row["end_date"]) < today:
            db.deactivate_reminder(row["id"])
            continue
        schedule_reminder(job_queue, row["id"], tzinfo)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(START_TEXT)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(START_TEXT)


async def meus_lembretes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.list_active_reminders(update.effective_user.id)
    if not rows:
        await update.effective_message.reply_text("Voce nao tem lembretes ativos.")
        return
    lines = [
        f"{r['id']}. {r['text']} - todos os dias as {r['time']} "
        f"(ate {date.fromisoformat(r['end_date']).strftime('%d/%m/%Y')})"
        for r in rows
    ]
    await update.effective_message.reply_text(
        "Seus lembretes ativos:\n\n" + "\n".join(lines)
    )


async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("Comando cancelado.")
    context.user_data.clear()
    return ConversationHandler.END


async def lembrete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Qual tarefa voce quer que eu lembre? (ex: Levar o cachorro para passear)"
    )
    return TEXT


async def receber_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.effective_message.text.strip()
    if not text:
        await update.effective_message.reply_text(
            "A tarefa nao pode ser vazia. Envie a tarefa:"
        )
        return TEXT
    if len(text) > 200:
        await update.effective_message.reply_text(
            "Tarefa muito longa (maximo de 200 caracteres). Envie uma versao mais curta:"
        )
        return TEXT
    context.user_data["reminder_text"] = text
    await update.effective_message.reply_text(
        "Por quanto tempo o lembrete deve ficar ativo? "
        "(ex: 3 semanas, 15 dias, 1 mes, ou apenas um numero de dias)"
    )
    return DURATION


async def receber_duracao(update: Update, context: ContextTypes.DEFAULT_TYPE):
    days = parse_duration(update.effective_message.text)
    if days is None:
        await update.effective_message.reply_text(
            "Duracao invalida. Use formatos como '3 semanas', '10 dias', '1 mes' "
            "ou apenas um numero de dias."
        )
        return DURATION
    context.user_data["duration_days"] = days
    await update.effective_message.reply_text(
        "Em que horario devo lembrar todos os dias? (formato HH:MM, ex: 14:30)"
    )
    return TIME


async def receber_horario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    horario = parse_time(update.effective_message.text)
    if horario is None:
        await update.effective_message.reply_text(
            "Horario invalido. Use o formato HH:MM, ex: 07:00 ou 18:45."
        )
        return TIME
    context.user_data["reminder_time"] = horario
    text = context.user_data["reminder_text"]
    days = context.user_data["duration_days"]
    end = datetime.now(config.TIMEZONE).date() + timedelta(days=days)
    message = (
        "Confirma o lembrete?\n\n"
        f"Tarefa: {text}\n"
        "Frequencia: todos os dias\n"
        f"Horario: {horario.strftime('%H:%M')}\n"
        f"Duracao: {days} dia(s), ate {end.strftime('%d/%m/%Y')}\n\n"
        "Envie 'sim' para confirmar ou 'nao' para cancelar."
    )
    await update.effective_message.reply_text(message)
    return CONFIRM


async def confirmar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.effective_message.text.strip().lower()
    if answer not in ("sim", "nao"):
        await update.effective_message.reply_text(
            "Envie 'sim' para confirmar ou 'nao' para cancelar."
        )
        return CONFIRM
    if answer == "nao":
        await update.effective_message.reply_text(
            "Lembrete cancelado. Nada foi agendado."
        )
        context.user_data.clear()
        return ConversationHandler.END
    return await criar_lembrete(update, context)


async def criar_lembrete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = context.user_data["reminder_text"]
    days = context.user_data["duration_days"]
    horario = context.user_data["reminder_time"]
    today = datetime.now(config.TIMEZONE).date()
    end = today + timedelta(days=days)
    reminder_id = db.add_reminder(
        update.effective_user.id,
        update.effective_chat.id,
        text,
        today.isoformat(),
        end.isoformat(),
        horario.strftime("%H:%M"),
    )
    schedule_reminder(context.job_queue, reminder_id, config.TIMEZONE)
    await update.effective_message.reply_text(
        "Lembrete criado!\n\n"
        f"'{text}'\n"
        f"todos os dias as {horario.strftime('%H:%M')}\n"
        f"por {days} dia(s), ate {end.strftime('%d/%m/%Y')}."
    )
    context.user_data.clear()
    return ConversationHandler.END


async def cancelar_lembrete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.list_active_reminders(update.effective_user.id)
    if not rows:
        await update.effective_message.reply_text("Voce nao tem lembretes ativos.")
        return ConversationHandler.END
    context.user_data["reminders_to_cancel"] = rows
    lines = [
        f"{i + 1}. {r['text']} - todos os dias as {r['time']}"
        for i, r in enumerate(rows)
    ]
    await update.effective_message.reply_text(
        "Lembretes ativos:\n\n"
        + "\n".join(lines)
        + "\n\nEnvie o numero do lembrete que deseja cancelar."
    )
    return CANCEL


async def cancelar_lembrete_numero(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = context.user_data.get("reminders_to_cancel", [])
    raw = update.effective_message.text.strip()
    if not raw.isdigit():
        await update.effective_message.reply_text(
            "Envie apenas o numero do lembrete."
        )
        return CANCEL
    index = int(raw) - 1
    if index < 0 or index >= len(rows):
        await update.effective_message.reply_text(
            "Numero invalido. Envie um numero da lista."
        )
        return CANCEL
    row = rows[index]
    db.deactivate_reminder(row["id"])
    for job in context.job_queue.get_jobs_by_name(f"reminder_{row['id']}"):
        job.schedule_removal()
    await update.effective_message.reply_text(
        f"Lembrete cancelado: '{row['text']}'"
    )
    context.user_data.pop("reminders_to_cancel", None)
    return ConversationHandler.END


def build_application():
    lembrete_conversation = ConversationHandler(
        entry_points=[CommandHandler("lembrete", lembrete_start)],
        states={
            TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_texto)],
            DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_duracao)],
            TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_horario)],
            CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirmar)],
        },
        fallbacks=[CommandHandler("cancelar", cancelar)],
        name="lembrete",
        conversation_timeout=300,
    )

    cancelar_conversation = ConversationHandler(
        entry_points=[CommandHandler("cancelar_lembrete", cancelar_lembrete_start)],
        states={
            CANCEL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cancelar_lembrete_numero)
            ]
        },
        fallbacks=[CommandHandler("cancelar", cancelar)],
        name="cancelar_lembrete",
        conversation_timeout=180,
    )

    application = (
        ApplicationBuilder()
        .token(config.BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("meus_lembretes", meus_lembretes))
    application.add_handler(lembrete_conversation)
    application.add_handler(cancelar_conversation)
    return application


def main():
    if not config.BOT_TOKEN:
        logger.error(
            "BOT_TOKEN nao definido. Defina a variavel de ambiente BOT_TOKEN antes de iniciar."
        )
        raise SystemExit(1)
    db.init_db()
    application = build_application()
    httpd = web.start_http_server(config.PORT)
    logger.info("Bot iniciado. Pressione Ctrl+C para parar.")
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        web.stop_http_server(httpd)


if __name__ == "__main__":
    main()
