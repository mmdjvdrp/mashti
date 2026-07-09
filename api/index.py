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
from openai import OpenAI  # 🟢 تغییر به کتابخانه استاندارد OpenAI

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# 🟢 گرفتن کلیدهای سایت Conduit از متغیر Vercel
# (همان کلیدهایی که با sk-cdt شروع می‌شوند را با ویرگول در GEMINI_API_KEY ورسل قرار بده)
keys_string = os.environ.get("GEMINI_API_KEY", "")
CONDUIT_KEYS = [k.strip() for k in keys_string.split(",") if k.strip()]

# 🟢 آدرس دقیق API سایت Conduit
AI_BASE_URL = "https://conduit.ozdoev.net/api/v1"

# 🟢 مدلی که می‌خواهیم استفاده کنیم
AI_MODEL = "gemini-2.5-flash" 

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, threaded=False)
app = Flask(__name__)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

IRAN_TZ = pytz.timezone('Asia/Tehran')

# ==========================================
# 🗓️ توابع اتصال به تقویم
# ==========================================
def link_account(telegram_id, user_uuid):
    try:
        res = supabase.table("planner_data").select("user_id, data").eq("user_id", user_uuid).execute()
        if not res.data:
            return False, "❌ حساب کاربری وب پیدا نشد! مطمئن شوید که کد را درست کپی کرده‌اید."
        
        data = res.data[0]['data']
        data['telegram_id'] = str(telegram_id)
        supabase.table("planner_data").update({"data": data}).eq("user_id", user_uuid).execute()
        return True, "✅ حساب وب شما با موفقیت به ربات تلگرام متصل شد!"
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
        is_done = t.get("doneDates", {}).get(today, False) if t.get("isDaily") else t.get("done", False)
        status = "انجام شده" if is_done else "در انتظار انجام"
        tasks_text += f"- ID: {t['id']} | عنوان: {t['title']} | وضعیت: {status}\n"
    return f"لیست کارهای امروز کاربر:\n{tasks_text}"

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
        todos.append({
            "id": "t" + str(int(time.time() * 1000)), "title": action_text,
            "date": today, "done": False, "isDaily": False, "doneDates": {}
        })
        planner_data["todos"] = todos
        updated = True
    
    if updated:
        supabase.table("planner_data").update({"data": planner_data}).eq("user_id", supabase_user_id).execute()
        return True
    return False

def db_run(query):
    try:
        return query.execute()
    except Exception as e:
        if any(x in str(e).lower() for x in ["eof", "disconnected", "ssl", "protocol"]):
            return query.execute()
        raise e

def fa_to_en_digits(text):
    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    return text.translate(trans)

# ==========================================
# 🎤 پردازش فایل‌های صوتی
# ==========================================
@bot.message_handler(content_types=['voice', 'audio', 'video_note'])
def handle_voice(message):
    # سایت Conduit از دریافت مستقیم فایل صوتی پشتیبانی نمی‌کند
    bot.reply_to(message, "🎤 به دلیل استفاده از سرور واسطه Conduit، فعلاً پردازش مستقیم فایل صوتی غیرفعال است. لطفاً درخواست خود را تایپ کنید! ⌨️")

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

    # دستور اتصال
    if text.lower().startswith("/connect"):
        try:
            parts = text.split()
            if len(parts) >= 2:
                success, msg = link_account(user_id, parts[1].strip())
                bot.reply_to(message, msg)
            else:
                bot.reply_to(message, "❌ کد اتصال یافت نشد.")
        except Exception as e:
            bot.reply_to(message, f"❌ خطا: {str(e)}")
        return 

    try:
        bot.send_chat_action(user_id, 'typing')
    except:
        pass 

    try:
        text_lower = text.lower()
        
        # گاوصندوق
        if text.startswith("/lock"):
            parts = text.split(" ", 3)
            db_run(supabase.table("secure_vaults").insert({"user_id": user_id, "box_name": parts[1], "password": parts[2], "content": parts[3]}))
            bot.reply_to(message, f"🔒 اطلاعات در جعبه '{parts[1]}' قفل شد.")
            return 
        if text.startswith("/unlock"):
            parts = text.split(" ", 2)
            res = db_run(supabase.table("secure_vaults").select("content").eq("user_id", user_id).eq("box_name", parts[1]).eq("password", parts[2]))
            if res.data:
                bot.reply_to(message, f"🔓 اطلاعات جعبه:\n\n- " + "\n- ".join([r["content"] for r in res.data]))
            else:
                bot.reply_to(message, "❌ جعبه پیدا نشد یا رمز اشتباه است!")
            return 

        # یادآور متنی
        match = re.search(r"^(?:/remind\s+(\d+)\s+(.+)|(\d+)\s*دقیقه\s*(?:دیگه|بعد)\s*(?:بهم\s*)?(?:بگو|پیام\s*بده)\s*(.+))$", fa_to_en_digits(text), re.IGNORECASE)
        if match:
            minutes, reminder_text = (int(match.group(1)), match.group(2)) if match.group(1) else (int(match.group(3)), match.group(4))
            send_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)
            db_run(supabase.table("scheduled_reminders").insert({"user_id": user_id, "message_text": reminder_text, "send_at": send_at.isoformat(), "is_sent": False}))
            bot.reply_to(message, f"⏰ یادآور ثبت شد! {minutes} دقیقه دیگر به شما پیام می‌دهم.")
            return

        is_save = text_lower.endswith("save") or text_lower.endswith("ذخیره کن")
        if is_save:
            db_run(supabase.table("chat_memory").insert({"user_id": user_id, "role": "fact", "content": text}))
        else:
            db_run(supabase.table("chat_memory").insert({"user_id": user_id, "role": "user", "content": text}))

        facts_res = db_run(supabase.table("chat_memory").select("content").eq("user_id", user_id).eq("role", "fact"))
        saved_facts = [r["content"] for r in facts_res.data]
        history_res = db_run(supabase.table("chat_memory").select("*").eq("user_id", user_id).neq("role", "fact").order("created_at", desc=True).limit(8))
        chat_history = history_res.data[::-1]

        supabase_user_id, planner_data = get_user_planner_data(user_id)
        planner_context = generate_planner_prompt_context(planner_data)
        current_time_iran = datetime.datetime.now(IRAN_TZ).strftime("%Y-%m-%d %H:%M:%S")

        sys_instruct = f"""تو یک دستیار هوشمند هستی. نام کاربر: {user_name}. زمان: {current_time_iran}

⚠️ تو مستقیماً به پایگاه داده تقویم متصل هستی.
{f"✅ وضعیت: کاربر متصل است.\n{planner_context}" if planner_data else "❌ وضعیت: کاربر متصل نیست."}

خروجی تو باید دقیقاً و فقط یک آبجکت JSON باشد. هیچ متنی خارج از JSON ننویس.
{{
  "action": null,
  "action_id": null,
  "action_text": null,
  "response": "پاسخ دوستانه تو به کاربر"
}}

- تیک زدن تسک: action="tick_todo" و action_id=شناسه تسک.
- اضافه کردن تسک: action="add_todo" و action_text=عنوان کار.
"""
        if saved_facts:
            sys_instruct += f"\n\n⚠️ قوانین کاربر:\n- " + "\n- ".join(saved_facts)

        # 🟢 ساختار پیام‌ها با استاندارد OpenAI / Conduit
        messages = [{"role": "system", "content": sys_instruct}]
        for row in chat_history:
            role = "user" if row["role"] == "user" else "assistant"
            messages.append({"role": role, "content": row["content"]})
        if is_save:
            messages.append({"role": "user", "content": text})

        bot_reply = None
        last_error = "کلید API یافت نشد."
        
        # 🟢 سیستم چرخشی هوشمند برای کلیدهای سایت Conduit
        random.shuffle(CONDUIT_KEYS)
        for api_key in CONDUIT_KEYS:
            if not api_key: continue
            try:
                ai_client = OpenAI(api_key=api_key, base_url=AI_BASE_URL)
                response = ai_client.chat.completions.create(
                    model=AI_MODEL,
                    messages=messages,
                )
                bot_reply = response.choices[0].message.content
                break
            except Exception as e:
                last_error = str(e)
                continue

        if not bot_reply:
            bot.reply_to(message, f"❌ خطا از سمت سرور Conduit:\n`{last_error}`\nمطمئن شوید کلیدهای این سایت را در متغیر Vercel قرار داده‌اید.", parse_mode="Markdown")
            return

        try:
            clean_text = bot_reply.strip()
            match = re.search(r'\{[\s\S]*\}', clean_text)
            if match: clean_text = match.group(0)
            result = json.loads(clean_text)
            
            action = result.get("action")
            if action and planner_data:
                process_planner_action(supabase_user_id, planner_data, action, result.get("action_id"), result.get("action_text"))
            
            ai_response = result.get("response", "انجام شد.")
            if not is_save:
                db_run(supabase.table("chat_memory").insert({"user_id": user_id, "role": "model", "content": ai_response}))
            bot.reply_to(message, ai_response)

        except json.JSONDecodeError:
            bot.reply_to(message, "❌ خطا در پردازش پاسخ سایت Conduit. (احتمالاً فرمت خروجی به هم ریخته است)")

    except Exception as e:
        bot.reply_to(message, f"❌ خطای سیستم: {str(e)}")

# ==========================================
# 🌐 سیستم مسیردهی برای Vercel
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
                except:
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
            except:
                return jsonify({"status": "already processed"}), 200
            bot.process_new_updates([update])
            return jsonify({"status": "ok"}), 200
        return jsonify({"status": "error"}), 403

    return "✅ سرور پایتون متصل به سایت Conduit نصب شد!"
