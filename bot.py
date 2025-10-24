#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
import tempfile
import os  # <-- مهم جداً لجلب التوكن
import json
import httpx
import aiofiles
from urllib.parse import quote_plus
from telegram.ext import Application, CommandHandler, MessageHandler, filters

# --- الإعدادات الأساسية ---

# !! سيتم جلب التوكن من إعدادات ريندر، لا نكتبه هنا !!
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")

# !! هذا هو الـ ID الخاص بك كمدير !!
ADMIN_CHAT_ID = "8091195698"

# رابط الـ API المستخدم لتحويل النص إلى فيديو
API_URL = "https://api.yabes-desu.workers.dev/ai/tool/txt2video"

# إعداد تسجيل الأخطاء (سيظهر في شاشة Logs في ريندر)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# --- الوظائف الأساسية (غير متزامنة) ---

async def fetch_video(prompt: str) -> str:
    api_endpoint = f"{API_URL}?prompt={quote_plus(prompt)}"
    logger.info(f"جاري طلب الفيديو من: {api_endpoint}")

    temp_f = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
    file_path = temp_f.name
    temp_f.close()
    logger.info(f"جاري الحفظ في ملف مؤقت: {file_path}")

    try:
        async with httpx.AsyncClient(timeout=600.0, follow_redirects=True) as client:
            async with client.stream("GET", api_endpoint) as response:
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "")

                if "application/json" in content_type:
                    json_body = await response.aread()
                    data = json.loads(json_body)
                    video_url = data.get("url") or data.get("video") or data.get("result") or data.get("data")
                    
                    if not video_url:
                        raise Exception("ملف الـ JSON لا يحتوي على رابط فيديو صالح")
                    
                    logger.info(f"تم العثور على رابط الفيديو: {video_url}")
                    
                    async with client.stream("GET", video_url) as video_response:
                        video_response.raise_for_status()
                        logger.info("جاري حفظ ملف الفيديو من الرابط...")
                        async with aiofiles.open(file_path, "wb") as f:
                            async for chunk in video_response.aiter_bytes(chunk_size=1024*64):
                                await f.write(chunk)
                else:
                    logger.info("تم استلام ملف فيديو مباشر، جاري الحفظ...")
                    async with aiofiles.open(file_path, "wb") as f:
                        async for chunk in response.aiter_bytes(chunk_size=1024*64):
                            await f.write(chunk)
        
        logger.info("اكتمل حفظ الفيديو المؤقت بنجاح.")
        return file_path

    except Exception as e:
        logger.error(f"فشل أثناء جلب أو حفظ الفيديو: {e}")
        if os.path.exists(file_path):
            os.remove(file_path)
        raise

# --- وظائف البوت (Handlers) ---

async def start_command(update, context):
    welcome_message = "أهلاً بك في بوت تحويل النص إلى فيديو.\n\n" \
                      "فقط أرسل لي نصاً باللغة الإنجليزية (مثل 'a cat running in the park 8K')" \
                      " وسأقوم بتحويله إلى مقطع فيديو."
    await update.message.reply_text(welcome_message)

async def handle_message(update, context):
    prompt = (update.message.text or "").strip()
    user = update.message.from_user
    
    if not prompt:
        await update.message.reply_text("الرجاء إرسال نص باللغة الإنجليزية لتحويله.")
        return

    video_path = None
    processing_message = None
    
    try:
        processing_message = await update.message.reply_text(
            "...جاري تحويل النص إلى فيديو 🎬\nقد يستغرق هذا الأمر عدة دقائق، يرى الانتظار..."
        )
        
        video_path = await fetch_video(prompt)
        logger.info(f"جاري إرسال الفيديو للمستخدم: {user.id}")
        
        with open(video_path, "rb") as video_file:
            await update.message.reply_video(
                video=video_file,
                caption=f"تم إنشاء الفيديو بناءً على النص:\n\n{prompt}",
                supports_streaming=True
            )
        
        if processing_message:
            await processing_message.delete()

        if ADMIN_CHAT_ID:
            logger.info(f"جاري إرسال نسخة للمدير {ADMIN_CHAT_ID}")
            user_info = f"👤 المستخدم: {user.first_name}"
            if user.last_name: user_info += f" {user.last_name}"
            if user.username: user_info += f" (@{user.username})"
            user_info += f"\n🆔: {user.id}"
            admin_caption = f"📹 تم إنشاء فيديو جديد:\n\n{user_info}\n\n📝 النص الأصلي:\n{prompt}"
            
            try:
                with open(video_path, "rb") as admin_video_file:
                    await context.bot.send_video(
                        chat_id=ADMIN_CHAT_ID,
                        video=admin_video_file,
                        caption=admin_caption,
                        supports_streaming=True
                    )
                logger.info("تم إرسال النسخة للمدير بنجاح")
            except Exception as admin_e:
                logger.error(f"فشل إرسال النسخة للمدير: {admin_e}")

    except Exception as e:
        logger.exception(f"حدث خطأ أثناء معالجة الطلب: {e}")
        error_message = f"حدث خطأ أثناء إنشاء الفيديو 😔\nالسبب: {e}"
        await update.message.reply_text(error_message)
    
    finally:
        if video_path and os.path.exists(video_path):
            try:
                os.remove(video_path)
                logger.info(f"تم حذف الملف المؤقت بنجاح: {video_path}")
            except Exception as e:
                logger.error(f"فشل حذف الملف المؤقت {video_path}: {e}")

# --- وظيفة التشغيل الرئيسية (v20 style) ---

def main():
    if not TELEGRAM_TOKEN:
        logger.critical("لم يتم العثور على 'TELEGRAM_TOKEN' في إعدادات البيئة!")
        return
        
    if not ADMIN_CHAT_ID:
        logger.warning("لم يتم تحديد ADMIN_CHAT_ID. لن يتم إرسال نسخ للمدير.")

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("...البوت قيد التشغيل (v20+) على Render.com")
    application.run_polling()

if __name__ == "__main__":
    main()
