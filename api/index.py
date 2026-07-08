import os
import telebot
import random
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from supabase import create_client, Client
from google import genai
from google.genai import types

# ==========================================
# ⚙️ خواندن متغیرهای محیطی
# ==========================================
load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
keys_string = os.environ.get("GEMINI_API_KEY", "")
GEMINI_KEYS = [k.strip() for k in keys_string.split(",") if k.strip()]

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, threaded=False)
app = Flask(__name__)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
genai_clients = [genai.Client(api_key=key) for key in GEMINI_KEYS]

# ==========================================
# 🛠️ سیستم خودترمیم‌شونده برای سوپابیس
# ==========================================
def db_run(query):
    """اگر کانکشن دیتابیس خواب رفته بود، یک بار خطا رو نادیده می‌گیره و با کانکشن جدید دوباره تلاش می‌کنه"""
    try:
        return query.execute()
    except Exception as e:
        err = str(e).lower()
        if "eof" in err or "disconnected" in err or "ssl" in err or "protocol" in err:
            return query.execute() # تلاش مجدد با سوکتِ تازه
        raise e

# ==========================================
# 🧠 منطق اصلی ربات
# ==========================================
@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_id = message.from_user.id
    user_name = message.from_user.first_name or "کاربر"
    text = message.text.strip() if message.text else ""
    text_lower = text.lower()

    if not text:
        return

    # حل مشکل قطعی سوکت تلگرام: اگر ارور داد نادیده می‌گیریم تا کانکشن تلگرام ریست بشه
    try:
        bot.send_chat_action(user_id, 'typing')
    except Exception:
        pass 

    try:
        # 1. بخش گاوصندوق امنیتی
        if text.startswith("/lock"):
            try:
                parts = text.split(" ", 3)
                db_run(supabase.table("secure_vaults").insert({
                    "user_id": user_id, "box_name": parts[1], "password": parts[2], "content": parts[3]
                }))
                bot.reply_to(message, f"🔒 اطلاعات در جعبه '{parts[1]}' قفل شد.")
            except Exception:
                bot.reply_to(message, "❌ فرمت اشتباه است: /lock [اسم] [رمز] [متن]")
            return 

        if text.startswith("/unlock"):
            try:
                parts = text.split(" ", 2)
                res = db_run(supabase.table("secure_vaults").select("content").eq("user_id", user_id).eq("box_name", parts[1]).eq("password", parts[2]))
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
            db_run(supabase.table("chat_memory").insert({"user_id": user_id, "role": "fact", "content": text}))
        else:
            db_run(supabase.table("chat_memory").insert({"user_id": user_id, "role": "user", "content": text}))

        facts_res = db_run(supabase.table("chat_memory").select("content").eq("user_id", user_id).eq("role", "fact"))
        saved_facts = [row["content"] for row in facts_res.data]
        
        history_res = db_run(supabase.table("chat_memory").select("*").eq("user_id", user_id).neq("role", "fact").order("created_at", desc=True).limit(10))
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

        # 3. ارتباط با جمنای (با سیستم خودترمیم‌شونده قطعی سرور)
        bot_reply = None
        random.shuffle(genai_clients) 
        
        for client in genai_clients:
            try:
                response = client.models.generate_content(model='gemini-2.5-flash', contents=contents, config=config)
                bot_reply = response.text
                break
            except Exception as e:
                err_str = str(e).lower()
                # اگر گوگل کانکشن رو بسته بود، یک بار دیگه سریع با همون کلید تلاش کن
                if "eof" in err_str or "disconnected" in err_str or "ssl" in err_str:
                    try:
                        response = client.models.generate_content(model='gemini-2.5-flash', contents=contents, config=config)
                        bot_reply = response.text
                        break
                    except:
                        continue # اگر بازم نشد برو کلید بعدی
                elif "429" in err_str or "exhausted" in err_str:
                    continue # لیمیت شده، برو کلید بعدی
                else:
                    raise e
                    
        if not bot_reply:
            bot_reply = "❌ تمام کلیدهای هوش مصنوعی مسدود شده‌اند یا گوگل در دسترس نیست. لطفاً کمی بعد تلاش کنید."

        if not is_save and bot_reply and not bot_reply.startswith("❌"):
            db_run(supabase.table("chat_memory").insert({"user_id": user_id, "role": "model", "content": bot_reply}))

        bot.reply_to(message, bot_reply)

    except Exception as e:
        bot.reply_to(message, f"❌ خطای سیستم: {str(e)}")

# ==========================================
# 🌐 تنظیمات Webhook برای Vercel
# ==========================================

@app.route('/', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        
        # 🟢 قفل کردن همزمانی با دیتابیس Supabase 🟢
        try:
            # تلاش برای ثبت آیدی پیام در دیتابیس مشترک
            supabase.table("processed_updates").insert({"update_id": update.update_id}).execute()
        except Exception as e:
            err_msg = str(e)
            # اگر آیدی قبلاً ثبت شده بود (ارور duplicate)، یعنی پیام تکراریه و بلافاصله متوقفش کن
            if "duplicate" in err_msg or "23505" in err_msg:
                return jsonify({"status": "already processed"}), 200
            
            # اگر خطای دیگه‌ای بود (مثلاً جدول هنوز ساخته نشده)، اجازه بده ربات کارش رو بکنه تا قطع نشه
            print(f"Deduplication bypass: {err_msg}")
        # --------------------------------------------
        
        bot.process_new_updates([update])
        return jsonify({"status": "ok"}), 200
        
    return jsonify({"status": "error"}), 403

@app.route('/', methods=['GET'])
def index():
    return "✅ ربات بدون مشکل، با سیستم خودترمیم‌شونده و قفل دیتابیس روی Vercel فعال است!"
