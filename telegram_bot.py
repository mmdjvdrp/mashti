import os
import threading
import telebot
from flask import Flask
from supabase import create_client, Client
from google import genai
from google.genai import types

# خواندن کلیدها از Render
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# راه‌اندازی سرور
app = Flask(__name__)
@app.route('/')
def home():
    return "✅ سرور ربات روشن است!"
def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
threading.Thread(target=run_web, daemon=True).start()

# اتصال
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
genai_client = genai.Client(api_key=GEMINI_API_KEY)
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_id = message.from_user.id
    user_name = message.from_user.first_name
    text = message.text.strip()
    text_lower = text.lower()

    bot.send_chat_action(user_id, 'typing')

    try:
        # ==========================================
        # بخش اول: گاوصندوق امنیتی و مخفی
        # ==========================================
        if text.startswith("/lock"):
            try:
                parts = text.split(" ", 3)
                supabase.table("secure_vaults").insert({
                    "user_id": user_id, "box_name": parts[1], 
                    "password": parts[2], "content": parts[3]
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

        # ==========================================
        # بخش دوم: چت، حافظه و اینترنت
        # ==========================================
        
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

        # 🧠 هسته مرکزی ارتقا یافته (دستور یادگیری و تطبیق‌پذیری)
        sys_instruct = f"""تو یک دستیار هوشمند و در حال پیشرفت هستی. نام کاربر تو {user_name} است.
1. تو به اینترنت متصل هستی. اگر کاربر سوالی پرسید که نیاز به اطلاعات لحظه‌ای (مثل اخبار، قیمت‌ها، ورزش و آب‌وهوا) داشت، حتماً از جستجوی گوگل استفاده کن.
2. با لحن صمیمی و دوستانه پاسخ بده.
3. سعی کن از لحن و مکالمات قبلی کاربر یاد بگیری و خودت را با سلیقه او وفق بدهی."""
        
        if saved_facts:
            facts_text = "\n- ".join(saved_facts)
            sys_instruct += f"\n\n⚠️ اطلاعات زیر را درباره کاربر می‌دانی. فقط در زمان نیاز به آن‌ها اشاره کن و طوطی‌وار تکرارشان نکن:\n- {facts_text}"

        # 🌐 روشن کردن قابلیت جستجو در گوگل (Google Search Tool)
        config = types.GenerateContentConfig(
            system_instruction=sys_instruct,
            tools=[{"google_search": {}}]  # این یک خط ربات شما را به اینترنت وصل می‌کند!
        )

        contents = []
        for row in chat_history:
            contents.append(types.Content(role=row["role"], parts=[types.Part.from_text(text=row["content"])]))
            
        if is_save:
            contents.append(types.Content(role="user", parts=[types.Part.from_text(text=text)]))

        # ارتباط با جمنای و دریافت پاسخ (همراه با جستجوی وب در صورت نیاز)
        gemini_response = genai_client.models.generate_content(model='gemini-2.0-flash', contents=contents, config=config)
        bot_reply = gemini_response.text

        if not is_save:
            supabase.table("chat_memory").insert({"user_id": user_id, "role": "model", "content": bot_reply}).execute()

        bot.reply_to(message, bot_reply)

    except Exception as e:
        bot.reply_to(message, f"❌ خطایی رخ داد: {e}")

bot.infinity_polling()
