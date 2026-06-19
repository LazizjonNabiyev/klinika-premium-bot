import os, asyncio, logging, aiohttp
from datetime import datetime, timedelta
import pytz
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from flask import Flask, request
import threading, time

logging.basicConfig(level=logging.INFO)

BOT_TOKEN    = os.environ.get("BOT_TOKEN", "8519255967:AAGJPFIBqCZlDHTWSmsBohfo03swSzWtmAo")
GROUP_ID     = os.environ.get("GROUP_ID", "@doctorashurovclicnicbaza")
ADMIN_IDS    = [int(x) for x in os.environ.get("ADMIN_IDS", "920162633").split(",") if x]
WEBHOOK_URL  = os.environ.get("WEBHOOK_URL", "https://klinikabot-production.up.railway.app")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
PAYME_CARD   = os.environ.get("PAYME_CARD", "8600 1234 5678 9012")  # O'zgartiring
CLICK_CARD   = os.environ.get("CLICK_CARD", "8600 9876 5432 1098")  # O'zgartiring
TZ           = pytz.timezone("Asia/Tashkent")


CLINIC_NAME    = "Ashurov Clinik"
CLINIC_PHONE   = "+998 91 166 66 96\n📱 +998 90 995 17 77"
CLINIC_ADDRESS = "Toshkent sh., Dormon yo'li"

DOCTORS = {
    "1": {"name": "Dr. Ashurov B.A.",   "spec_uz": "Terapevt",     "spec_ru": "Терапевт",     "price": 50000, "times": ["09:00","09:30","10:00","10:30","11:00","11:30","14:00","14:30","15:00","15:30"]},
    "2": {"name": "Dr. Xolmatova M.S.", "spec_uz": "Kardiolog",    "spec_ru": "Кардиолог",    "price": 70000, "times": ["09:00","10:00","11:00","14:00","15:00","16:00"]},
    "3": {"name": "Dr. Karimov J.R.",   "spec_uz": "Nevropatolog", "spec_ru": "Невропатолог", "price": 65000, "times": ["09:00","09:30","10:00","11:00","14:00","15:00"]},
    "4": {"name": "Dr. Yusupova N.K.",  "spec_uz": "Ginekolog",    "spec_ru": "Гинеколог",    "price": 80000, "times": ["09:00","10:00","11:00","14:00","15:00"]},
    "5": {"name": "Dr. Nazarov F.B.",   "spec_uz": "Jarroh",       "spec_ru": "Хирург",       "price": 90000, "times": ["10:00","11:00","14:00","15:00","16:00"]},
    "6": {"name": "Dr. Tosheva G.M.",   "spec_uz": "Pediatr",      "spec_ru": "Педиатр",      "price": 55000, "times": ["09:00","10:00","11:00","14:00","15:00"]},
    "7": {"name": "Dr. Rahimov A.T.",   "spec_uz": "Ortoped",      "spec_ru": "Ортопед",      "price": 75000, "times": ["10:00","11:00","14:00","15:00","16:00"]},
}

SERVICES = {
    "uz": [("🔬 Qon tahlili","25,000 so'm"),("🫀 EKG","30,000 so'm"),("🔊 UZI","50,000 so'm"),("👁 Ko'z tekshiruvi","40,000 so'm"),("💉 Ukol","15,000 so'm"),("🩺 Shifokor ko'rigi","50,000 so'm")],
    "ru": [("🔬 Анализ крови","25,000 сум"),("🫀 ЭКГ","30,000 сум"),("🔊 УЗИ","50,000 сум"),("👁 Осмотр глаз","40,000 сум"),("💉 Укол","15,000 сум"),("🩺 Приём врача","50,000 сум")]
}

user_state      = {}
users_db        = {}
appointments    = {}
appt_counter    = [0]
booked_times    = {}
doctor_counters = {}
ratings         = []
ai_sessions     = {}  # {uid: [{"role": "user/model", "parts": "..."}]}

def get_next_number(doc_id, date):
    if doc_id not in doctor_counters: doctor_counters[doc_id] = {}
    if date not in doctor_counters[doc_id]: doctor_counters[doc_id][date] = 0
    doctor_counters[doc_id][date] += 1
    return doctor_counters[doc_id][date]

def get_s(uid): return user_state.get(str(uid), {})
def set_s(uid, s): user_state[str(uid)] = s
def del_s(uid): user_state.pop(str(uid), None)
def is_admin(uid): return int(uid) in ADMIN_IDS
def now_tz(): return datetime.now(TZ)

def format_price(price):
    return f"{price:,}".replace(",", " ") + " so'm"

def get_dates():
    dates, d = [], now_tz().date()
    for i in range(10):
        dd = d + timedelta(days=i)
        if dd.weekday() < 6:
            dates.append(dd.strftime("%d.%m.%Y"))
        if len(dates) == 5: break
    return dates

def get_free_times(doc_id, date):
    all_times = DOCTORS[doc_id]["times"]
    taken = booked_times.get(doc_id, {}).get(date, [])
    return [t for t in all_times if t not in taken]

def book_time(doc_id, date, time):
    if doc_id not in booked_times: booked_times[doc_id] = {}
    if date not in booked_times[doc_id]: booked_times[doc_id][date] = []
    booked_times[doc_id][date].append(time)

def unbook_time(doc_id, date, time):
    try: booked_times[doc_id][date].remove(time)
    except: pass

def get_user_appointments(uid):
    return [a for a in appointments.values() if a["uid"]==uid and a["status"] in ["pending","confirmed","payment_pending"]]

# ─── OPENROUTER AI ───────────────────────────────────────────
async def ask_gemini(uid, user_message, lang):
    try:
        if uid not in ai_sessions:
            ai_sessions[uid] = []

        system_prompt = f"""Sen {CLINIC_NAME} tibbiy markazining AI yordamchisisiz.
Faqat tibbiy maslahat va klinika haqida savollarga javob ber.
Klinika haqida: {CLINIC_ADDRESS}, tel: {CLINIC_PHONE}
Shifokorlar: Terapevt, Kardiolog, Nevropatolog, Ginekolog, Jarroh, Pediatr, Ortoped.
Agar savol tibbiyotga aloqasiz bo'lsa, "Bu savolga javob bera olmayman, tibbiy maslahat uchun murojaat qiling" de.
Javob {'o\'zbek tilida' if lang=='uz' else 'rus tilida'} bo'lsin. Qisqa va aniq javob ber."""

        history = ai_sessions[uid][-6:] if len(ai_sessions[uid]) > 6 else ai_sessions[uid]

        messages = [{"role": "system", "content": system_prompt}]
        for h in history:
            messages.append({"role": h["role"], "content": h["parts"]})
        messages.append({"role": "user", "content": user_message})

        models = [
            "meta-llama/llama-3.3-70b-instruct:free",
            "openai/gpt-oss-20b:free",
            "nousresearch/hermes-3-llama-3.1-405b:free",
            "openchat/openchat-7b:free",
        ]
        answer = None
        async with aiohttp.ClientSession() as session:
            for model in models:
                try:
                    async with session.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {OPENROUTER_KEY}",
                            "Content-Type": "application/json",
                            "HTTP-Referer": "https://t.me/ashurov_clinik_bot",
                        },
                        json={
                            "model": model,
                            "messages": messages,
                            "max_tokens": 500,
                        },
                        timeout=aiohttp.ClientTimeout(total=15)
                    ) as resp:
                        data = await resp.json()
                        logging.info(f"OpenRouter [{model}]: {data}")
                        if "choices" in data and data["choices"]:
                            answer = data["choices"][0]["message"]["content"]
                            break
                        else:
                            logging.warning(f"Model {model} failed: {data}")
                except Exception as me:
                    logging.warning(f"Model {model} exception: {me}")
        if not answer:
            return "\u274c AI hozir ishlamayapti. Keyinroq urinib ko\u2019ring." if lang=="uz" else "\u274c AI nedostupen."

        ai_sessions[uid].append({"role": "user", "parts": user_message})
        ai_sessions[uid].append({"role": "assistant", "parts": answer})

        if len(ai_sessions[uid]) > 20:
            ai_sessions[uid] = ai_sessions[uid][-10:]

        return answer

    except asyncio.TimeoutError:
        return "⏱ Javob kechikdi. Iltimos qayta yuboring." if lang=="uz" else "⏱ Ответ задержался. Попробуйте снова."
    except Exception as e:
        logging.error(f"OpenRouter error: {e}")
        return "❌ AI hozir ishlamayapti. Keyinroq urinib ko'ring." if lang=="uz" else "❌ AI недоступен. Попробуйте позже."

# ─── KLAVIATURALAR ───────────────────────────────────────────
def kb_lang():
    return ReplyKeyboardMarkup([["🇺🇿 O'zbekcha","🇷🇺 Русский"]], resize_keyboard=True, one_time_keyboard=True)

def kb_menu(lang):
    if lang=="ru":
        return ReplyKeyboardMarkup([
            ["📅 Записаться на приём"],
            ["📋 Мои записи","👨‍⚕️ Наши врачи"],
            ["💰 Услуги и цены","📍 Адрес"],
            ["🤖 AI Maslahat","📞 Контакты"]
        ], resize_keyboard=True)
    return ReplyKeyboardMarkup([
        ["📅 Navbat olish"],
        ["📋 Mening navbatlarim","👨‍⚕️ Shifokorlar"],
        ["💰 Xizmatlar va narxlar","📍 Manzil"],
        ["🤖 AI Maslahat","📞 Bog'lanish"]
    ], resize_keyboard=True)

def kb_admin():
    return ReplyKeyboardMarkup([
        ["📋 Bugungi navbatlar","📊 Statistika"],
        ["📢 Xabar yuborish","👨‍⚕️ Shifokor qo'shish"],
        ["👤 Admin qo'shish","⭐ Baholar"],
        ["💳 To'lovlar","🔙 Chiqish"]
    ], resize_keyboard=True)

def kb_doctors(lang):
    rows = []
    for did, d in DOCTORS.items():
        spec = d["spec_uz"] if lang=="uz" else d["spec_ru"]
        price = format_price(d["price"])
        rows.append([f"{d['name']} — {spec} ({price})"])
    rows.append(["🔙 Orqaga" if lang=="uz" else "🔙 Назад"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def kb_dates(lang):
    rows = [[d] for d in get_dates()]
    rows.append(["🔙 Orqaga" if lang=="uz" else "🔙 Назад"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def kb_times(times, lang):
    if not times:
        return ReplyKeyboardMarkup([["🔙 Orqaga" if lang=="uz" else "🔙 Назад"]], resize_keyboard=True)
    rows = [times[i:i+3] for i in range(0, len(times), 3)]
    rows.append(["🔙 Orqaga" if lang=="uz" else "🔙 Назад"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def kb_confirm(lang):
    if lang=="ru": return ReplyKeyboardMarkup([["✅ Подтвердить","❌ Отмена"]], resize_keyboard=True, one_time_keyboard=True)
    return ReplyKeyboardMarkup([["✅ Tasdiqlash","❌ Bekor qilish"]], resize_keyboard=True, one_time_keyboard=True)

def kb_contact(lang):
    btn = KeyboardButton("📱 Raqamni ulashish" if lang=="uz" else "📱 Поделиться номером", request_contact=True)
    return ReplyKeyboardMarkup([[btn]], resize_keyboard=True, one_time_keyboard=True)

def kb_registrar(appt_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"confirm_{appt_id}"),
        InlineKeyboardButton("❌ Bekor qilish", callback_data=f"cancel_{appt_id}")
    ]])

def kb_payment_confirm(appt_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ To'lov tasdiqlandi", callback_data=f"pay_ok_{appt_id}"),
        InlineKeyboardButton("❌ Rad etish", callback_data=f"pay_no_{appt_id}")
    ]])

def kb_cancel_appt(appt_id, lang):
    txt = "❌ Bekor qilish" if lang=="uz" else "❌ Отменить"
    return InlineKeyboardMarkup([[InlineKeyboardButton(txt, callback_data=f"user_cancel_{appt_id}")]])

def kb_rating():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⭐1", callback_data="rate_1"),
        InlineKeyboardButton("⭐2", callback_data="rate_2"),
        InlineKeyboardButton("⭐3", callback_data="rate_3"),
        InlineKeyboardButton("⭐4", callback_data="rate_4"),
        InlineKeyboardButton("⭐5", callback_data="rate_5"),
    ]])

def kb_ai_exit(lang):
    return ReplyKeyboardMarkup([["🚪 AI dan chiqish" if lang=="uz" else "🚪 Выйти из AI"]], resize_keyboard=True)

# ─── GURUHGA YUBORISH ────────────────────────────────────────
async def send_to_group(bot, appt):
    now = now_tz().strftime("%d.%m.%Y %H:%M")
    doc = DOCTORS[appt['doc_id']]
    msg = (f"🏥 *Yangi navbat #{appt['id']} — {CLINIC_NAME}*\n━━━━━━━━━━━━━━\n"
           f"👤 *Ism:* {appt['name']}\n📞 *Tel:* {appt['phone']}\n━━━━━━━━━━━━━━\n"
           f"👨‍⚕️ *{appt['doctor']}* ({doc['spec_uz']})\n"
           f"📅 *Sana:* {appt['date']}\n🕐 *Vaqt:* {appt['time']}\n"
           f"🔢 *Navbat:* #{appt.get('queue_num','?')}\n"
           f"💰 *To'lov:* {format_price(doc['price'])}\n"
           f"💳 *Holat:* {'✅ To\'langan' if appt.get('paid') else '⏳ Kutilmoqda'}\n"
           f"━━━━━━━━━━━━━━\n🕐 {now}")
    await bot.send_message(chat_id=GROUP_ID, text=msg, parse_mode="Markdown", reply_markup=kb_registrar(appt['id']))

async def send_payment_check(bot, appt, photo_id):
    doc = DOCTORS[appt['doc_id']]
    caption = (f"💳 *To'lov cheki #{appt['id']}*\n\n"
               f"👤 {appt['name']}\n📞 {appt['phone']}\n"
               f"👨‍⚕️ {appt['doctor']}\n📅 {appt['date']} — {appt['time']}\n"
               f"💰 {format_price(doc['price'])}")
    await bot.send_photo(chat_id=GROUP_ID, photo=photo_id, caption=caption,
                         parse_mode="Markdown", reply_markup=kb_payment_confirm(appt['id']))

# ─── ESLATMA ─────────────────────────────────────────────────
def reminder_worker():
    while True:
        try:
            now = now_tz()
            today = now.strftime("%d.%m.%Y")
            for appt in list(appointments.values()):
                if appt["status"] != "confirmed": continue
                if appt["date"] != today: continue
                if appt.get("reminded"): continue
                try:
                    appt_time = datetime.strptime(f"{today} {appt['time']}", "%d.%m.%Y %H:%M")
                    appt_time = TZ.localize(appt_time)
                    diff = (appt_time - now).total_seconds() / 60
                    if 55 <= diff <= 65:
                        appt["reminded"] = True
                        asyncio.run_coroutine_threadsafe(send_reminder(appt), loop)
                except: pass
        except: pass
        time.sleep(60)

async def send_reminder(appt):
    try:
        await ptb_app.bot.send_message(
            chat_id=appt["uid"],
            text=(f"⏰ *Eslatma!*\n\nBugun soat *{appt['time']}* da navbatingiz bor.\n"
                  f"👨‍⚕️ {appt['doctor']}\n\n📍 {CLINIC_ADDRESS}\n\nVaqtida kelishingizni so'raymiz! 🙏"),
            parse_mode="Markdown")
    except: pass

# ─── CALLBACK ────────────────────────────────────────────────
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    data = cb.data
    uid = cb.from_user.id
    await cb.answer()

    # Registratura tasdiqlashi
    if data.startswith("confirm_") or data.startswith("cancel_"):
        action = data.split("_")[0]
        appt_id = int(data.split("_")[1])
        appt = appointments.get(appt_id)
        if not appt:
            await cb.edit_message_text("❌ Navbat topilmadi"); return
        admin_name = cb.from_user.first_name or "Admin"

        if action == "confirm":
            if appt["status"] == "confirmed":
                await cb.answer("Allaqachon tasdiqlangan!", show_alert=True); return
            appt["status"] = "confirmed"
            await cb.edit_message_text(cb.message.text + f"\n\n✅ *Tasdiqlandi* — {admin_name}", parse_mode="Markdown")
            try:
                await ctx.bot.send_message(chat_id=appt["uid"],
                    text=(f"✅ *Navbatingiz tasdiqlandi!*\n\n"
                          f"👨‍⚕️ {appt['doctor']}\n📅 {appt['date']} — {appt['time']}\n\n"
                          f"📍 {CLINIC_ADDRESS}\n📞 {CLINIC_PHONE}\n\n⏰ Vaqtida keling!"),
                    parse_mode="Markdown")
            except: pass

        elif action == "cancel":
            if appt["status"] == "cancelled":
                await cb.answer("Allaqachon bekor!", show_alert=True); return
            appt["status"] = "cancelled"
            unbook_time(appt["doc_id"], appt["date"], appt["time"])
            await cb.edit_message_text(cb.message.text + f"\n\n❌ *Bekor* — {admin_name}", parse_mode="Markdown")
            try:
                await ctx.bot.send_message(chat_id=appt["uid"],
                    text=(f"❌ *Navbatingiz bekor qilindi*\n\n"
                          f"👨‍⚕️ {appt['doctor']}\n📅 {appt['date']} — {appt['time']}\n\n"
                          f"Qayta navbat olish uchun /start\n📞 {CLINIC_PHONE}"),
                    parse_mode="Markdown")
            except: pass

    # To'lov tasdiqlash
    elif data.startswith("pay_ok_") or data.startswith("pay_no_"):
        action = "ok" if data.startswith("pay_ok_") else "no"
        appt_id = int(data.split("_")[2])
        appt = appointments.get(appt_id)
        if not appt:
            await cb.answer("Topilmadi", show_alert=True); return
        admin_name = cb.from_user.first_name or "Admin"

        if action == "ok":
            appt["paid"] = True
            appt["status"] = "confirmed"
            await cb.edit_message_caption(
                caption=cb.message.caption + f"\n\n✅ *To'lov tasdiqlandi* — {admin_name}",
                parse_mode="Markdown")
            try:
                doc = DOCTORS[appt["doc_id"]]
                await ctx.bot.send_message(chat_id=appt["uid"],
                    text=(f"✅ *To'lovingiz tasdiqlandi!*\n\n"
                          f"👨‍⚕️ {appt['doctor']}\n📅 {appt['date']} — {appt['time']}\n"
                          f"💰 {format_price(doc['price'])}\n\n"
                          f"📍 {CLINIC_ADDRESS}\n⏰ Vaqtida keling! 🙏"),
                    parse_mode="Markdown")
            except: pass

        else:
            await cb.edit_message_caption(
                caption=cb.message.caption + f"\n\n❌ *To'lov rad etildi* — {admin_name}",
                parse_mode="Markdown")
            try:
                await ctx.bot.send_message(chat_id=appt["uid"],
                    text=(f"❌ *To'lovingiz tasdiqlanmadi*\n\n"
                          f"Iltimos to'g'ri chek yuboring yoki bog'laning:\n📞 {CLINIC_PHONE}"))
                # Qayta chek yuborish uchun state
                lang = users_db.get(str(appt["uid"]), {}).get("lang", "uz")
                set_s(appt["uid"], {**get_s(appt["uid"]), "step": "send_payment", "appt_id": appt_id})
            except: pass

    # Foydalanuvchi bekor qilish
    elif data.startswith("user_cancel_"):
        appt_id = int(data.split("_")[2])
        appt = appointments.get(appt_id)
        if not appt or appt["uid"] != uid:
            await cb.answer("❌ Topilmadi", show_alert=True); return
        if appt["status"] == "cancelled":
            await cb.answer("Allaqachon bekor!", show_alert=True); return
        appt["status"] = "cancelled"
        unbook_time(appt["doc_id"], appt["date"], appt["time"])
        await cb.edit_message_text(f"❌ Bekor qilindi\n\n👨‍⚕️ {appt['doctor']}\n📅 {appt['date']} — {appt['time']}")
        try:
            await ctx.bot.send_message(chat_id=GROUP_ID,
                text=f"❌ Mijoz bekor qildi\n👤 {appt['name']}\n📅 {appt['date']} — {appt['time']}")
        except: pass

    # Baholash
    elif data.startswith("rate_"):
        star = int(data.split("_")[1])
        s = get_s(uid)
        appt_id = s.get("rating_appt")
        ratings.append({"uid": uid, "appt_id": appt_id, "rating": star})
        set_s(uid, {**s, "rating_step": "comment", "rating_star": star})
        lang = users_db.get(str(uid), {}).get("lang", "uz")
        await cb.edit_message_text(
            f"{'⭐'*star} Rahmat! Izoh qoldirishingiz mumkin (yoki /start):" if lang=="uz" else
            f"{'⭐'*star} Спасибо! Можете оставить комментарий (или /start):")

# ─── ASOSIY HANDLER ──────────────────────────────────────────
async def handle_update(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.from_user: return
    uid  = msg.from_user.id
    text = (msg.text or "").strip()
    s    = get_s(uid)
    lang = s.get("lang", "uz")

    # /start
    if text == "/start":
        del_s(uid)
        ai_sessions.pop(uid, None)
        await msg.reply_text("🌐 Tilni tanlang / Выберите язык:", reply_markup=kb_lang())
        return

    # Admin
    if is_admin(uid) and text == "/admin":
        set_s(uid, {**s, "admin_mode": True})
        await msg.reply_text("🔧 Admin Panel:", reply_markup=kb_admin())
        return

    if is_admin(uid) and text.startswith("/reply "):
        parts = text.split(" ", 2)
        if len(parts) == 3:
            try:
                await ctx.bot.send_message(chat_id=int(parts[1]), text=f"📩 {CLINIC_NAME}:\n\n{parts[2]}")
                await msg.reply_text("✅ Yuborildi!")
            except: await msg.reply_text("❌ Xato")
        return

    # AI sessiya
    if s.get("step") == "ai_chat":
        if text in ["🚪 AI dan chiqish", "🚪 Выйти из AI", "/start"]:
            set_s(uid, {"lang": lang, "step": "menu"})
            ai_sessions.pop(uid, None)
            await msg.reply_text("Asosiy menyu:" if lang=="uz" else "Главное меню:", reply_markup=kb_menu(lang))
            return
        # AI ga yuborish
        await ctx.bot.send_chat_action(chat_id=uid, action="typing")
        answer = await ask_gemini(uid, text, lang)
        await msg.reply_text(answer, reply_markup=kb_ai_exit(lang))
        return

    # To'lov cheki yuborish
    if s.get("step") == "send_payment":
        appt_id = s.get("appt_id")
        appt = appointments.get(appt_id)
        if msg.photo:
            photo_id = msg.photo[-1].file_id
            await send_payment_check(ctx.bot, appt, photo_id)
            set_s(uid, {"lang": lang, "step": "menu"})
            await msg.reply_text(
                "✅ Chek yuborildi! Admin tekshirib tasdiqlaydi." if lang=="uz" else
                "✅ Чек отправлен! Ожидайте подтверждения.",
                reply_markup=kb_menu(lang))
        else:
            await msg.reply_text("📸 Iltimos to'lov cheki rasmini yuboring!" if lang=="uz" else "📸 Пришлите фото чека!")
        return

    # Izoh (baholash)
    if s.get("rating_step") == "comment":
        ratings[-1]["comment"] = text
        set_s(uid, {**s, "rating_step": None})
        star = s.get("rating_star", 5)
        for aid in ADMIN_IDS:
            try:
                await ctx.bot.send_message(chat_id=aid,
                    text=f"⭐ *Yangi baho*\n\n{'⭐'*star} ({star}/5)\n"
                         f"👤 {users_db.get(str(uid),{}).get('name','—')}\n💬 {text}",
                    parse_mode="Markdown")
            except: pass
        await msg.reply_text("🙏 Fikringiz uchun rahmat!", reply_markup=kb_menu(lang))
        return

    # Admin panel
    if s.get("admin_mode"):
        if text == "🔙 Chiqish":
            set_s(uid, {"lang": lang})
            await msg.reply_text("Asosiy menyu:", reply_markup=kb_menu(lang)); return

        if text == "📋 Bugungi navbatlar":
            today = now_tz().strftime("%d.%m.%Y")
            ta = sorted([a for a in appointments.values() if a["date"]==today], key=lambda x: x["time"])
            if not ta: await msg.reply_text("Bugun navbat yo'q"); return
            result = f"📋 Bugun ({today}) — {len(ta)} ta:\n\n"
            for a in ta:
                st = "✅" if a["status"]=="confirmed" else "❌" if a["status"]=="cancelled" else "⏳"
                paid = "💰✅" if a.get("paid") else "💰⏳"
                result += f"{st}{paid} {a['time']} — {a['name']}\n📞 {a['phone']}\n👨‍⚕️ {a['doctor']}\n\n"
            await msg.reply_text(result); return

        if text == "📊 Statistika":
            today = now_tz().strftime("%d.%m.%Y")
            today_c = len([a for a in appointments.values() if a["date"]==today])
            confirmed = len([a for a in appointments.values() if a["status"]=="confirmed"])
            paid_total = sum(DOCTORS[a["doc_id"]]["price"] for a in appointments.values() if a.get("paid"))
            avg_rating = round(sum(r["rating"] for r in ratings)/len(ratings), 1) if ratings else "—"
            await msg.reply_text(
                f"📊 *Statistika*\n\n"
                f"👥 Ro'yxatdan: {len(users_db)}\n"
                f"📅 Bugun: {today_c}\n"
                f"✅ Tasdiqlangan: {confirmed}\n"
                f"📦 Jami: {len(appointments)}\n"
                f"💰 Jami tushum: {format_price(paid_total)}\n"
                f"⭐ O'rtacha baho: {avg_rating}",
                parse_mode="Markdown"); return

        if text == "⭐ Baholar":
            if not ratings: await msg.reply_text("Hozircha baho yo'q"); return
            avg = round(sum(r["rating"] for r in ratings)/len(ratings), 1)
            result = f"⭐ *Baholar* — O'rtacha: {avg}/5\n\n"
            for r in ratings[-10:]:
                name = users_db.get(str(r["uid"]), {}).get("name", "—")
                result += f"{'⭐'*r['rating']} — {name}\n"
                if r.get("comment"): result += f"💬 {r['comment']}\n"
                result += "\n"
            await msg.reply_text(result, parse_mode="Markdown"); return

        if text == "💳 To'lovlar":
            paid = [a for a in appointments.values() if a.get("paid")]
            pending = [a for a in appointments.values() if not a.get("paid") and a["status"] not in ["cancelled"]]
            total = sum(DOCTORS[a["doc_id"]]["price"] for a in paid)
            result = f"💳 *To'lovlar*\n\n✅ To'langan: {len(paid)} ta\n⏳ Kutilmoqda: {len(pending)} ta\n💰 Jami: {format_price(total)}"
            await msg.reply_text(result, parse_mode="Markdown"); return

        if text == "📢 Xabar yuborish":
            set_s(uid, {**s, "admin_step": "broadcast"})
            await msg.reply_text(f"Barcha {len(users_db)} ta foydalanuvchiga xabar:"); return

        if s.get("admin_step") == "broadcast":
            count = 0
            for u_id in users_db:
                try:
                    await ctx.bot.send_message(chat_id=int(u_id), text=f"📢 {CLINIC_NAME}:\n\n{text}")
                    count += 1
                except: pass
            set_s(uid, {**s, "admin_step": None})
            await msg.reply_text(f"✅ {count} ta foydalanuvchiga yuborildi!", reply_markup=kb_admin()); return

        if text == "👤 Admin qo'shish":
            set_s(uid, {**s, "admin_step": "add_admin"})
            await msg.reply_text("Yangi admin Telegram ID:"); return

        if s.get("admin_step") == "add_admin":
            try:
                ADMIN_IDS.append(int(text))
                set_s(uid, {**s, "admin_step": None})
                await msg.reply_text(f"✅ {text} admin!", reply_markup=kb_admin())
            except: await msg.reply_text("❌ Raqam kiriting")
            return

        if text == "👨‍⚕️ Shifokor qo'shish":
            set_s(uid, {**s, "admin_step": "doc_name"})
            await msg.reply_text("Shifokor ismi:"); return
        if s.get("admin_step") == "doc_name":
            set_s(uid, {**s, "admin_step": "doc_spec", "doc_name": text})
            await msg.reply_text("Mutaxassislik:"); return
        if s.get("admin_step") == "doc_spec":
            set_s(uid, {**s, "admin_step": "doc_price", "doc_spec": text})
            await msg.reply_text("Ko'rik narxi (so'mda, faqat raqam):"); return
        if s.get("admin_step") == "doc_price":
            try:
                price = int(text.replace(" ", ""))
                new_id = str(len(DOCTORS)+1)
                DOCTORS[new_id] = {"name": s["doc_name"], "spec_uz": s["doc_spec"], "spec_ru": s["doc_spec"],
                                   "price": price, "times": ["09:00","10:00","11:00","14:00","15:00"]}
                set_s(uid, {**s, "admin_step": None})
                await msg.reply_text(f"✅ {s['doc_name']} qo'shildi!", reply_markup=kb_admin())
            except: await msg.reply_text("❌ Faqat raqam kiriting")
            return

    # Til tanlash
    if text == "🇺🇿 O'zbekcha":
        set_s(uid, {"lang": "uz"})
        name = msg.from_user.first_name or "Do'stim"
        if str(uid) not in users_db:
            set_s(uid, {"lang": "uz", "step": "get_name"})
            await msg.reply_text(f"Salom, {name}! 👋\n\nTo'liq ismingizni kiriting:")
        else:
            set_s(uid, {"lang": "uz", "step": "menu"})
            await msg.reply_text(f"🏥 {CLINIC_NAME}\n\nSalom, {users_db[str(uid)]['name']}!", reply_markup=kb_menu("uz"))
        return

    if text == "🇷🇺 Русский":
        set_s(uid, {"lang": "ru"})
        name = msg.from_user.first_name or "Друг"
        if str(uid) not in users_db:
            set_s(uid, {"lang": "ru", "step": "get_name"})
            await msg.reply_text(f"Привет, {name}! 👋\n\nВведите ваше полное имя:")
        else:
            set_s(uid, {"lang": "ru", "step": "menu"})
            await msg.reply_text(f"🏥 {CLINIC_NAME}\n\nПривет, {users_db[str(uid)]['name']}!", reply_markup=kb_menu("ru"))
        return

    # Ro'yxatdan o'tish
    if s.get("step") == "get_name":
        set_s(uid, {**s, "step": "get_phone", "reg_name": text})
        await msg.reply_text("📞 Telefon raqamingizni ulashing:" if lang=="uz" else "📞 Поделитесь номером:", reply_markup=kb_contact(lang))
        return

    if s.get("step") == "get_phone":
        phone = msg.contact.phone_number if msg.contact else text
        if not phone: await msg.reply_text("📞 Raqam yuboring"); return
        users_db[str(uid)] = {"name": s["reg_name"], "phone": phone, "lang": lang}
        set_s(uid, {"lang": lang, "step": "menu"})
        await msg.reply_text("✅ Ro'yxatdan o'tdingiz!\n\n🏥 " + CLINIC_NAME if lang=="uz" else "✅ Вы зарегистрированы!", reply_markup=kb_menu(lang))
        try:
            await ctx.bot.send_message(chat_id=GROUP_ID,
                text=f"👤 *Yangi foydalanuvchi*\n\nIsm: {s['reg_name']}\nTel: {phone}\nID: {uid}",
                parse_mode="Markdown")
        except: pass
        return

    if text in ["🔙 Orqaga", "🔙 Назад"]:
        set_s(uid, {"lang": lang, "step": "menu"})
        await msg.reply_text("Asosiy menyu:" if lang=="uz" else "Главное меню:", reply_markup=kb_menu(lang)); return

    # AI Maslahat
    if text in ["🤖 AI Maslahat", "🤖 AI Консультация"]:
        set_s(uid, {**s, "step": "ai_chat"})
        await msg.reply_text(
            "🤖 *AI Tibbiy Maslahat*\n\nSavolingizni yozing. Chiqish uchun tugmani bosing." if lang=="uz" else
            "🤖 *AI Консультация*\n\nНапишите вопрос. Для выхода нажмите кнопку.",
            parse_mode="Markdown", reply_markup=kb_ai_exit(lang))
        return

    # Mening navbatlarim
    if text in ["📋 Mening navbatlarim", "📋 Мои записи"]:
        my_appts = get_user_appointments(uid)
        if not my_appts:
            await msg.reply_text("Sizda hozircha navbat yo'q" if lang=="uz" else "У вас нет записей"); return
        for a in my_appts:
            doc = DOCTORS[a["doc_id"]]
            st = "⏳ Kutilmoqda" if a["status"]=="pending" else "✅ Tasdiqlangan" if a["status"]=="confirmed" else "💳 To'lov kutilmoqda"
            paid = "💰 To'langan ✅" if a.get("paid") else f"💰 To'lov: {format_price(doc['price'])}"
            txt = f"{st}\n👨‍⚕️ {a['doctor']}\n📅 {a['date']} — {a['time']}\n{paid}"
            await msg.reply_text(txt, reply_markup=kb_cancel_appt(a["id"], lang))
        return

    # Navbat olish
    if text in ["📅 Navbat olish", "📅 Записаться на приём"]:
        if str(uid) not in users_db:
            set_s(uid, {"lang": lang, "step": "get_name"})
            await msg.reply_text("Avval ismingizni kiriting:" if lang=="uz" else "Введите имя:"); return
        set_s(uid, {**s, "step": "choose_doctor"})
        await msg.reply_text("👨‍⚕️ Shifokorni tanlang:" if lang=="uz" else "👨‍⚕️ Выберите врача:", reply_markup=kb_doctors(lang)); return

    if s.get("step") == "choose_doctor":
        chosen = None
        for did, d in DOCTORS.items():
            spec = d["spec_uz"] if lang=="uz" else d["spec_ru"]
            if text == f"{d['name']} — {spec} ({format_price(d['price'])})":
                chosen = (did, d); break
        if not chosen:
            await msg.reply_text("Shifokorni tanlang:", reply_markup=kb_doctors(lang)); return
        set_s(uid, {**s, "step": "choose_date", "doc_id": chosen[0], "doc_name": chosen[1]["name"]})
        await msg.reply_text("📅 Sanani tanlang:" if lang=="uz" else "📅 Выберите дату:", reply_markup=kb_dates(lang)); return

    if s.get("step") == "choose_date":
        if text not in get_dates():
            await msg.reply_text("Sanani tanlang:", reply_markup=kb_dates(lang)); return
        free = get_free_times(s["doc_id"], text)
        if not free:
            await msg.reply_text("😔 Bu kun barcha vaqtlar band:" if lang=="uz" else "😔 Все занято:", reply_markup=kb_dates(lang)); return
        set_s(uid, {**s, "step": "choose_time", "date": text})
        await msg.reply_text("🕐 Vaqtni tanlang:" if lang=="uz" else "🕐 Выберите время:", reply_markup=kb_times(free, lang)); return

    if s.get("step") == "choose_time":
        free = get_free_times(s["doc_id"], s["date"])
        if text not in free:
            await msg.reply_text("Vaqtni tanlang:", reply_markup=kb_times(free, lang)); return
        set_s(uid, {**s, "step": "confirm", "time": text})
        doc = DOCTORS[s["doc_id"]]
        spec = doc["spec_uz"] if lang=="uz" else doc["spec_ru"]
        user = users_db[str(uid)]
        summary = (f"📋 *Navbat ma'lumotlari:*\n\n" if lang=="uz" else f"📋 *Данные записи:*\n\n")
        summary += (f"👤 {user['name']}\n📞 {user['phone']}\n"
                    f"👨‍⚕️ {doc['name']} ({spec})\n📅 {s['date']}\n🕐 {text}\n"
                    f"💰 To'lov: *{format_price(doc['price'])}*\n\n")
        summary += "✅ Tasdiqlaysizmi?" if lang=="uz" else "✅ Подтверждаете?"
        await msg.reply_text(summary, parse_mode="Markdown", reply_markup=kb_confirm(lang)); return

    if s.get("step") == "confirm":
        if text in ["✅ Tasdiqlash", "✅ Подтвердить"]:
            user = users_db[str(uid)]
            doc = DOCTORS[s["doc_id"]]
            appt_counter[0] += 1
            aid = appt_counter[0]
            queue_num = get_next_number(s["doc_id"], s["date"])
            appt = {"id": aid, "uid": uid, "name": user["name"], "phone": user["phone"],
                    "doctor": doc["name"], "doc_id": s["doc_id"], "date": s["date"],
                    "time": s["time"], "status": "payment_pending", "queue_num": queue_num, "paid": False}
            appointments[aid] = appt
            book_time(s["doc_id"], s["date"], s["time"])
            await send_to_group(ctx.bot, appt)

            # To'lov ma'lumotlari
            set_s(uid, {"lang": lang, "step": "send_payment", "appt_id": aid})
            await msg.reply_text(
                f"✅ *Navbat yaratildi!*\n\n"
                f"💰 To'lov summasi: *{format_price(doc['price'])}*\n\n"
                f"📱 To'lov usullari:\n"
                f"• *Payme:* `{PAYME_CARD}`\n"
                f"• *Click:* `{CLICK_CARD}`\n\n"
                f"To'lovdan so'ng 📸 *chek rasmini* shu yerga yuboring!",
                parse_mode="Markdown")
        else:
            set_s(uid, {"lang": lang, "step": "menu"})
            await msg.reply_text("❌ Bekor qilindi" if lang=="uz" else "❌ Отменено", reply_markup=kb_menu(lang))
        return

    if text in ["👨‍⚕️ Shifokorlar", "👨‍⚕️ Наши врачи"]:
        result = "👨‍⚕️ *Bizning shifokorlar:*\n\n" if lang=="uz" else "👨‍⚕️ *Наши врачи:*\n\n"
        for d in DOCTORS.values():
            spec = d["spec_uz"] if lang=="uz" else d["spec_ru"]
            result += f"• *{d['name']}*\n  {spec} — {format_price(d['price'])}\n\n"
        await msg.reply_text(result, parse_mode="Markdown"); return

    if text in ["💰 Xizmatlar va narxlar", "💰 Услуги и цены"]:
        result = "💰 *Xizmatlar:*\n\n" if lang=="uz" else "💰 *Услуги:*\n\n"
        for name, price in SERVICES[lang]: result += f"{name} — *{price}*\n"
        await msg.reply_text(result, parse_mode="Markdown"); return

    if text in ["📍 Manzil", "📍 Адрес"]:
        await msg.reply_text(f"📍 *{CLINIC_NAME}*\n\n{CLINIC_ADDRESS}\n📞 {CLINIC_PHONE}", parse_mode="Markdown"); return

    if text in ["📞 Bog'lanish", "📞 Контакты"]:
        await msg.reply_text(f"📞 *Bog'lanish*\n\n📱 {CLINIC_PHONE}", parse_mode="Markdown"); return

    await msg.reply_text("🌐 Tilni tanlang / Выберите язык:", reply_markup=kb_lang())


# ─── Flask + PTB ─────────────────────────────────────────────
flask_app = Flask(__name__)
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

ptb_app = Application.builder().token(BOT_TOKEN).updater(None).build()
ptb_app.add_handler(CommandHandler("start", handle_update))
ptb_app.add_handler(CallbackQueryHandler(on_callback))
ptb_app.add_handler(MessageHandler(filters.ALL, handle_update))

async def init():
    await ptb_app.initialize()
    await ptb_app.start()
    await ptb_app.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook", drop_pending_updates=True)
    print(f"✅ {CLINIC_NAME} boti ishga tushdi!")

loop.run_until_complete(init())

reminder_thread = threading.Thread(target=reminder_worker, daemon=True)
reminder_thread.start()

@flask_app.route("/", methods=["GET"])
def index(): return f"{CLINIC_NAME} — Ishlayapti! ✅", 200

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True)
    update = Update.de_json(data, ptb_app.bot)
    loop.run_until_complete(ptb_app.process_update(update))
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)
