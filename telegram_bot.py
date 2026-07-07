import os
import threading
import telebot
from flask import Flask
from supabase import create_client, Client
from google import genai
from google.genai import types

# 1. تنظیمات کلیدها (اینجا اطلاعات خودت را بگذار)
TELEGRAM_BOT_TOKEN = "8728567806:AAHFdit3ATkGwWZzf5dpoJ9ZEj0qK4_D8EA"
GEMINI_API_KEY = "AQ.Ab8RN6JBqx0GnZF8_Tj6pjTlEP0kBI-Kmkt6pGZ4-2E_wwTljQ "
SUPABASE_URL = "https://yoooqtgynrsmawccpqyj.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inlvb29xdGd5bnJzbWF3Y2NwcXlqIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MzM1MjU0OSwiZXhwIjoyMDk4OTI4NTQ5fQ.2AEHs8i53FBPUC_n_06g35JmmcGgEf1rALSkEgvI0tc"

# 2. راه‌اندازی سرور وب کوچک برای اینکه Render ربات را خاموش نکند
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ سرور ربات روشن است!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# اجرای وب‌سرور در پس‌زمینه
threading.Thread(target=run_web, daemon=True).start()

# 3. راه‌اندازی ربات و دیتابیس
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
genai_client = genai.Client(api_key=GEMINI_API_KEY)
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# 4. دریافت پیام‌های تلگرام
@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_id = message.from_user.id
    user_name = message.from_user.first_name
    user_text = message.text

    bot.send_chat_action(user_id, 'typing')

    try:
        # ذخیره پیام کاربر
        supabase.table("chat_memory").insert({"user_id": user_id, "role": "user", "content": user_text}).execute()

        # خواندن تاریخچه از دیتابیس
        response = supabase.table("chat_memory").select("*").eq("user_id", user_id).order("created_at", desc=False).limit(20).execute()
        
        contents = []
        for row in response.data:
            contents.append(types.Content(role=row["role"], parts=[types.Part.from_text(text=row["content"])]))

        sys_instruct = f"تو یک دستیار شخصی در تلگرام هستی. نام کاربری که با تو صحبت می‌کند {user_name} است. به زبان فارسی صمیمی پاسخ بده."
        config = types.GenerateContentConfig(system_instruction=sys_instruct)

        # دریافت جواب از گوگل
        gemini_response = genai_client.models.generate_content(model='gemini-2.5-flash', contents=contents, config=config)
        bot_reply = gemini_response.text

        # ذخیره جواب ربات
        supabase.table("chat_memory").insert({"user_id": user_id, "role": "model", "content": bot_reply}).execute()

        # ارسال جواب
        bot.reply_to(message, bot_reply)

    except Exception as e:
        bot.reply_to(message, f"❌ خطایی رخ داد: {e}")

# 5. روشن نگه‌داشتن ربات تلگرام
bot.infinity_polling()
