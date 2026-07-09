import os
import re
import time
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

IRAN_TZ = pytz.timezone('Asia/Tehran')

# ==========================================
# 🗓️ توابع اتصال به تقویم و مدیریت Taskها
# ==========================================
def link_account(telegram_id, user_uuid):
    try:
        res = supabase.table("planner_data").select("user_id, data").eq("user_id", user_uuid).execute()
        if not res.data:
            return False, "❌ حساب کاربری وب پیدا نشد! مطمئن شوید که کد را درست کپی کرده‌اید."
        
        data = res.data[0]['data']
        data['telegram_id'] = str(telegram_id)
        supabase.table("planner_data").update({"data": data}).eq("user_id", user_uuid).execute()
        return True, "✅ حساب وب شما با موفقیت به ربات تلگرام متصل شد! \n\nحالا می‌توانید بگویید: «امروز چه کارهایی دارم؟» یا «تسک ورزش رو تیک بزن»."
    except Exception as e:
        return False, f"❌ خطا در ارتباط با دیتابیس: {str(e)}"

def get_user_planner_data(telegram_id):
    try:
        res = supabase.table("planner_data").select("user_id, data").eq("data->>telegram_id", str(telegram_id)).execute()
        if res.data:
            return res.data[0]['user_id'], res.data[0]['data']
    except Exception:
        pass
    return None, None

def generate_planner_prompt_context(planner_data):
    if not planner_data:
        return ""
    
    today = datetime.datetime.now(IRAN_TZ).strftime("%Y-%m-%d")
    todos = planner_data.get("todos", [])
    today_todos = [t for t in todos if t.get("date") == today or t.get("isDaily")]
    
    if not today_todos:
        return "کاربر هیچ کار (To-Do) ثبت شده‌ای برای امروز ندارد."

    tasks_text = ""
    for t in today_todos:
        if t.get("isDaily"):
            done_dates = t.get("doneDates", {})
            is_done = done_dates.get(today, False)
        else:
            is_done = t.get("done", False)
            
        status = "انجام شده" if is_done else "در انتظار انجام"
        tasks_text += f"- ID: {t['id']} | عنوان: {t['title']} | وضعیت: {status}\n"
        
    return f"لیست کارهای امروز کاربر (از سایت تقویم):\n{tasks_text}"

def process_planner_action(supabase_user_id, planner_data, action, action_id, action_text):
    today = datetime.datetime.now(IRAN_TZ).strftime("%Y-%m-%d")
    todos = planner_data.get("todos", [])
    updated = False
    
    if action == "tick_todo" and action_id:
        for t in todos:
            if t['id'] == action_id:
                if t.get("isDaily"):
                    if "doneDates" not in t:
                        t["doneDates"] = {}
                    t["doneDates"][today] = True
                else:
                    t["done"] = True
                updated = True
                break
                
    elif action == "add_todo" and action_text:
        new_todo = {
            "id": "t" + str(int(time.time() * 1000)),
            "title": action_text,
            "date": today,
            "done": False,
            "isDaily": False,
            "doneDates": {}
        }
        todos.append(new_todo)
        planner_data["todos"] = todos
        updated = True
    
    if updated:
        supabase.table("planner_data").update({"data": planner_data}).eq("user_id", supabase_user_id).execute()
        return True
    return False

# ==========================================
# 🛠️ توابع کمکی ربات
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
# 🎤 پردازش فایل‌های صوتی
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

        supabase_user_id, planner_data = get_user_planner_data(user_id)
        planner_context = generate_planner_prompt_context(planner_data)

        system_prompt = f"""تو یک دستیار هوشمند هستی. زمان فعلی ایران: {current_time_iran}
{f"توجه: کاربر به سیستم تقویم سایت متصل است.\n{planner_context}" if planner_data else ""}

توجه بسیار مهم: خروجی تو باید دقیقاً و فقط یک آبجکت JSON معتبر باشد و هیچ متن اضافه‌ای قبل یا بعد از آن ننویس.
ساختار الزامی:
{{
  "is_reminder": false, 
  "minutes": 0,
  "message": "",
  "action": null,
  "action_id": null,
  "action_text": null,
  "response": "پاسخ تو به کاربر"
}}

- اگر کاربر خواست تسکی را تیک بزند: action را "tick_todo" بگذار و action_id را از لیست پیدا کن.
- اگر خواست کار جدیدی اضافه کند: action را "add_todo" بگذار و عنوان کار را در action_text بنویس.
- پاسخ صوتی تو همیشه در فیلد response قرار می‌گیرد.
"""
        
        audio_part = types.Part.from_bytes(data=downloaded_file, mime_type=mime_type)
        text_part = types.Part.from_text(text=system_prompt)
        contents = [types.Content(role="user", parts=[audio_part, text_part])]
        
        # 🟢 حذف response_mime_type برای جلوگیری از ارور 400 گوگل
        config = types.GenerateContentConfig(tools=[{"google_search": {}}])
        
        bot_reply_text = None
        last_error = ""
        random.shuffle(genai_clients) 
        for client in genai_clients:
            try:
                response = client.models.generate_content(model='gemini-2.5-flash', contents=contents, config=config)
                bot_reply_text = response.text
                break
            except Exception as e:
                last_error = str(e)
                continue

        if not bot_reply_text:
            bot.edit_message_text(f"❌ ارتباط با سرور هوش مصنوعی برقرار نشد.\n\nجزئیات خطا:\n`{last_error}`", chat_id=user_id, message_id=status_msg.message_id, parse_mode="Markdown")
            return

        try:
            # 🟢 سیستم هوشمند استخراج JSON در صورت وجود مارک‌داون‌های اضافی
            clean_text = bot_reply_text.strip()
            match = re.search(r'\{[\s\S]*\}', clean_text)
            if match:
                clean_text = match.group(0)
                
            result = json.loads(clean_text)
            ai_response = result.get("response", "انجام شد.")

            action = result.get("action")
            action_id = result.get("action_id")
            action_text = result.get("action_text")
            
            if action and planner_data:
                process_planner_action(supabase_user_id, planner_data, action, action_id, action_text)
            
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
# 🧠 پردازش پیام‌های متنی
# ==========================================
@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_id = message.from_user.id
    user_name = message.from_user.first_name or "کاربر"
    text = message.text.strip() if message.text else ""

    if not text:
        return

    if text.lower().startswith("/connect"):
        try:
            parts = text.split()
            if len(parts) >= 2:
                uuid_code = parts[1].strip()
                success, msg = link_account(user_id, uuid_code)
                bot.reply_to(message, msg)
            else:
                bot.reply_to(message, "❌ کد اتصال یافت نشد. لطفاً کد را از سایت کپی کنید.")
        except Exception as e:
            bot.reply_to(message, f"❌ خطا: {str(e)}")
        return 

    try:
        bot.send_chat_action(user_id, 'typing')
    except:
        pass 

    try:
        text_lower = text.lower()
        if text.startswith("/lock"):
            try:
                parts = text.split(" ", 3)
                db_run(supabase.table("secure_vaults").insert({"user_id": user_id, "box_name": parts[1], "password": parts[2], "content": parts[3]}))
                bot.reply_to(message, f"🔒 اطلاعات در جعبه '{parts[1]}' قفل شد.")
            except:
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
            except:
                bot.reply_to(message, "❌ فرمت اشتباه است: /unlock [اسم] [رمز]")
            return 

        text_normalized = fa_to_en_digits(text)
        pattern = r"^(?:/remind\s+(\d+)\s+(.+)|(\d+)\s*دقیقه\s*(?:دیگه|بعد)\s*(?:بهم\s*)?(?:بگو|پیام\s*بده)\s*(.+))$"
        match = re.search(pattern, text_normalized, re.IGNORECASE)

        if match:
            if match.group(1):  
                minutes, reminder_text = int(match.group(1)), match.group(2)
            else:  
                minutes, reminder_text = int(match.group(3)), match.group(4)
            send_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)
            db_run(supabase.table("scheduled_reminders").insert({"user_id": user_id, "message_text": reminder_text, "send_at": send_at.isoformat(), "is_sent": False}))
            bot.reply_to(message, f"⏰ یادآور ثبت شد! {minutes} دقیقه دیگر به شما پیام می‌دهم:\n\n«{reminder_text}»")
            return

        is_save = text_lower.endswith("save") or text_lower.endswith("ذخیره کن")
        if is_save:
            db_run(supabase.table("chat_memory").insert({"user_id": user_id, "role": "fact", "content": text}))
        else:
            db_run(supabase.table("chat_memory").insert({"user_id": user_id, "role": "user", "content": text}))

        facts_res = db_run(supabase.table("chat_memory").select("content").eq("user_id", user_id).eq("role", "fact"))
        saved_facts = [row["content"] for row in facts_res.data]
        history_res = db_run(supabase.table("chat_memory").select("*").eq("user_id", user_id).neq("role", "fact").order("created_at", desc=True).limit(10))
        chat_history = history_res.data[::-1]

        supabase_user_id, planner_data = get_user_planner_data(user_id)
        planner_context = generate_planner_prompt_context(planner_data)
        current_time_iran = datetime.datetime.now(IRAN_TZ).strftime("%Y-%m-%d %H:%M:%S")

        sys_instruct = f"""تو یک دستیار هوشمند هستی. نام کاربر تو {user_name} است.
زمان فعلی: {current_time_iran}
{f"توجه: کاربر به سیستم تقویم سایت متصل است.\n{planner_context}" if planner_data else ""}

توجه بسیار مهم: خروجی تو باید دقیقاً و فقط یک آبجکت JSON معتبر باشد و هیچ متن اضافه‌ای ننویس.
ساختار الزامی:
{{
  "action": null,
  "action_id": null,
  "action_text": null,
  "response": "پاسخ کامل تو به کاربر"
}}

- اگر کاربر گفت کاری را انجام داده، action را "tick_todo" قرار بده و شناسه آن کار را از لیست بالا در action_id بگذار.
- اگر خواست کار جدیدی اضافه کند: action را "add_todo" قرار بده و عنوان کار را در action_text بنویس.
- جواب و صحبت‌هایت با کاربر را همیشه در فیلد response بنویس.
"""
        if saved_facts:
            sys_instruct += f"\n\n⚠️ اطلاعات دائم کاربر:\n- " + "\n- ".join(saved_facts)

        # 🟢 حذف response_mime_type برای جلوگیری از ارور 400 گوگل
        config = types.GenerateContentConfig(
            system_instruction=sys_instruct, 
            tools=[{"google_search": {}}]
        )

        contents = []
        for row in chat_history:
            contents.append(types.Content(role=row["role"], parts=[types.Part.from_text(text=row["content"])]))
        if is_save:
            contents.append(types.Content(role="user", parts=[types.Part.from_text(text=text)]))

        bot_reply = None
        last_error = ""
        random.shuffle(genai_clients) 
        for client in genai_clients:
            try:
                response = client.models.generate_content(model='gemini-2.5-flash', contents=contents, config=config)
                bot_reply = response.text
                break
            except Exception as e:
                last_error = str(e)
                continue
                    
        if not bot_reply:
            bot.reply_to(message, f"❌ سیستم هوش مصنوعی پاسخگو نبود.\n\nجزئیات خطا:\n`{last_error}`", parse_mode="Markdown")
            return

        try:
            # 🟢 سیستم هوشمند استخراج JSON
            clean_text = bot_reply.strip()
            match = re.search(r'\{[\s\S]*\}', clean_text)
            if match:
                clean_text = match.group(0)
                
            result = json.loads(clean_text)
            
            action = result.get("action")
            action_id = result.get("action_id")
            action_text = result.get("action_text")
            
            if action and planner_data:
                process_planner_action(supabase_user_id, planner_data, action, action_id, action_text)
            
            ai_response = result.get("response", "پاسخی تولید نشد.")
            
            if not is_save:
                db_run(supabase.table("chat_memory").insert({"user_id": user_id, "role": "model", "content": ai_response}))
                
            bot.reply_to(message, ai_response)

        except json.JSONDecodeError:
            bot.reply_to(message, "❌ خطا در پردازش پاسخ هوش مصنوعی.")

    except Exception as e:
        bot.reply_to(message, f"❌ خطای سیستم: {str(e)}")

# ==========================================
# 🌐 سیستم مسیردهی پیشرفته برای Vercel
# ==========================================
@app.route('/', defaults={'path': ''}, methods=['GET', 'POST'])
@app.route('/<path:path>', methods=['GET', 'POST'])
def catch_all(path):
    if 'cron' in path or 'send-reminders' in path:
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

    if request.method == 'POST':
        if request.headers.get('content-type') == 'application/json':
            json_string = request.get_data().decode('utf-8')
            update = telebot.types.Update.de_json(json_string)
            try:
                supabase.table("processed_updates").insert({"update_id": update.update_id}).execute()
            except Exception as e:
                if "duplicate" in str(e) or "23505" in str(e):
                    return jsonify({"status": "already processed"}), 200
            bot.process_new_updates([update])
            return jsonify({"status": "ok"}), 200
        return jsonify({"status": "error"}), 403

    return "✅ سرور پایتون نصب شد و ربات در حال کار است!"
