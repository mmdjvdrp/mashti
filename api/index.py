import os
import re
import datetime
import random
import json
import pytz
import telebot
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from supabase import create_client, Client
from google import genai
from google.genai import types

# اضافه کردن ماژول جدید تقویم (مطمئن شو فایل bot_planner_api.py کنار این فایل باشه)
import bot_planner_api

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

# منطقه زمانی ایران
IRAN_TZ = pytz.timezone('Asia/Tehran')

# ==========================================
# 🛠️ توابع کمکی
# ==========================================
def db_run(query):
    try:
        return query.execute()
    except Exception as e:
        err = str(e).lower()
        if "eof" in err or "disconnected" in err or "ssl" in err or "protocol" in err:
            return query.execute()
        raise e

def fa_to_en_digits(text):
    persian_digits = "۰۱۲۳۴۵۶۷۸۹"
    arabic_digits = "٠١٢٣٤٥٦٧٨٩"
    english_digits = "0123456789"
    translation_table = str.maketrans(persian_digits + arabic_digits, english_digits + english_digits)
    return text.translate(translation_table)

# ==========================================
# 🎤 پردازش فایل‌های صوتی و ویدیویی
# ==========================================
@bot.message_handler(content_types=['voice', 'audio', 'video_note'])
def handle_voice(message):
    user_id = message.from_user.id
    status_msg = bot.reply_to(message, "🎤 در حال گوش دادن و تحلیل صدای شما... ⏳")

    try:
        mime_type = "audio/ogg"
        if message.content_type == 'voice':
            file_id = message.voice.file_id
        elif message.content_type == 'audio':
            file_id = message.audio.file_id
            mime_type = "audio/mpeg"
        elif message.content_type == 'video_note':
            file_id = message.video_note.file_id
            mime_type = "video/mp4"

        file_info = bot.get_file(file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        current_time_iran = datetime.datetime.now(IRAN_TZ).strftime("%Y-%m-%d %H:%M:%S")

        # گرفتن اطلاعات تقویم برای پیام صوتی
        supabase_user_id, planner_data = bot_planner_api.get_user_planner_data(supabase, user_id)
        planner_context = bot_planner_api.generate_planner_prompt_context(planner_data)

        system_prompt = f"""تو یک دستیار هوشمند هستی. زمان فعلی ایران: {current_time_iran}
{f"توجه: کاربر به سیستم تقویم سایت متصل است.\n{planner_context}" if planner_data else ""}

خروجی تو باید دقیقاً یک JSON معتبر باشد (بدون تگ مارک‌داون ```).
ساختار الزامی:
{{
  "is_reminder": false, 
  "minutes": 0,
  "message": "",
  "action": null,
  "action_id": null,
  "response": "پاسخ تو به کاربر"
}}

- اگر کاربر درخواست یادآوری داشت: is_reminder را true کن، زمان را در minutes بنویس و موضوع را در message.
- اگر کاربر خواست تسکی را تیک بزند: action را "tick_todo" بگذار و action_id را از لیست بالا پیدا کن.
- پاسخ صوتی تو همیشه در فیلد response قرار می‌گیرد.
"""
        
        audio_part = types.Part.from_bytes(data=downloaded_file, mime_type=mime_type)
        text_part = types.Part.from_text(text=system_prompt)
        contents = [types.Content(role="user", parts=[audio_part, text_part])]
        
        config = types.GenerateContentConfig(
            tools=[{"google_search": {}}],
            response_mime_type="application/json"
        )
        
        bot_reply_text = None
        random.shuffle(genai_clients) 
        for client in genai_clients:
            try:
                response = client.models.generate_content(model='gemini-2.5-flash', contents=contents, config=config)
                bot_reply_text = response.text
                break
            except Exception:
                continue

        if not bot_reply_text:
            bot.edit_message_text("❌ ارتباط با سرور هوش مصنوعی برقرار نشد.", chat_id=user_id, message_id=status_msg.message_id)
            return

        try:
            result = json.loads(bot_reply_text.strip())
            ai_response = result.get("response", "انجام شد.")

            # اعمال دستورات تقویم (تیک زدن تسک از طریق ویس)
            action = result.get("action")
            action_id = result.get("action_id")
            if action and action_id and planner_data:
                bot_planner_api.process_planner_action(supabase, supabase_user_id, planner_data, action, action_id)
            
            # ثبت یادآور صوتی
            if result.get("is_reminder"):
                minutes = max(int(result.get("minutes", 0)), 0)
                reminder_text = result.get("message", "یادآوری")
                send_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)
                db_run(supabase.table("scheduled_reminders").insert({
                    "user_id": user_id, "message_text": reminder_text, "send_at": send_at.isoformat(), "is_sent": False
                }))

            bot.edit_message_text(ai_response, chat_id=user_id, message_id=status_msg.message_id)
                
        except json.JSONDecodeError:
            bot.edit_message_text("❌ خطا در تحلیل خروجی هوش مصنوعی.", chat_id=user_id, message_id=status_msg.message_id)

    except Exception as e:
        bot.edit_message_text(f"❌ خطای سیستم: {str(e)}", chat_id=user_id, message_id=status_msg.message_id)

# ==========================================
# 🧠 منطق پیام‌های متنی (Text)
# ==========================================
@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_id = message.from_user.id
    user_name = message.from_user.first_name or "کاربر"
    text = message.text.strip() if message.text else ""
    text_lower = text.lower()

    if not text:
        return

    try:
        bot.send_chat_action(user_id, 'typing')
    except Exception:
        pass 

    # === ۱. دستور اتصال اکانت تقویم (کاملا ایزوله شده) ===
    if text_lower.startswith("/connect"):
        try:
            parts = text.split()
            if len(parts) >= 2:
                uuid_code = parts[1].strip()
                success, msg = bot_planner_api.link_account(supabase, user_id, uuid_code)
                bot.reply_to(message, msg)
            else:
                bot.reply_to(message, "❌ لطفاً کد اتصال را همراه با دستور وارد کنید.\nمثال: `/connect 12345678-abcd`", parse_mode="Markdown")
        except Exception as e:
            bot.reply_to(message, f"❌ خطایی در سیستم اتصال رخ داد: {str(e)}")
        return # <--- این ریتورن باعث میشه هوش مصنوعی درگیر این پیام نشه!

    try:
        # 2. بخش گاوصندوق امنیتی
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

        # 3. سیستم یادآور متنی
        text_normalized = fa_to_en_digits(text)
        pattern = r"^(?:/remind\s+(\d+)\s+(.+)|(\d+)\s*دقیقه\s*(?:دیگه|بعد)\s*(?:بهم\s*)?(?:بگو|پیام\s*بده)\s*(.+))$"
        match = re.search(pattern, text_normalized, re.IGNORECASE)

        if match:
            if match.group(1):  
                minutes, reminder_text = int(match.group(1)), match.group(2)
            else:  
                minutes, reminder_text = int(match.group(3)), match.group(4)
            send_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)
            db_run(supabase.table("scheduled_reminders").insert({
                "user_id": user_id, "message_text": reminder_text, "send_at": send_at.isoformat(), "is_sent": False
            }))
            bot.reply_to(message, f"⏰ یادآور ثبت شد! {minutes} دقیقه دیگر به شما پیام می‌دهم:\n\n«{reminder_text}»")
            return

        # 4. بخش چت، حافظه و تقویم با هوش مصنوعی
        is_save = text_lower.endswith("save") or text_lower.endswith("ذخیره کن")
        if is_save:
            db_run(supabase.table("chat_memory").insert({"user_id": user_id, "role": "fact", "content": text}))
        else:
            db_run(supabase.table("chat_memory").insert({"user_id": user_id, "role": "user", "content": text}))

        facts_res = db_run(supabase.table("chat_memory").select("content").eq("user_id", user_id).eq("role", "fact"))
        saved_facts = [row["content"] for row in facts_res.data]
        history_res = db_run(supabase.table("chat_memory").select("*").eq("user_id", user_id).neq("role", "fact").order("created_at", desc=True).limit(10))
        chat_history = history_res.data[::-1]

        # === خواندن اطلاعات تقویم کاربر ===
        supabase_user_id, planner_data = bot_planner_api.get_user_planner_data(supabase, user_id)
        planner_context = bot_planner_api.generate_planner_prompt_context(planner_data)

        current_time_iran = datetime.datetime.now(IRAN_TZ).strftime("%Y-%m-%d %H:%M:%S")

        sys_instruct = f"""تو یک دستیار هوشمند هستی. نام کاربر تو {user_name} است.
زمان فعلی در ایران: {current_time_iran}
{f"توجه: کاربر به سیستم تقویم سایت متصل است.\n{planner_context}" if planner_data else ""}

توجه بسیار مهم: خروجی تو باید دقیقاً و فقط یک آبجکت JSON معتبر باشد.
ساختار الزامی:
{{
  "action": null,
  "action_id": null,
  "response": "پاسخ کامل تو به کاربر"
}}

- اگر کاربر گفت کاری را انجام داده (مثلاً: "خرید رو تیک بزن")، شناسه آن تسک را از لیست پیدا کن، action را روی "tick_todo" قرار بده و شناسه را در action_id بگذار.
- در غیر این صورت فیلدهای action و action_id را null بگذار.
- جواب و صحبت‌هایت با کاربر را همیشه در فیلد response بنویس.
"""
        if saved_facts:
            sys_instruct += f"\n\n⚠️ اطلاعات دائم کاربر:\n- " + "\n- ".join(saved_facts)

        # 🟢 اجبار مدل به تولید فقط JSON
        config = types.GenerateContentConfig(
            system_instruction=sys_instruct, 
            tools=[{"google_search": {}}],
            response_mime_type="application/json"
        )

        contents = []
        for row in chat_history:
            contents.append(types.Content(role=row["role"], parts=[types.Part.from_text(text=row["content"])]))
        if is_save:
            contents.append(types.Content(role="user", parts=[types.Part.from_text(text=text)]))

        bot_reply = None
        random.shuffle(genai_clients) 
        for client in genai_clients:
            try:
                response = client.models.generate_content(model='gemini-2.5-flash', contents=contents, config=config)
                bot_reply = response.text
                break
            except Exception:
                continue
                    
        if not bot_reply:
            bot.reply_to(message, "❌ سیستم هوش مصنوعی پاسخگو نبود.")
            return

        try:
            result = json.loads(bot_reply.strip())
            
            # هندل کردن اکشن‌های تقویم
            action = result.get("action")
            action_id = result.get("action_id")
            if action and action_id and planner_data:
                bot_planner_api.process_planner_action(supabase, supabase_user_id, planner_data, action, action_id)
            
            ai_response = result.get("response", "پاسخی تولید نشد.")
            
            if not is_save:
                db_run(supabase.table("chat_memory").insert({"user_id": user_id, "role": "model", "content": ai_response}))
                
            bot.reply_to(message, ai_response)

        except json.JSONDecodeError:
            bot.reply_to(message, "❌ خطا در پردازش پاسخ هوش مصنوعی.")

    except Exception as e:
        bot.reply_to(message, f"❌ خطای سیستم: {str(e)}")

# ==========================================
# 🌐 تنظیمات Webhook و Cron
# ==========================================
@app.route('/', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        
        try:
            supabase.table("processed_updates").insert({"update_id": update.update_id}).execute()
        except Exception as e:
            err_msg = str(e)
            if "duplicate" in err_msg or "23505" in err_msg:
                return jsonify({"status": "already processed"}), 200
        
        bot.process_new_updates([update])
        return jsonify({"status": "ok"}), 200
        
    return jsonify({"status": "error"}), 403

@app.route('/cron/send-reminders', methods=['GET', 'POST'])
def send_reminders():
    now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        res = db_run(supabase.table("scheduled_reminders").select("*").eq("is_sent", False).lte("send_at", now_utc))
        sent_count = 0
        for row in res.data:
            try:
                bot.send_message(row["user_id"], row["message_text"])
                db_run(supabase.table("scheduled_reminders").update({"is_sent": True}).eq("id", row["id"]))
                sent_count += 1
            except Exception:
                pass
        return jsonify({"status": "success", "processed": sent_count}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/', methods=['GET'])
def index():
    return "✅ ربات مجهز به ساعت زنده ایران، سرچ گوگل، یادآور هوشمند و اتصال تقویم است!"
