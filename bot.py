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

# بث واحد فقط
stream = None
session = {}

# عدد محاولات إعادة الاتصال
MAX_RECONNECT_ATTEMPTS = 20


def start_ffmpeg(source, stream_key):
    output = f"rtmps://live-api-s.facebook.com:443/rtmp/{stream_key}"

    cmd = [
        "ffmpeg",

        "-hide_banner",
        "-loglevel", "warning",

        # إعادة الاتصال بمصدر M3U8
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_at_eof", "1",
        "-reconnect_delay_max", "10",

        "-i", source,

        # ترميز مناسب لـ Facebook
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",

        "-r", "30",
        "-g", "60",
        "-keyint_min", "60",
        "-sc_threshold", "0",

        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",

        "-f", "flv",
        output,
    ]

    # نكتب أخطاء FFmpeg إلى ملف حتى لا يمتلئ stderr
    log_file = open("/tmp/ffmpeg.log", "a", buffering=1)

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=log_file,
    )

    return process, log_file


def get_last_error():
    try:
        with open("/tmp/ffmpeg.log", "r", errors="ignore") as f:
            data = f.read()

        if not data:
            return "لا توجد رسالة خطأ من FFmpeg."

        return data[-2500:]

    except Exception as e:
        return f"تعذر قراءة سجل FFmpeg: {e}"


def duration(start_time):
    seconds = int(
        (datetime.now() - start_time).total_seconds()
    )

    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60

    return f"{h:02d}:{m:02d}:{s:02d}"


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    global session

    if stream is not None:
        if stream["process"].poll() is None:
            await update.message.reply_text(
                "⚠️ يوجد بث يعمل بالفعل.\n\n"
                "استخدم /stop لإيقافه."
            )
            return

    session = {
        "user_id": update.effective_user.id,
        "step": "key",
    }

    await update.message.reply_text(
        "📡 بدء بث Facebook\n\n"
        "أرسل Facebook Stream Key 🔑"
    )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    global session
    global stream

    if not session:
        return

    text = update.message.text.strip()

    if not text:
        return

    # المرحلة الأولى: Stream Key
    if session["step"] == "key":

        session["stream_key"] = text
        session["step"] = "source"

        await update.message.reply_text(
            "✅ تم استلام Stream Key.\n\n"
            "الآن أرسل رابط M3U8 🔗"
        )

        return

    # المرحلة الثانية: رابط M3U8
    if session["step"] == "source":

        source = text
        stream_key = session["stream_key"]
        user_id = session["user_id"]

        await update.message.reply_text(
            "⏳ جاري تشغيل البث...\n"
            "انتظر قليلاً."
        )

        try:

            process, log_file = start_ffmpeg(
                source,
                stream_key
            )

            await asyncio.sleep(5)

            if process.poll() is not None:

                error = get_last_error()

                log_file.close()

                session = {}

                await update.message.reply_text(
                    "❌ فشل تشغيل البث.\n\n"
                    "📋 سبب FFmpeg:\n\n"
                    f"{error}"
                )

                return

            stream = {
                "process": process,
                "log_file": log_file,
                "source": source,
                "stream_key": stream_key,
                "user_id": user_id,
                "started": datetime.now(),
                "reconnects": 0,
            }

            session = {}

            await update.message.reply_text(
                "🟢 البث يعمل الآن.\n\n"
                "🔄 إعادة الاتصال التلقائية: مفعلة\n\n"
                "⛔ إيقاف: /stop\n"
                "📊 الحالة: /live"
            )

        except Exception as e:

            session = {}

            await update.message.reply_text(
                f"❌ حدث خطأ:\n\n{e}"
            )


async def stop_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global stream

    if stream is None:
        await update.message.reply_text(
            "🔴 لا يوجد بث يعمل."
        )
        return

    process = stream["process"]

    try:

        if process.poll() is None:
            process.terminate()

            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()

    except Exception:
        pass

    try:
        stream["log_file"].close()
    except Exception:
        pass

    stream = None

    await update.message.reply_text(
        "⛔ تم إيقاف البث."
    )


async def live_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global stream

    if stream is None:

        await update.message.reply_text(
            "🔴 البث متوقف."
        )

        return

    process = stream["process"]

    if process.poll() is not None:

        error = get_last_error()

        try:
            stream["log_file"].close()
        except Exception:
            pass

        stream = None

        await update.message.reply_text(
            "🔴 البث توقف.\n\n"
            "📋 آخر خطأ من FFmpeg:\n\n"
            f"{error}"
        )

        return

    await update.message.reply_text(
        "🟢 البث يعمل\n\n"
        f"⏱ المدة: {duration(stream['started'])}\n"
        f"🔄 محاولات إعادة الاتصال: "
        f"{stream['reconnects']}"
    )


async def monitor(
    application: Application
):

    global stream

    while True:

        await asyncio.sleep(10)

        if stream is None:
            continue

        process = stream["process"]

        # ما زال يعمل
        if process.poll() is None:
            continue

        user_id = stream["user_id"]

        error = get_last_error()

        source = stream["source"]
        stream_key = stream["stream_key"]

        reconnect_number = stream["reconnects"] + 1

        # إذا توقف FFmpeg نحاول تشغيله من جديد
        if reconnect_number <= MAX_RECONNECT_ATTEMPTS:

            stream["reconnects"] = reconnect_number

            try:
                stream["log_file"].close()
            except Exception:
                pass

            await application.bot.send_message(
                chat_id=user_id,
                text=(
                    "⚠️ انقطع البث!\n\n"
                    f"🔄 محاولة إعادة الاتصال: "
                    f"{reconnect_number}/{MAX_RECONNECT_ATTEMPTS}\n\n"
                    "📋 آخر رسالة FFmpeg:\n"
                    f"{error[-1500:]}"
                )
            )

            await asyncio.sleep(5)

            try:

                new_process, new_log = start_ffmpeg(
                    source,
                    stream_key
                )

                await asyncio.sleep(5)

                if new_process.poll() is None:

                    stream["process"] = new_process
                    stream["log_file"] = new_log

                    await application.bot.send_message(
                        chat_id=user_id,
                        text=(
                            "🟢 تم استئناف البث بنجاح.\n\n"
                            f"🔄 محاولة رقم {reconnect_number}"
                        )
                    )

                else:

                    new_error = get_last_error()

                    try:
                        new_log.close()
                    except Exception:
                        pass

                    await application.bot.send_message(
                        chat_id=user_id,
                        text=(
                            "❌ فشلت إعادة الاتصال.\n\n"
                            f"📋 الخطأ:\n"
                            f"{new_error[-1500:]}"
                        )
                    )

            except Exception as e:

                await application.bot.send_message(
                    chat_id=user_id,
                    text=(
                        "❌ خطأ أثناء إعادة تشغيل FFmpeg:\n\n"
                        f"{e}"
                    )
                )

        else:

            try:
                stream["log_file"].close()
            except Exception:
                pass

            await application.bot.send_message(
                chat_id=user_id,
                text=(
                    "🔴 تم إيقاف البث نهائيًا.\n\n"
                    "❌ فشلت جميع محاولات إعادة الاتصال.\n\n"
                    "📋 آخر خطأ:\n"
                    f"{error[-2000:]}"
                )
            )

            stream = None


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "📖 أوامر البوت\n\n"
        "/start — بدء بث جديد\n"
        "/stop — إيقاف البث\n"
        "/live — حالة البث\n"
        "/help — المساعدة"
    )


async def post_init(
    application: Application
):

    asyncio.create_task(
        monitor(application)
    )


def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN غير موجود في Railway Variables"
        )

    application = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start_command)
    )

    application.add_handler(
        CommandHandler("stop", stop_command)
    )

    application.add_handler(
        CommandHandler("live", live_command)
    )

    application.add_handler(
        CommandHandler("help", help_command)
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler
        )
    )

    print("Bot started...")

    application.run_polling()


if __name__ == "__main__":
    main()
