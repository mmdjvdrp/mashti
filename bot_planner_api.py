import datetime
import pytz

IRAN_TZ = pytz.timezone('Asia/Tehran')

def link_account(supabase, telegram_id, user_uuid):
    """وصل کردن آیدی تلگرام به اطلاعات تقویم کاربر در دیتابیس"""
    try:
        # پیدا کردن کاربر در دیتابیس با استفاده از UUID سایت
        res = supabase.table("planner_data").select("user_id, data").eq("user_id", user_uuid).execute()
        if not res.data:
            return False, "❌ حساب کاربری وب پیدا نشد! مطمئن شوید که کد را درست کپی کرده‌اید."
        
        data = res.data[0]['data']
        # آیدی تلگرام را در داخل JSON دیتابیس تقویم ذخیره می‌کنیم
        data['telegram_id'] = str(telegram_id)
        
        # آپدیت دیتابیس
        supabase.table("planner_data").update({"data": data}).eq("user_id", user_uuid).execute()
        return True, "✅ حساب وب شما با موفقیت به ربات تلگرام متصل شد! \n\nحالا می‌توانید بگویید: «امروز چه کارهایی دارم؟» یا «تسک ورزش رو تیک بزن»."
    except Exception as e:
        return False, f"❌ خطا در ارتباط با دیتابیس: {str(e)}"

def get_user_planner_data(supabase, telegram_id):
    """گرفتن اطلاعات کاربر بر اساس آیدی تلگرام"""
    try:
        res = supabase.table("planner_data").select("user_id, data").eq("data->>telegram_id", str(telegram_id)).execute()
        if res.data:
            return res.data[0]['user_id'], res.data[0]['data']
    except Exception:
        pass
    return None, None

def generate_planner_prompt_context(planner_data):
    """تبدیل کارهای امروز به متنی که هوش مصنوعی بفهمد"""
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

def process_planner_action(supabase, supabase_user_id, planner_data, action, action_id):
    """تیک زدن کارها توسط هوش مصنوعی"""
    if action == "tick_todo" and action_id:
        today = datetime.datetime.now(IRAN_TZ).strftime("%Y-%m-%d")
        todos = planner_data.get("todos", [])
        updated = False
        
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
        
        if updated:
            supabase.table("planner_data").update({"data": planner_data}).eq("user_id", supabase_user_id).execute()
            return True
    return False
