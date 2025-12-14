import asyncio
import logging
import sqlite3
import uuid
import random
import aiohttp

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, 
    CallbackQuery, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    LabeledPrice, 
    PreCheckoutQuery
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

# --- КОНФИГУРАЦИЯ ---
TOKEN = "7643036413:AAHZNDIMkzL-_arsFeuHyAWvfsH2W8oMgBI"
ADMIN_IDS = [7991277731] # Твой ID
ADMIN_USERNAME = "otrizs" # Юзернейм для связи

# !!! ВСТАВЬ СЮДА СВОЙ КЛЮЧ ОТ TWIBOOST !!!
TWIBOOST_API_KEY = "sfEOv8teq7U8vwVCMBqPzPk50kceWHqfR4PVM5kiHiyHKFx2x5Xvd6w23SSw" 
SERVICE_ID = 3576        # ID услуги на Twiboost
API_URL = "https://twiboost.com/api/v2"

logging.basicConfig(level=logging.INFO)

# --- БАЗА ДАННЫХ ---
def db_start():
    conn = sqlite3.connect('vpn_bot.db')
    cur = conn.cursor()
    # Таблица ключей
    cur.execute("CREATE TABLE IF NOT EXISTS keys(code TEXT PRIMARY KEY, uses INTEGER)")
    
    # Таблица пользователей
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY, 
            date_added TEXT,
            available_boosts INTEGER DEFAULT 0
        )
    """)
    
    # Новая таблица для отслеживания активных заказов
    cur.execute("""
        CREATE TABLE IF NOT EXISTS active_orders(
            order_id INTEGER PRIMARY KEY,
            user_id INTEGER
        )
    """)
    
    # Миграция
    try:
        cur.execute("ALTER TABLE users ADD COLUMN available_boosts INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass 
        
    conn.commit()
    conn.close()

# -- Функции БД --
def add_new_key(code, uses):
    conn = sqlite3.connect('vpn_bot.db')
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO keys VALUES(?, ?)", (code, uses))
        conn.commit()
        res = True
    except sqlite3.IntegrityError:
        res = False
    conn.close()
    return res

def delete_key(code):
    conn = sqlite3.connect('vpn_bot.db')
    cur = conn.cursor()
    cur.execute("DELETE FROM keys WHERE code = ?", (code,))
    conn.commit()
    conn.close()

def use_key_transaction(code, user_id):
    conn = sqlite3.connect('vpn_bot.db')
    cur = conn.cursor()
    cur.execute("SELECT uses FROM keys WHERE code = ?", (code,))
    data = cur.fetchone()
    if not data:
        conn.close()
        return False
    uses = data[0]
    if uses > 1:
        cur.execute("UPDATE keys SET uses = ? WHERE code = ?", (uses - 1, code))
    else:
        cur.execute("DELETE FROM keys WHERE code = ?", (code,))
    
    cur.execute("""
        INSERT INTO users (user_id, date_added, available_boosts) 
        VALUES (?, datetime('now'), 1)
        ON CONFLICT(user_id) DO UPDATE SET available_boosts = available_boosts + 1
    """, (user_id,))
    conn.commit()
    conn.close()
    return True

def get_user_boosts(user_id):
    conn = sqlite3.connect('vpn_bot.db')
    cur = conn.cursor()
    cur.execute("SELECT available_boosts FROM users WHERE user_id = ?", (user_id,))
    res = cur.fetchone()
    conn.close()
    if res: return res[0]
    return 0

def decrement_user_boost(user_id):
    conn = sqlite3.connect('vpn_bot.db')
    cur = conn.cursor()
    cur.execute("UPDATE users SET available_boosts = available_boosts - 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

# Функции для отслеживания заказов
def add_active_order(order_id, user_id):
    conn = sqlite3.connect('vpn_bot.db')
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO active_orders (order_id, user_id) VALUES (?, ?)", (order_id, user_id))
    conn.commit()
    conn.close()

def remove_active_order(order_id):
    conn = sqlite3.connect('vpn_bot.db')
    cur = conn.cursor()
    cur.execute("DELETE FROM active_orders WHERE order_id = ?", (order_id,))
    conn.commit()
    conn.close()

def get_all_active_orders():
    conn = sqlite3.connect('vpn_bot.db')
    cur = conn.cursor()
    cur.execute("SELECT order_id, user_id FROM active_orders")
    rows = cur.fetchall()
    conn.close()
    return rows

# --- API ФУНКЦИИ ---
async def send_order_to_twiboost(link: str, quantity: int):
    params = {
        'key': TWIBOOST_API_KEY,
        'action': 'add',
        'service': SERVICE_ID,
        'link': link,
        'quantity': quantity
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(API_URL, data=params) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    return {"error": f"HTTP Error: {response.status}"}
        except Exception as e:
            logging.error(f"Ошибка API: {e}")
            return {"error": str(e)}

async def get_order_status(order_id):
    params = {
        'key': TWIBOOST_API_KEY,
        'action': 'status',
        'order': order_id
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(API_URL, data=params) as response:
                if response.status == 200:
                    return await response.json()
        except:
            pass
    return {}

# --- ФОНОВАЯ ЗАДАЧА: МОНИТОРИНГ ---
async def order_status_monitor(bot: Bot):
    """Проверяет статусы заказов и уведомляет пользователей"""
    while True:
        try:
            orders = get_all_active_orders()
            if not orders:
                await asyncio.sleep(30) # Спим 30 сек если заказов нет
                continue

            for order_id, user_id in orders:
                status_data = await get_order_status(order_id)
                status = status_data.get('status', '').lower()
                
                finished_statuses = ['completed', 'canceled', 'partial', 'refunded']
                
                if status in finished_statuses:
                    remove_active_order(order_id)
                    
                    if status == 'completed':
                        msg_text = (
                            f"✅ <b>Подписка успешно продлена!</b> (Заказ #{order_id})\n\n"
                            f"Все бонусные дни должны быть начислены.\n"
                            f"Если что-то не так — пишите в тех. поддержку: @{ADMIN_USERNAME}"
                        )
                    else:
                        msg_text = (
                            f"⚠️ <b>Заказ #{order_id} завершен со статусом: {status}</b>\n\n"
                            f"Если дни подписки пришли не полностью, пожалуйста, напишите в ТП: @{ADMIN_USERNAME}"
                        )
                    
                    try:
                        await bot.send_message(user_id, msg_text, parse_mode="HTML")
                    except Exception as e:
                        logging.error(f"Не удалось отправить уведомление юзеру {user_id}: {e}")
                
                await asyncio.sleep(1) 
            
            await asyncio.sleep(60) 
            
        except Exception as e:
            logging.error(f"Ошибка в мониторе заказов: {e}")
            await asyncio.sleep(60)

# --- FSM ---
class UserStates(StatesGroup):
    waiting_for_key = State()
    confirming_raff = State() # Новое состояние для Рафф

class AdminStates(StatesGroup):
    waiting_for_key_name = State()
    waiting_for_key_uses = State()
    waiting_for_del_key = State()

# --- КЛАВИАТУРЫ ---
def kb_guest_start():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔑 Активировать ключ", callback_data="start_activation")],
        [InlineKeyboardButton(text="💳 Оплата картой", url=f"https://t.me/{ADMIN_USERNAME}")],
        [InlineKeyboardButton(text="⭐️ Купить за Stars (99★)", callback_data="buy_stars")],
        [InlineKeyboardButton(text="🆘 Тех. Поддержка", url=f"https://t.me/{ADMIN_USERNAME}")]
    ])

def kb_raff_confirm():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Далее ➡️", callback_data="raff_proceed")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="raff_cancel")]
    ])

def kb_admin_main():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать ключ", callback_data="adm_add")],
        [InlineKeyboardButton(text="🗑 Удалить ключ", callback_data="adm_del")],
        [InlineKeyboardButton(text="❌ Закрыть", callback_data="adm_close")]
    ])

def kb_cancel_admin():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Отмена", callback_data="adm_cancel")]
    ])

# --- ЛОГИКА ---
router = Router()
dp = Dispatcher()
dp.include_router(router)

# Админка
@router.message(Command("admin"))
async def admin_panel(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    await message.answer("🔧 <b>Админка</b>", reply_markup=kb_admin_main(), parse_mode="HTML")

@router.callback_query(F.data == "adm_add")
async def adm_add_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Название ключа:", reply_markup=kb_cancel_admin())
    await state.set_state(AdminStates.waiting_for_key_name)

@router.message(AdminStates.waiting_for_key_name)
async def adm_add_name(message: Message, state: FSMContext):
    await state.update_data(key_name=message.text.strip())
    await message.answer("Количество активаций:", reply_markup=kb_cancel_admin())
    await state.set_state(AdminStates.waiting_for_key_uses)

@router.message(AdminStates.waiting_for_key_uses)
async def adm_add_uses(message: Message, state: FSMContext):
    if not message.text.isdigit(): return
    data = await state.get_data()
    if add_new_key(data['key_name'], int(message.text)):
        await message.answer("✅ Создано.")
    else:
        await message.answer("❌ Уже есть.")
    await state.clear()
    await message.answer("Админка", reply_markup=kb_admin_main())

@router.callback_query(F.data == "adm_del")
async def adm_del_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Код ключа для удаления:", reply_markup=kb_cancel_admin())
    await state.set_state(AdminStates.waiting_for_del_key)

@router.message(AdminStates.waiting_for_del_key)
async def adm_del_finish(message: Message, state: FSMContext):
    delete_key(message.text.strip())
    await message.answer("Удалено.", reply_markup=kb_admin_main())
    await state.clear()

@router.callback_query(F.data == "adm_cancel")
async def adm_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Админка", reply_markup=kb_admin_main())
    
@router.callback_query(F.data == "adm_close")
async def adm_close(callback: CallbackQuery):
    await callback.message.delete()

# --- ПОЛЬЗОВАТЕЛЬ ---
def get_premium_text(boosts):
    return (
        f"✅ <b>Доступ активен!</b>\n"
        f"🔥 Доступно активаций: <b>{boosts}</b> шт.\n\n"
        "Отправьте реферальную ссылку на бота (@avoVPN_bot, @molniya_vpn_bot, @raffvpnbot),\n"
        "и она автоматически продлится.\n\n"
        "<i>1 ключ = 1 продление.</i>"
    )

@router.message(CommandStart())
async def cmd_start(message: Message):
    boosts = get_user_boosts(message.from_user.id)
    if boosts > 0:
        await message.answer(get_premium_text(boosts), parse_mode="HTML")
    else:
        text = (
            "👋 <b>Добро пожаловать!</b>\n\n"
            "🔒 VPN от <b>1200 дней</b> за одну активацию!\n"
            "Быстрое и надежное продление подписки.\n\n"
            "💎 <b>Как купить:</b>\n"
            "• Картой\n"
            "• Звездами Telegram (Stars)\n\n"
            "<b>Выберите действие:</b>"
        )
        await message.answer(text, reply_markup=kb_guest_start(), parse_mode="HTML")

@router.callback_query(F.data == "start_activation")
async def process_activation(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("🔑 <b>Введите ваш ключ активации:</b>", parse_mode="HTML")
    await state.set_state(UserStates.waiting_for_key)
    await callback.answer()

@router.message(UserStates.waiting_for_key)
async def process_key_input(message: Message, state: FSMContext):
    if use_key_transaction(message.text.strip(), message.from_user.id):
        boosts = get_user_boosts(message.from_user.id)
        await message.answer("🎉 <b>Ключ активирован!</b>", parse_mode="HTML")
        await message.answer(get_premium_text(boosts), parse_mode="HTML")
        await state.clear()
    else:
        await message.answer("❌ Неверный ключ.", parse_mode="HTML")

# --- ЛОГИКА ЗАКАЗОВ ---
@router.message(F.text.regexp(r'(https?://)?t\.me/'))
async def process_referral_link(message: Message, state: FSMContext):
    user_id = message.from_user.id
    link = message.text.strip()
    
    if get_user_boosts(user_id) <= 0:
        await message.answer("⛔️ <b>Нужен новый ключ!</b>\nКупите ключ для запуска.", reply_markup=kb_guest_start(), parse_mode="HTML")
        return

    valid_bots = ["avoVPN_bot", "molniya_vpn_bot", "raffvpnbot"]
    if not any(bot in link for bot in valid_bots):
        await message.answer("⚠️ Принимаем ссылки только на AvoVPN, MolniyaVPN, Raff VPN.")
        return

    # --- ПРОВЕРКА ДЛЯ RAFF VPN ---
    if "raffvpnbot" in link.lower():
        warning_msg = (
            "⚠️ <b>Внимание для Raff VPN!</b>\n\n"
            "Для успешного продления у вас должна быть активная подписка "
            "(хотя бы пробная), иначе ничего не получится!"
        )
        # Сохраняем ссылку, чтобы использовать её после нажатия кнопки
        await state.update_data(pending_link=link)
        await message.answer(warning_msg, reply_markup=kb_raff_confirm(), parse_mode="HTML")
        return # Прерываем функцию, ждем нажатия кнопки

    # Если это не Рафф, запускаем сразу
    await execute_boost_order(message, user_id, link)

# --- ОБРАБОТКА КНОПОК RAFF ---
@router.callback_query(F.data == "raff_proceed")
async def process_raff_proceed(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    data = await state.get_data()
    link = data.get('pending_link')
    
    if not link:
        await callback.message.edit_text("❌ Ошибка: Ссылка потеряна. Отправьте её заново.")
        return

    # Еще раз проверяем баланс, на всякий случай
    if get_user_boosts(user_id) <= 0:
        await callback.message.edit_text("⛔️ Ключи закончились.")
        return

    # Удаляем сообщение с кнопками и запускаем
    await callback.message.delete()
    # Отправляем новое сообщение, как будто юзер только что скинул ссылку
    new_msg = await callback.message.answer("⏳ Запуск...")
    await execute_boost_order(new_msg, user_id, link, is_edit=True)
    await state.clear()

@router.callback_query(F.data == "raff_cancel")
async def process_raff_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("🔙 Отменено. Вы можете отправить другую ссылку.")

# --- ОБЩАЯ ФУНКЦИЯ ЗАПУСКА ЗАКАЗА ---
async def execute_boost_order(message_obj: Message, user_id: int, link: str, is_edit=False):
    """
    message_obj: объект сообщения, которое будем редактировать или отвечать
    is_edit: если True, значит message_obj это уже сообщение от бота, которое надо редактировать
    """
    
    # Рандом 220-250
    quantity = random.randint(220, 250)
    
    if not is_edit:
        msg = await message_obj.answer(f"⏳ <b>Запуск процесса...</b>", parse_mode="HTML")
    else:
        msg = message_obj
        await msg.edit_text(f"⏳ <b>Запуск процесса...</b>", parse_mode="HTML")
    
    response = await send_order_to_twiboost(link, quantity)
    
    if response and "order" in response:
        decrement_user_boost(user_id)
        order_id = response['order']
        
        # Добавляем в мониторинг
        add_active_order(order_id, user_id)
        
        await msg.edit_text(
            f"✅ <b>Заказ #{order_id} принят в работу!</b>\n"
            f"🔗 Ссылка: {link}\n\n"
            "⏳ <b>Ожидайте выполнения.</b> Бот пришлет уведомление, когда подписка продлится.",
            parse_mode="HTML"
        )
    elif response and "error" in response:
        await msg.edit_text(f"❌ Ошибка: {response['error']}")
    else:
        await msg.edit_text("❌ Неизвестная ошибка.")

# --- ОПЛАТА STARS ---
@router.callback_query(F.data == "buy_stars")
async def buy_process(callback: CallbackQuery):
    await callback.message.answer_invoice(
        title="VPN Ключ (1200+ дней)",
        description="Ключ для продления VPN.",
        payload="vpn_boost_key",
        provider_token="", currency="XTR",
        prices=[LabeledPrice(label="Ключ", amount=99)]
    )

@router.pre_checkout_query()
async def on_pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)

@router.message(F.successful_payment)
async def on_success(message: Message):
    key = "BOOST-" + uuid.uuid4().hex[:8].upper()
    add_new_key(key, 1)
    await message.answer(
        f"🎉 <b>Оплата успешна!</b>\nВаш ключ:\n<code>{key}</code>\n\n1. Скопируйте.\n2. Нажмите /start -> Активировать.", 
        parse_mode="HTML"
    )

# --- ЗАПУСК ---
async def main():
    db_start()
    bot = Bot(token=TOKEN)
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Запускаем фоновую задачу мониторинга
    asyncio.create_task(order_status_monitor(bot))
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass