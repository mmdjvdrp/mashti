import os
import re
import datetime
import random
import json
import pytz  # 👈 اضافه شد برای مدیریت ساعت دقیق ایران
import telebot
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

# منطقه زمانی ایران
IRAN_TZ = pytz.timezone('Asia/Tehran')

# ==========================================
# 🛠️ توابع کمکی و خودترمیم‌شونده
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
# 🎤 پردازش فایل‌های صوتی و ویدیویی (متصل به سرچ و زمان)
# ==========================================
@bot.message_handler(content_types=['voice', 'audio', 'video_note'])
def handle_voice(message):
    user_id = message.from_user.id
    
    status_msg = bot.reply_to(message, "🎤 در حال گوش دادن، جستجو و تحلیل صدای شما... ⏳")

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
        
        # محاسبه زمان زنده ایران
        current_time_iran = datetime.datetime.now(IRAN_TZ).strftime("%Y-%m-%d %H:%M:%S")

        system_prompt = f"""تو یک دستیار هوشمند هستی. زمان و تاریخ فعلی در ایران (تهران): {current_time_iran}
تو به اینترنت متصل هستی. در صورت نیاز به روزترین اطلاعات را حتماً جستجو کن.

توجه بسیار مهم: خروجی تو باید دقیقاً و فقط یک آبجکت JSON معتبر باشد و هیچ متن اضافه‌ای قبل یا بعد از آن ننویس (حتی تگ‌های مارک‌داون مثل ```json را هم نگذار).

اگر کاربر درخواست یادآوری (Reminder) داشت:
- زمان باقی‌مانده تا آن ساعت را نسبت به زمان فعلیِ ایران محاسبه کن.
- عدد محاسبه شده را به دقیقه تبدیل کن و در فیلد minutes قرار بده.
- فرمت دقیق: {{"is_reminder": true, "minutes": تعداد_دقیقه, "message": "موضوع یادآوری", "response": "تاییدیه دوستانه"}}

اگر کاربر سوالی پرسید یا حرف عادی زد:
- فرمت دقیق: {{"is_reminder": false, "minutes": 0, "message": "", "response": "پاسخ کامل تو به کاربر بر اساس جستجوی وب یا دانشت"}}
"""
        
        audio_part = types.Part.from_bytes(data=downloaded_file, mime_type=mime_type)
        text_part = types.Part.from_text(text=system_prompt)
        contents = [types.Content(role="user", parts=[audio_part, text_part])]
        
        # 🟢 حل مشکل تداخل: Mime-Type اجباری حذف شد تا جستجو بتواند کار کند
        config = types.GenerateContentConfig(
            tools=[{"google_search": {}}]  # فقط ابزار سرچ فعال است
        )
        
        bot_reply_text = None
        last_error = "نامشخص"
        random.shuffle(genai_clients) 
        
        for client in genai_clients:
            try:
                response = client.models.generate_content(model='gemini-2.5-flash', contents=contents, config=config)
                bot_reply_text = response.text
                break
            except Exception as e:
                last_error = str(e) # ذخیره ارور برای نمایش به شما
                continue

        # اگر هیچ کلیدی کار نکرد، ارور دقیق به شما نمایش داده می‌شود
        if not bot_reply_text:
            bot.edit_message_text(f"❌ ارتباط با سرور گوگل برقرار نشد.\nدلیل خطا:\n`{last_error}`", 
                                  parse_mode="Markdown", chat_id=user_id, message_id=status_msg.message_id)
            return

        try:
            # 🟢 پاک‌سازی متن خروجی در صورتی که مدل مارک‌داون اضافی فرستاده باشد
            clean_text = bot_reply_text.strip()
            if "{" in clean_text and "}" in clean_text:
                # پیدا کردن محدوده JSON برای جلوگیری از خطای پارس
                match = re.search(r'\{.*\}', clean_text, re.DOTALL)
                if match:
                    clean_text = match.group(0)

            result = json.loads(clean_text)
            
            if result.get("is_reminder"):
                minutes = int(result.get("minutes", 0))
                if minutes < 0:
                    minutes = 0 
                    
                reminder_text = result.get("message", "یادآوری")
                ai_response = result.get("response", f"⏰ چشم، یادآور تنظیم شد.")
                
                send_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)

                db_run(supabase.table("scheduled_reminders").insert({
                    "user_id": user_id,
                    "message_text": reminder_text,
                    "send_at": send_at.isoformat(),
                    "is_sent": False
                }))

                bot.edit_message_text(ai_response, chat_id=user_id, message_id=status_msg.message_id)
                
            else:
                ai_response = result.get("response", "متوجه نشدم.")
                
                db_run(supabase.table("chat_memory").insert({"user_id": user_id, "role": "user", "content": "[کاربر پیام صوتی ارسال کرد]" }))
                db_run(supabase.table("chat_memory").insert({"user_id": user_id, "role": "model", "content": ai_response}))
                
                bot.edit_message_text(ai_response, chat_id=user_id, message_id=status_msg.message_id)
                
        except json.JSONDecodeError:
            bot.edit_message_text("❌ خطا در تحلیل خروجی هوش مصنوعی. لطفاً دوباره تلاش کنید.", chat_id=user_id, message_id=status_msg.message_id)
            # برای عیب‌یابی شما در محیط تست:
            print("Failed to parse this response:", bot_reply_text)

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

        # 2. سیستم یادآور زمان‌بندی شده (فقط برای متن‌های مستقیم)
        text_normalized = fa_to_en_digits(text)
        pattern = r"^(?:/remind\s+(\d+)\s+(.+)|(\d+)\s*دقیقه\s*(?:دیگه|بعد)\s*(?:بهم\s*)?(?:بگو|پیام\s*بده)\s*(.+))$"
        match = re.search(pattern, text_normalized, re.IGNORECASE)

        if match:
            try:
                if match.group(1):  
                    minutes = int(match.group(1))
                    reminder_text = match.group(2)
                else:  
                    minutes = int(match.group(3))
                    reminder_text = match.group(4)

                send_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)

                db_run(supabase.table("scheduled_reminders").insert({
                    "user_id": user_id,
                    "message_text": reminder_text,
                    "send_at": send_at.isoformat(),
                    "is_sent": False
                }))

                bot.reply_to(message, f"⏰ یادآور ثبت شد! {minutes} دقیقه دیگر به شما پیام می‌دهم:\n\n«{reminder_text}»")
                return
            except Exception:
                bot.reply_to(message, "❌ خطایی در ثبت یادآور رخ داد.")
                return

        # 3. بخش چت، حافظه و اینترنت
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

        current_time_iran = datetime.datetime.now(IRAN_TZ).strftime("%Y-%m-%d %H:%M:%S")

        sys_instruct = f"""تو یک دستیار هوشمند هستی. نام کاربر تو {user_name} است.
زمان فعلی در ایران: {current_time_iran}
1. تو به اینترنت متصل هستی. در صورت نیاز به روزترین اطلاعات را جستجو کن.
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

        bot_reply = None
        random.shuffle(genai_clients) 
        
        for client in genai_clients:
            try:
                response = client.models.generate_content(model='gemini-2.5-flash', contents=contents, config=config)
                bot_reply = response.text
                break
            except Exception as e:
                err_str = str(e).lower()
                if "eof" in err_str or "disconnected" in err_str or "ssl" in err_str:
                    try:
                        response = client.models.generate_content(model='gemini-2.5-flash', contents=contents, config=config)
                        bot_reply = response.text
                        break
                    except:
                        continue
                elif "429" in err_str or "exhausted" in err_str:
                    continue
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
# 🌐 تنظیمات Webhook و Cron برای Vercel
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
        res = db_run(
            supabase.table("scheduled_reminders")
            .select("*")
            .eq("is_sent", False)
            .lte("send_at", now_utc)
        )
        
        sent_count = 0
        for row in res.data:
            try:
                bot.send_message(row["user_id"], row["message_text"])
                
                db_run(
                    supabase.table("scheduled_reminders")
                    .update({"is_sent": True})
                    .eq("id", row["id"])
                )
                sent_count += 1
            except Exception as send_err:
                pass
                
        return jsonify({"status": "success", "processed": sent_count}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/', methods=['GET'])
def index():
    return "✅ ربات مجهز به ساعت زنده ایران، سرچ گوگل و یادآور هوشمند صوتی است!"
