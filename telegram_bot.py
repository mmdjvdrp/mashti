import os
import threading
import telebot
from flask import Flask
from supabase import create_client, Client
from google import genai
from google.genai import types

# خواندن کلیدها به صورت مخفی و امن از سرور
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# راه‌اندازی وب‌سرور برای Render
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ سرور ربات روشن است!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_web, daemon=True).start()

# اتصال به سرویس‌ها
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
genai_client = genai.Client(api_key=GEMINI_API_KEY)
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

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

bot.infinity_polling()
