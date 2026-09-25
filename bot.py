import os
import asyncio
import subprocess
from datetime import datetime

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
MAX_STREAMS = 5

streams = {}
user_sessions = {}


def ffmpeg_exists():
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except Exception:
        return False


def get_free_slot():
    for number in range(1, MAX_STREAMS + 1):
        if number not in streams:
            return number
    return None


def start_ffmpeg(source_url, stream_key):
    output_url = f"rtmps://live-api-s.facebook.com:443/rtmp/{stream_key}"

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "10",
        "-i", source_url,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-r", "30",
        "-g", "60",
        "-keyint_min", "60",
        "-sc_threshold", "0",
        "-f", "flv",
        output_url,
    ]

    return subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    user_sessions[user_id] = {"step": "stream_key"}

    await update.message.reply_text(
        "📡 بدء بث جديد\n\n"
        "أرسل الآن Facebook Stream Key 🔑"
    )


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in user_sessions:
        return

    text = update.message.text.strip()
    session = user_sessions[user_id]

    if session["step"] == "stream_key":
        if not text:
            await update.message.reply_text("❌ Stream Key غير صالح.")
            return

        session["stream_key"] = text
        session["step"] = "source"

        await update.message.reply_text(
            "✅ تم استلام Stream Key.\n\n"
            "الآن أرسل رابط المصدر 🔗\n\n"
            "يمكن أن يكون M3U8 أو MPD أو MP4 أو رابط M3U/IPTV."
        )
        return

    if session["step"] == "source":
        source_url = text
        stream_key = session["stream_key"]
        slot = get_free_slot()

        if slot is None:
            del user_sessions[user_id]
            await update.message.reply_text(
                "⛔ جميع الخانات الخمس قيد التشغيل.\n\n"
                "أوقف أحد البثوث أولاً باستخدام /stop 1 إلى /stop 5."
            )
            return

        await update.message.reply_text(
            f"⏳ جاري تشغيل البث رقم {slot}..."
        )

        try:
            process = start_ffmpeg(source_url, stream_key)

            streams[slot] = {
                "process": process,
                "key": stream_key,
                "url": source_url,
                "started": datetime.now(),
                "user_id": user_id,
            }

            del user_sessions[user_id]

            await update.message.reply_text(
                f"🟢 تم تشغيل البث رقم {slot}\n\n"
                f"📺 الحالة: Live\n"
                f"🔢 الرقم: {slot}\n\n"
                f"لإيقافه: /stop {slot}\n"
                f"لمشاهدة الحالة: /live"
            )

        except Exception as e:
            del user_sessions[user_id]
            await update.message.reply_text(
                f"❌ فشل تشغيل FFmpeg.\n\nالخطأ: {str(e)}"
            )


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "استخدم الأمر هكذا:\n"
            "/stop 1\n/stop 2\n/stop 3\n/stop 4\n/stop 5"
        )
        return

    try:
        slot = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ يجب إدخال رقم من 1 إلى 5.")
        return

    if slot < 1 or slot > MAX_STREAMS:
        await update.message.reply_text("❌ الرقم يجب أن يكون من 1 إلى 5.")
        return

    if slot not in streams:
        await update.message.reply_text(f"🔴 البث رقم {slot} غير مشغل.")
        return

    process = streams[slot]["process"]

    try:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
    except Exception:
        pass

    del streams[slot]

    await update.message.reply_text(
        f"⛔ تم إيقاف البث رقم {slot}."
    )


async def live_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = "📡 حالة البثوث\n\n"

    for slot in range(1, MAX_STREAMS + 1):
        if slot not in streams:
            message += f"{slot} 🔴 متوقف\n"
            continue

        stream = streams[slot]
        process = stream["process"]

        if process.poll() is not None:
            del streams[slot]
            message += f"{slot} 🔴 انتهى / متوقف\n"
            continue

        duration = datetime.now() - stream["started"]
        total_seconds = int(duration.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60

        message += (
            f"{slot} 🟢 Live\n"
            f"   مدة التشغيل: {hours:02d}:{minutes:02d}:{seconds:02d}\n"
        )

    await update.message.reply_text(message)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📡 أوامر البوت\n\n"
        "/start — بدء بث جديد\n"
        "/live — عرض البثوث الحالية\n"
        "/stop 1 — إيقاف البث رقم 1\n"
        "/stop 2 — إيقاف البث رقم 2\n"
        "/stop 3 — إيقاف البث رقم 3\n"
        "/stop 4 — إيقاف البث رقم 4\n"
        "/stop 5 — إيقاف البث رقم 5"
    )


async def monitor_streams():
    while True:
        stopped = []

        for slot, stream in list(streams.items()):
            if stream["process"].poll() is not None:
                stopped.append(slot)

        for slot in stopped:
            streams.pop(slot, None)

        await asyncio.sleep(10)


async def post_init(application):
    asyncio.create_task(monitor_streams())


def main():
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN environment variable is missing.")
        return

    if not ffmpeg_exists():
        print("ERROR: FFmpeg is not installed.")
        return

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(CommandHandler("live", live_command))
    application.add_handler(CommandHandler("help", help_command))

    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler)
    )

    print("Telegram bot started.")
    application.run_polling()


if __name__ == "__main__":
    main()
