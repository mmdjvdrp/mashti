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
        # کوچک کردن حروف برای اینکه اگر نوشتید save یا SAVE یا Save، ربات متوجه شود
        text_lower = user_text.lower().strip()
        
        # 1. بررسی اینکه آیا شما دستور ذخیره داده‌اید؟
        is_save = False
        if text_lower.endswith("save") or text_lower.endswith("s-a-v-e") or text_lower.endswith("ذخیره کن"):
            is_save = True

        # 2. سیستم طبقه‌بندی دیتابیس (جدا کردن فکت از چت معمولی)
        if is_save:
            # ذخیره پیام به عنوان "اطلاعات دائمی و ابدی" (fact)
            supabase.table("chat_memory").insert({"user_id": user_id, "role": "fact", "content": user_text}).execute()
        else:
            # ذخیره پیام به عنوان "چت روزمره" (user)
            supabase.table("chat_memory").insert({"user_id": user_id, "role": "user", "content": user_text}).execute()

        # 3. استخراج اطلاعات از دیتابیس به صورت هوشمند و بهینه
        
        # الف) خواندن تمام اطلاعات دائمی (facts) شما
        facts_res = supabase.table("chat_memory").select("content").eq("user_id", user_id).eq("role", "fact").execute()
        saved_facts = [row["content"] for row in facts_res.data]
        
        # ب) خواندن تاریخچه چت عادی (فقط 10 تای آخر را می‌خواند تا حجم و اینترنت اشغال نشود)
        history_res = supabase.table("chat_memory").select("*").eq("user_id", user_id).neq("role", "fact").order("created_at", desc=True).limit(10).execute()
        chat_history = history_res.data[::-1] # برعکس کردن برای ترتیب زمانی درست

        # 4. آماده‌سازی مغز ربات (تزریق اطلاعات به هسته مرکزی جمنای)
        sys_instruct = f"تو یک دستیار شخصی باهوش هستی. نام کاربری که با تو صحبت می‌کند {user_name} است. به زبان فارسی صمیمی پاسخ بده.\n"
        
        if saved_facts:
            facts_text = "\n- ".join(saved_facts)
            sys_instruct += f"\n⚠️ تو این اطلاعات مهم را به صورت دائمی از این کاربر در حافظه‌ات ذخیره کرده‌ای و باید همیشه طبق آن‌ها رفتار کنی:\n- {facts_text}"

        config = types.GenerateContentConfig(system_instruction=sys_instruct)

        # 5. ساختن مکالمه‌ای که برای گوگل ارسال می‌شود
        contents = []
        for row in chat_history:
            contents.append(types.Content(role=row["role"], parts=[types.Part.from_text(text=row["content"])]))
            
        # اگر پیام شما دارای کلمه Save بود، آن را موقتاً به این مکالمه اضافه می‌کنیم تا ربات بتواند به شما بگوید "چشم، ذخیره کردم!"
        if is_save:
            contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_text)]))

        # 6. دریافت پاسخ از هوش مصنوعی
        gemini_response = genai_client.models.generate_content(model='gemini-2.5-flash', contents=contents, config=config)
        bot_reply = gemini_response.text

        # 7. فقط اگر چت روزمره بود، جواب ربات را ذخیره کن (تا دیتابیس با پیام‌های اضافی مثل "چشم ذخیره کردم" شلوغ نشود)
        if not is_save:
            supabase.table("chat_memory").insert({"user_id": user_id, "role": "model", "content": bot_reply}).execute()

        # ارسال پاسخ نهایی به شما
        bot.reply_to(message, bot_reply)

    except Exception as e:
        bot.reply_to(message, f"❌ خطایی رخ داد: {e}")
