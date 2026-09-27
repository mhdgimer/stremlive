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


# =========================================================
# إعدادات
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

# بث واحد فقط
stream = None

# جلسة إدخال البيانات
session = {}

# أقصى عدد لمحاولات إعادة تشغيل FFmpeg
MAX_RECONNECT_ATTEMPTS = 20

# الوقت بين محاولات إعادة التشغيل
RECONNECT_WAIT = 5

# مدة الانتظار بعد تشغيل FFmpeg للتأكد أنه بدأ
START_CHECK_TIME = 5

# ملف السجل
LOG_FILE = "/tmp/ffmpeg.log"


# =========================================================
# تشغيل FFmpeg
# =========================================================

def start_ffmpeg(source, stream_key):

    output = (
        f"rtmps://live-api.facebook.com:443/rtmp/"
        f"{stream_key}"
    )

    cmd = [
        "ffmpeg",

        "-hide_banner",
        "-loglevel", "warning",

        # =================================================
        # إعادة الاتصال بالمصدر
        # =================================================

        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_at_eof", "1",

        # إعادة الاتصال عند أخطاء الشبكة
        "-reconnect_on_network_error", "1",

        # إعادة الاتصال عند أخطاء HTTP
        "-reconnect_on_http_error", "4xx,5xx",

        # أقصى وقت انتظار بين المحاولات
        "-reconnect_delay_max", "10",

        # =================================================
        # السماح بامتدادات HLS المختلفة
        # =================================================

        "-allowed_extensions", "ALL",

        # =================================================
        # المصدر
        # =================================================

        "-i", source,

        # =================================================
        # الفيديو
        # =================================================

        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",

        # 30 FPS
        "-r", "30",

        # Keyframe كل ثانيتين
        "-g", "60",
        "-keyint_min", "60",
        "-sc_threshold", "0",

        # =================================================
        # الصوت
        # =================================================

        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",

        # =================================================
        # Facebook Live
        # =================================================

        "-f", "flv",

        output,
    ]

    # فتح ملف السجل
    log_file = open(
        LOG_FILE,
        "a",
        buffering=1
    )

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=log_file,
    )

    return process, log_file


# =========================================================
# قراءة آخر خطأ
# =========================================================

def get_last_error():

    try:

        if not os.path.exists(LOG_FILE):
            return "لا يوجد سجل FFmpeg."

        with open(
            LOG_FILE,
            "r",
            errors="ignore"
        ) as f:

            data = f.read()

        if not data:
            return "لا توجد رسالة خطأ من FFmpeg."

        return data[-3000:]

    except Exception as e:

        return (
            "تعذر قراءة سجل FFmpeg:\n"
            f"{e}"
        )


# =========================================================
# حساب مدة البث
# =========================================================

def duration(start_time):

    seconds = int(
        (
            datetime.now() - start_time
        ).total_seconds()
    )

    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60

    return (
        f"{h:02d}:"
        f"{m:02d}:"
        f"{s:02d}"
    )


# =========================================================
# /start
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global session
    global stream

    # التأكد من وجود بث
    if stream is not None:

        if stream["process"].poll() is None:

            await update.message.reply_text(
                "⚠️ يوجد بث يعمل بالفعل.\n\n"
                "استخدم /stop لإيقافه."
            )

            return

    # إنشاء جلسة جديدة
    session = {
        "user_id": update.effective_user.id,
        "step": "key",
    }

    await update.message.reply_text(
        "📡 بدء بث Facebook\n\n"
        "أرسل Facebook Stream Key 🔑"
    )


# =========================================================
# استقبال Stream Key + الرابط
# =========================================================

async def text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global session
    global stream

    if not session:
        return

    if not update.message:
        return

    text = update.message.text

    if not text:
        return

    text = text.strip()

    if not text:
        return

    # =====================================================
    # المرحلة الأولى
    # Stream Key
    # =====================================================

    if session["step"] == "key":

        session["stream_key"] = text
        session["step"] = "source"

        await update.message.reply_text(
            "✅ تم استلام Stream Key.\n\n"
            "الآن أرسل رابط الفيديو.\n\n"
            "يدعم مثلًا:\n"
            "• M3U8\n"
            "• M3U\n"
            "• MPD\n"
            "• TS\n"
            "• MP4\n"
            "• روابط HTTP/HTTPS التي يتعرف عليها FFmpeg"
        )

        return

    # =====================================================
    # المرحلة الثانية
    # رابط الفيديو
    # =====================================================

    if session["step"] == "source":

        source = text

        stream_key = session["stream_key"]

        user_id = session["user_id"]

        await update.message.reply_text(
            "⏳ جاري تشغيل البث...\n\n"
            "قد يستغرق ذلك عدة ثوانٍ."
        )

        try:

            # تشغيل FFmpeg
            process, log_file = start_ffmpeg(
                source,
                stream_key
            )

            # الانتظار للتأكد من التشغيل
            await asyncio.sleep(
                START_CHECK_TIME
            )

            # =================================================
            # فشل التشغيل
            # =================================================

            if process.poll() is not None:

                error = get_last_error()

                try:
                    log_file.close()
                except Exception:
                    pass

                session = {}

                await update.message.reply_text(
                    "❌ فشل تشغيل البث.\n\n"
                    "📋 سبب FFmpeg:\n\n"
                    f"{error}"
                )

                return

            # =================================================
            # حفظ بيانات البث
            # =================================================

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

                "📡 المصدر:\n"
                f"{source}\n\n"

                "🔄 إعادة الاتصال التلقائية: مفعلة\n"
                f"🔁 أقصى محاولات: "
                f"{MAX_RECONNECT_ATTEMPTS}\n\n"

                "⛔ إيقاف: /stop\n"
                "📊 الحالة: /live"
            )

        except Exception as e:

            session = {}

            await update.message.reply_text(
                "❌ حدث خطأ أثناء تشغيل البث:\n\n"
                f"{e}"
            )


# =========================================================
# /stop
# =========================================================

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

        # إيقاف FFmpeg
        if process.poll() is None:

            process.terminate()

            try:

                process.wait(
                    timeout=10
                )

            except subprocess.TimeoutExpired:

                process.kill()

    except Exception:
        pass

    # إغلاق ملف السجل
    try:

        stream["log_file"].close()

    except Exception:
        pass

    stream = None

    await update.message.reply_text(
        "⛔ تم إيقاف البث."
    )


# =========================================================
# /live
# =========================================================

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

    # =====================================================
    # FFmpeg توقف
    # =====================================================

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

    # =====================================================
    # البث يعمل
    # =====================================================

    await update.message.reply_text(
        "🟢 البث يعمل\n\n"

        f"⏱ المدة: "
        f"{duration(stream['started'])}\n\n"

        f"🔄 محاولات إعادة الاتصال: "
        f"{stream['reconnects']}/{MAX_RECONNECT_ATTEMPTS}"
    )


# =========================================================
# مراقبة FFmpeg
# =========================================================

async def monitor(
    application: Application
):

    global stream

    while True:

        await asyncio.sleep(10)

        # لا يوجد بث
        if stream is None:
            continue

        process = stream["process"]

        # =================================================
        # FFmpeg ما زال يعمل
        # =================================================

        if process.poll() is None:
            continue

        # =================================================
        # FFmpeg توقف
        # =================================================

        user_id = stream["user_id"]

        source = stream["source"]

        stream_key = stream["stream_key"]

        error = get_last_error()

        reconnect_number = (
            stream["reconnects"] + 1
        )

        # =================================================
        # ما زالت هناك محاولات
        # =================================================

        if reconnect_number <= MAX_RECONNECT_ATTEMPTS:

            stream["reconnects"] = reconnect_number

            # إغلاق log القديم
            try:
                stream["log_file"].close()
            except Exception:
                pass

            # =================================================
            # إرسال تنبيه
            # =================================================

            try:

                await application.bot.send_message(

                    chat_id=user_id,

                    text=(
                        "⚠️ انقطع البث!\n\n"

                        f"🔄 محاولة إعادة الاتصال: "
                        f"{reconnect_number}/"
                        f"{MAX_RECONNECT_ATTEMPTS}\n\n"

                        "⏳ سيتم المحاولة تلقائيًا...\n\n"

                        "📋 آخر رسالة FFmpeg:\n"

                        f"{error[-1500:]}"
                    )
                )

            except Exception:
                pass

            # انتظار قبل إعادة التشغيل
            await asyncio.sleep(
                RECONNECT_WAIT
            )

            # =================================================
            # إعادة تشغيل FFmpeg
            # =================================================

            try:

                new_process, new_log = start_ffmpeg(
                    source,
                    stream_key
                )

                # الانتظار للتأكد من نجاح التشغيل
                await asyncio.sleep(
                    START_CHECK_TIME
                )

                # =================================================
                # نجح
                # =================================================

                if new_process.poll() is None:

                    stream["process"] = new_process

                    stream["log_file"] = new_log

                    try:

                        await application.bot.send_message(

                            chat_id=user_id,

                            text=(
                                "🟢 تم استئناف البث بنجاح.\n\n"

                                f"🔄 محاولة رقم "
                                f"{reconnect_number}/"
                                f"{MAX_RECONNECT_ATTEMPTS}"
                            )
                        )

                    except Exception:
                        pass

                # =================================================
                # فشل
                # =================================================

                else:

                    new_error = get_last_error()

                    try:
                        new_log.close()
                    except Exception:
                        pass

                    try:

                        await application.bot.send_message(

                            chat_id=user_id,

                            text=(
                                "❌ فشلت إعادة الاتصال.\n\n"

                                f"🔄 المحاولة: "
                                f"{reconnect_number}/"
                                f"{MAX_RECONNECT_ATTEMPTS}\n\n"

                                "📋 الخطأ:\n"

                                f"{new_error[-1500:]}"
                            )
                        )

                    except Exception:
                        pass

            except Exception as e:

                try:

                    await application.bot.send_message(

                        chat_id=user_id,

                        text=(
                            "❌ خطأ أثناء إعادة تشغيل "
                            "FFmpeg:\n\n"
                            f"{e}"
                        )
                    )

                except Exception:
                    pass

        # =====================================================
        # انتهت جميع المحاولات
        # =====================================================

        else:

            try:
                stream["log_file"].close()
            except Exception:
                pass

            try:

                await application.bot.send_message(

                    chat_id=user_id,

                    text=(
                        "🔴 تم إيقاف البث نهائيًا.\n\n"

                        "❌ فشلت جميع محاولات "
                        "إعادة الاتصال.\n\n"

                        f"🔄 عدد المحاولات: "
                        f"{MAX_RECONNECT_ATTEMPTS}\n\n"

                        "📋 آخر خطأ:\n"

                        f"{error[-2000:]}"
                    )
                )

            except Exception:
                pass

            stream = None


# =========================================================
# /help
# =========================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(

        "📖 أوامر البوت\n\n"

        "/start — بدء بث جديد\n"

        "/stop — إيقاف البث\n"

        "/live — حالة البث\n"

        "/help — المساعدة\n\n"

        "📡 أنواع الروابط المدعومة:\n"

        "• M3U8\n"
        "• M3U\n"
        "• MPD\n"
        "• TS\n"
        "• MP4\n"
        "• HTTP / HTTPS"
    )


# =========================================================
# تشغيل Monitor
# =========================================================

async def post_init(
    application: Application
):

    asyncio.create_task(
        monitor(application)
    )


# =========================================================
# Main
# =========================================================

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

    # =====================================================
    # الأوامر
    # =====================================================

    application.add_handler(

        CommandHandler(
            "start",
            start_command
        )
    )

    application.add_handler(

        CommandHandler(
            "stop",
            stop_command
        )
    )

    application.add_handler(

        CommandHandler(
            "live",
            live_command
        )
    )

    application.add_handler(

        CommandHandler(
            "help",
            help_command
        )
    )

    # =====================================================
    # استقبال الروابط والنصوص
    # =====================================================

    application.add_handler(

        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler
        )
    )

    print(
        "Bot started..."
    )

    # =====================================================
    # تشغيل البوت
    # =====================================================

    application.run_polling()


# =========================================================
# تشغيل البرنامج
# =========================================================

if __name__ == "__main__":

    main()
