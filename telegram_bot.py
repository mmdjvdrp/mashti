import os
import threading
import telebot
import random
from flask import Flask
from supabase import create_client, Client
from google import genai
from google.genai import types

# خواندن کلیدها از Render
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# ==========================================
# 🔑 سیستم چرخش کلیدها (Load Balancing)
# ==========================================
# کلیدهای جمنای که با ویرگول جدا کرده‌اید را دریافت کرده و تبدیل به یک لیست می‌کنیم
keys_string = os.environ.get("GEMINI_API_KEY", "")
GEMINI_KEYS = [k.strip() for k in keys_string.split(",") if k.strip()]

# برای هر کلید، یک خط ارتباطی جداگانه با گوگل می‌سازیم
genai_clients = [genai.Client(api_key=key) for key in GEMINI_KEYS]

# راه‌اندازی سرور
app = Flask(__name__)
@app.route('/')
def home():
    return "✅ سرور ربات روشن است!"
def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
threading.Thread(target=run_web, daemon=True).start()

# اتصال به دیتابیس و تلگرام
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_id = message.from_user.id
    user_name = message.from_user.first_name
    text = message.text.strip()
    text_lower = text.lower()

    bot.send_chat_action(user_id, 'typing')

    try:
        # 1. بخش گاوصندوق امنیتی
        if text.startswith("/lock"):
            try:
                parts = text.split(" ", 3)
                supabase.table("secure_vaults").insert({
                    "user_id": user_id, "box_name": parts[1], "password": parts[2], "content": parts[3]
                }).execute()
                bot.reply_to(message, f"🔒 اطلاعات در جعبه '{parts[1]}' قفل شد.")
            except Exception:
                bot.reply_to(message, "❌ فرمت اشتباه است: /lock [اسم] [رمز] [متن]")
            return 

        if text.startswith("/unlock"):
            try:
                parts = text.split(" ", 2)
                res = supabase.table("secure_vaults").select("content").eq("user_id", user_id).eq("box_name", parts[1]).eq("password", parts[2]).execute()
                if res.data:
                    box_contents = "\n- ".join([row["content"] for row in res.data])
                    bot.reply_to(message, f"🔓 اطلاعات جعبه:\n\n- {box_contents}")
                else:
                    bot.reply_to(message, "❌ جعبه پیدا نشد یا رمز اشتباه است!")
            except Exception:
                bot.reply_to(message, "❌ فرمت اشتباه است: /unlock [اسم] [رمز]")
            return 

        # 2. بخش چت، حافظه و اینترنت
        is_save = False
        if text_lower.endswith("save") or text_lower.endswith("ذخیره کن"):
            is_save = True

        if is_save:
            supabase.table("chat_memory").insert({"user_id": user_id, "role": "fact", "content": text}).execute()
        else:
            supabase.table("chat_memory").insert({"user_id": user_id, "role": "user", "content": text}).execute()

        facts_res = supabase.table("chat_memory").select("content").eq("user_id", user_id).eq("role", "fact").execute()
        saved_facts = [row["content"] for row in facts_res.data]
        
        history_res = supabase.table("chat_memory").select("*").eq("user_id", user_id).neq("role", "fact").order("created_at", desc=True).limit(10).execute()
        chat_history = history_res.data[::-1]

        sys_instruct = f"""تو یک دستیار هوشمند هستی. نام کاربر تو {user_name} است.
1. تو به اینترنت متصل هستی. در صورت نیاز جستجو کن.
2. صمیمی باش و از قوانین کاربر پیروی کن."""
        if saved_facts:
            facts_text = "\n- ".join(saved_facts)
            sys_instruct += f"\n\n⚠️ قوانین و اطلاعات دائم کاربر (تکرارشان نکن مگر نیاز باشد):\n- {facts_text}"

        config = types.GenerateContentConfig(system_instruction=sys_instruct, tools=[{"google_search": {}}])

        contents = []
        for row in chat_history:
            contents.append(types.Content(role=row["role"], parts=[types.Part.from_text(text=row["content"])]))
        if is_save:
            contents.append(types.Content(role="user", parts=[types.Part.from_text(text=text)]))

        # ==========================================
        # 🔄 تلاش خودکار برای عبور از محدودیت گوگل
        # ==========================================
        bot_reply = None
        
        # کلیدها را به صورت تصادفی پخش می‌کنیم تا فشار روی یک کلید نیفتد
        random.shuffle(genai_clients) 
        
        for client in genai_clients:
            try:
                # درخواست با مدل اصلی و قدرتمند
                response = client.models.generate_content(model='gemini-2.5-flash', contents=contents, config=config)
                bot_reply = response.text
                break # اگر موفق شد، از حلقه خارج شو
            except Exception as e:
                if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                    # اگر این کلید محدود شده بود، ادامه بده و با کلید بعدی تست کن
                    continue
                else:
                    # اگر خطای دیگری بود (مثل قطعی نت گوگل)، آن را اعلام کن
                    raise e
                    
        # اگر حلقه تمام شد و هیچ کلیدی کار نکرد:
        if not bot_reply:
            bot_reply = "❌ تمام کلیدهای هوش مصنوعیِ من موقتاً مسدود شده‌اند! لطفاً ۳۰ ثانیه دیگر پیام بدهید."

        # ذخیره جواب ربات در دیتابیس (اگر موفقیت‌آمیز بود)
        if not is_save and bot_reply and not bot_reply.startswith("❌"):
            supabase.table("chat_memory").insert({"user_id": user_id, "role": "model", "content": bot_reply}).execute()

        bot.reply_to(message, bot_reply)

    except Exception as e:
        bot.reply_to(message, f"❌ خطایی رخ داد: {e}")

bot.infinity_polling()
