import telebot
import requests
import sqlite3
import json
import os
from telebot import types
from datetime import datetime
from threading import Lock
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("API_KEY")
G2BULK_API_KEY = os.getenv("G2BULK_API_KEY")
BASE_URL = os.getenv("BASE_URL")
ADMIN_ID = os.getenv("ADMIN_ID")

DEFAULT_EXCHANGE_RATE = 15000
# ============= إعداد قاعدة البيانات =============
conn = sqlite3.connect('wallet.db', check_same_thread=False)
db_lock = Lock()

def safe_db_execute(query, params=()):
    """تنفيذ استعلام آمن وإرجاع النتائج مباشرة."""
    with db_lock:
        cursor = conn.cursor()
        try:
            cursor.execute(query, params)
            conn.commit()
            return cursor.fetchall()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()


# تهيئة الجداول
safe_db_execute('''CREATE TABLE IF NOT EXISTS users
             (user_id INTEGER PRIMARY KEY, balance INTEGER DEFAULT 0)''')

safe_db_execute('''CREATE TABLE IF NOT EXISTS exchange_rate
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              rate INTEGER,
              updated_at TIMESTAMP)''')

safe_db_execute('''CREATE TABLE IF NOT EXISTS active_categories
             (category_id INTEGER PRIMARY KEY)''')

safe_db_execute('''CREATE TABLE IF NOT EXISTS bot_settings
             (key TEXT PRIMARY KEY, value TEXT)''')
safe_db_execute('''CREATE TABLE IF NOT EXISTS manual_categories
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL)''')

safe_db_execute('''CREATE TABLE IF NOT EXISTS manual_products
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              category_id INTEGER NOT NULL,
              name TEXT NOT NULL,
              price INTEGER NOT NULL,
              description TEXT,
              requires_player_id BOOLEAN DEFAULT FALSE,
              FOREIGN KEY(category_id) REFERENCES manual_categories(id))''')
safe_db_execute('''CREATE TABLE IF NOT EXISTS manual_orders
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL,
              product_id INTEGER NOT NULL,
              product_name TEXT NOT NULL,
              price INTEGER NOT NULL,
              player_id TEXT,
              status TEXT DEFAULT 'pending',
              created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
              admin_note TEXT,
              FOREIGN KEY(product_id) REFERENCES manual_products(id))''')

safe_db_execute('''CREATE TABLE IF NOT EXISTS user_orders
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL,
              order_type TEXT NOT NULL,  
              product_id INTEGER,
              product_name TEXT NOT NULL,
              price INTEGER NOT NULL,
              player_id TEXT,
              status TEXT DEFAULT 'pending',  
              created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
              admin_note TEXT)''')

safe_db_execute('''CREATE TABLE IF NOT EXISTS user_order_history
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL,
              order_id INTEGER NOT NULL,
              action TEXT NOT NULL,  
              status TEXT,
              note TEXT,
              created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
if not safe_db_execute("SELECT * FROM exchange_rate"):
    safe_db_execute("INSERT INTO exchange_rate (rate, updated_at) VALUES (?, ?)",
                    (DEFAULT_EXCHANGE_RATE, datetime.now()))

if not safe_db_execute("SELECT * FROM bot_settings WHERE key='is_paused'"):
    safe_db_execute("INSERT INTO bot_settings (key, value) VALUES ('is_paused', '0')")

if not safe_db_execute("SELECT * FROM bot_settings WHERE key='recharge_code'"):
    safe_db_execute("INSERT INTO bot_settings (key, value) VALUES ('recharge_code', 'GGSTORE123')")


bot = telebot.TeleBot(API_KEY)

# ============= وظائف المساعدة =============
def is_admin(user_id):
    return user_id == ADMIN_ID

def get_exchange_rate():
    """الحصول على سعر الصرف مع معالجة الأخطاء."""
    try:
        results = safe_db_execute("SELECT rate FROM exchange_rate ORDER BY id DESC LIMIT 1")
        return results[0][0] if results else DEFAULT_EXCHANGE_RATE
    except Exception as e:
        print(f"Error getting exchange rate: {str(e)}")
        return DEFAULT_EXCHANGE_RATE
def log_user_order(user_id, order_type, product_id, product_name, price, player_id=None):
    try:
        # تسجيل الطلب الرئيسي
        safe_db_execute(
            "INSERT INTO user_orders (user_id, order_type, product_id, product_name, price, player_id) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, order_type, product_id, product_name, price, player_id)
        )
        
        order_id = safe_db_execute("SELECT last_insert_rowid()")[0][0]
        
        # تسجيل في سجل التاريخ
        safe_db_execute(
            "INSERT INTO user_order_history (user_id, order_id, action, status) VALUES (?, ?, ?, ?)",
            (user_id, order_id, 'create', 'pending')
        )
        
        return order_id
    except Exception as e:
        print(f"Error logging order: {str(e)}")
        return None
def convert_to_syp(usd_amount):
    """تحويل مع تعزيز معالجة الأخطاء."""
    try:
        return int(float(usd_amount) * get_exchange_rate())
    except (ValueError, TypeError) as e:
        print(f"Conversion error: {str(e)}")
        raise ValueError("❌ سعر المنتج غير صالح")
    
def get_balance(user_id):
    results = safe_db_execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    return results[0][0] if results else 0

def update_balance(user_id, amount):
    try:
        # التحقق من وجود المستخدم أولاً
        safe_db_execute("INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, 0)", (user_id,))
        
        # تحديث الرصيد
        safe_db_execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
        return True
    except Exception as e:
        print(f"Error updating balance: {str(e)}")
        return False

def process_product_name_update(message, product_id):
    new_name = message.text.strip()
    if not new_name:
        bot.send_message(message.chat.id, "❌ الاسم لا يمكن أن يكون فارغًا!")
        return

    headers = {'X-API-Key': G2BULK_API_KEY}
    payload = {'title': new_name}
    response = requests.patch(
        f"{BASE_URL}products/{product_id}",
        json=payload,
        headers=headers
    )

    if response.status_code == 200:
        bot.send_message(message.chat.id, "✅ تم تحديث اسم المنتج بنجاح!")
    else:
        bot.send_message(message.chat.id, "❌ فشل في تحديث اسم المنتج!")
def log_order_status_update(order_id, new_status, admin_id=None, note=None):
    try:
        # تحديث حالة الطلب
        safe_db_execute(
            "UPDATE user_orders SET status=?, admin_note=? WHERE id=?",
            (new_status, note, order_id)
        )
        
        # الحصول على user_id من الطلب
        user_id = safe_db_execute("SELECT user_id FROM user_orders WHERE id=?", (order_id,))[0][0]
        
        # تسجيل في سجل التاريخ
        safe_db_execute(
            "INSERT INTO user_order_history (user_id, order_id, action, status, note) VALUES (?, ?, ?, ?, ?)",
            (user_id, order_id, 'status_update', new_status, note)
        )
        
        # إعلام المستخدم إذا تغيرت الحالة
        if new_status != 'pending':
            notify_user_of_status_change(user_id, order_id, new_status, note)
            
        return True
    except Exception as e:
        print(f"Error updating order status: {str(e)}")
        return False

def notify_user_of_status_change(user_id, order_id, new_status, note=None):
    try:
        order = safe_db_execute("SELECT product_name, price FROM user_orders WHERE id=?", (order_id,))[0]
        product_name, price = order
        
        if new_status == 'completed':
            message = (
                f"🎉 تم إكمال طلبك بنجاح!\n\n"
                f"🆔 رقم الطلب: {order_id}\n"
                f"📦 المنتج: {product_name}\n"
                f"💵 المبلغ: {price} ل.س\n\n"
                f"شكراً لثقتك بنا ❤️"
            )
        else:
            message = (
                f"⚠️ تم تحديث حالة طلبك\n\n"
                f"🆔 رقم الطلب: {order_id}\n"
                f"📦 المنتج: {product_name}\n"
                f"🔄 الحالة الجديدة: {'مرفوض' if new_status == 'rejected' else new_status}\n"
                f"{f'📝 الملاحظة: {note}' if note else ''}"
            )
        
        bot.send_message(user_id, message)
    except Exception as e:
        print(f"Error notifying user: {str(e)}")
def get_product_details(product_id):
    try:
        response = requests.get(
            f"{BASE_URL}products/{product_id}",
            headers={'X-API-Key': G2BULK_API_KEY},
            timeout=10
        )
        response.raise_for_status()  # يرفع استثناء إذا كانت الحالة غير 200
        
        data = response.json()
        if 'product' not in data:
            raise ValueError("استجابة API غير صالحة")
            
        product = data['product']
        product['unit_price'] = convert_to_syp(product['unit_price'])
        return product
        
    except requests.exceptions.RequestException as e:
        print(f"Error fetching product: {str(e)}")
        return None
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        print(f"Error parsing product data: {str(e)}")
        return None
def send_order_confirmation(user_id, order_id, product_name, price, player_id=None):
    """إرسال تأكيد الطلب للمستخدم"""
    try:
        message = (
            f"✅ تمت عملية الشراء بنجاح!\n\n"
            f"🆔 رقم الطلب: {order_id}\n"
            f"📦 المنتج: {product_name}\n"
            f"💵 المبلغ: {price} ل.س\n"
            f"{f'👤 معرف اللاعب: {player_id}' if player_id else ''}\n\n"
            f"شكراً لثقتك بنا ❤️"
        )
        bot.send_message(user_id, message)
    except Exception as e:
        print(f"Error sending confirmation: {str(e)}")
def notify_user_balance_update(user_id, amount, new_balance, admin_note=None):
    try:
        message = (
            f"🎉 تم تحديث رصيدك بنجاح!\n\n"
            f"💰 المبلغ المضاف: {amount} ل.س\n"
            f"💳 الرصيد الجديد: {new_balance} ل.س\n"
        )
        
        bot.send_message(user_id, message)
    except Exception as e:
        print(f"فشل في إرسال الإشعار للمستخدم {user_id}: {str(e)}")
def notify_admin(order_id, user_id, product_name, price, player_id=None):
    """إرسال إشعار للأدمن"""
    try:
        markup = types.InlineKeyboardMarkup()
        markup.row(
            types.InlineKeyboardButton("✅ إتمام الطلب", callback_data=f'complete_order_{order_id}'),
            types.InlineKeyboardButton("❌ رفض الطلب", callback_data=f'reject_order_{order_id}')
        )
        
        admin_msg = (
            f"🛒 طلب شراء جديد\n\n"
            f"🆔 رقم الطلب: {order_id}\n"
            f"👤 المستخدم: {user_id}\n"
            f"📦 المنتج: {product_name}\n"
            f"💵 المبلغ: {price} ل.س\n"
            f"{f'🎮 معرف اللاعب: {player_id}' if player_id else ''}"
        )
        bot.send_message(ADMIN_ID, admin_msg, reply_markup=markup)
    except Exception as e:
        print(f"Error notifying admin: {str(e)}")
def is_bot_paused():
    result = safe_db_execute("SELECT value FROM bot_settings WHERE key='is_paused'")
    return result[0][0] == '1' if result else False

# ============= واجهة المستخدم =============
def main_menu(user_id):

    markup = types.ReplyKeyboardMarkup(        
        resize_keyboard=True,
        is_persistent=True)

    markup.row('⚡PUBG MOBILE⚡')
    markup.row('CODES 💳', '🛍️ المنتجات اليدوية')
    markup.row('طلباتي 🗂️', 'رصيدي 💰') 
    
    if is_admin(user_id):
        markup.row('لوحة التحكم ⚙️')
    return markup
# ============= معالجة الأحداث =============
@bot.message_handler(commands=['start'])
def send_welcome(message):
    if is_bot_paused() and not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⏸️ البوت متوقف مؤقتًا.")
        return
    user_id = message.from_user.id
    update_balance(user_id, 0)
    bot.send_message(message.chat.id, "مرحبا بكم في متجر GG STORE !", reply_markup=main_menu(user_id))

@bot.message_handler(func=lambda msg: msg.text == 'رصيدي 💰')
def show_balance_handler(message):
    if is_bot_paused() and not is_admin(message.from_user.id):
        return
    try:
        user_id = message.from_user.id
        balance = get_balance(user_id)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("إعادة تعبئة الرصيد 💳", callback_data="recharge_balance"))
        bot.send_message(message.chat.id, f"رصيدك الحالي: {balance} ل.س", reply_markup=markup)
    except Exception as e:
        bot.send_message(message.chat.id, "❌ حدث خطأ!")

@bot.message_handler(func=lambda msg: msg.text == 'CODES 💳')
def show_categories_handler(message):
    if is_bot_paused() and not is_admin(message.from_user.id):
        return
    show_categories(message)
@bot.message_handler(func=lambda msg: msg.text == '🛍️ المنتجات اليدوية')
def show_manual_categories(message):
    if is_bot_paused() and not is_admin(message.from_user.id):
        return
    
    categories = safe_db_execute("SELECT id, name FROM manual_categories")
    if not categories:
        bot.send_message(message.chat.id, "⚠️ لا توجد فئات متاحة حالياً")
        return
    
    markup = types.InlineKeyboardMarkup()
    for cat_id, cat_name in categories:
        markup.add(types.InlineKeyboardButton(cat_name, callback_data=f'manual_cat_{cat_id}'))
    
    bot.send_message(message.chat.id, "اختر احد الفئات :", reply_markup=markup)
@bot.callback_query_handler(func=lambda call: call.data.startswith('manual_cat_'))
def show_manual_products(call):
    category_id = call.data.split('_')[2]
    products = safe_db_execute("SELECT id, name, price FROM manual_products WHERE category_id=?", (category_id,))
    
    if not products:
        bot.send_message(call.message.chat.id, "⚠️ لا توجد منتجات في هذه الفئة")
        return
    
    markup = types.InlineKeyboardMarkup()
    for prod_id, prod_name, prod_price in products:
        markup.add(types.InlineKeyboardButton(
            f"{prod_name} - {prod_price} ل.س",
            callback_data=f'manual_prod_{prod_id}'
        ))
    
    bot.edit_message_text(
        "اختر المنتج المطلوب :",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )
@bot.callback_query_handler(func=lambda call: call.data.startswith('manual_prod_'))
def show_manual_product_details(call):
    product_id = call.data.split('_')[2]
    product = safe_db_execute("SELECT name, price, description, requires_player_id FROM manual_products WHERE id=?", (product_id,))
    
    if not product:
        bot.send_message(call.message.chat.id, "⚠️ المنتج غير متوفر")
        return
    
    name, price, desc, requires_id = product[0]
    text = f"🛍️ {name}\n💵 السعر: {price} ل.س\n📄 الوصف: {desc or 'لا يوجد وصف'}"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("شراء الآن", callback_data=f'buy_manual_{product_id}'))
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )
@bot.message_handler(func=lambda msg: msg.text == '⚡PUBG MOBILE⚡')
def show_topup_offers_handler(message):
    if is_bot_paused() and not is_admin(message.from_user.id):
        return
    show_topup_offers(message)

@bot.message_handler(func=lambda msg: msg.text == 'لوحة التحكم ⚙️' and is_admin(msg.from_user.id))
def admin_panel_handler(message):
    show_admin_panel(message)
def manage_products(message):
    response = requests.get(f"{BASE_URL}products")
    if response.status_code == 200:
        products = response.json().get('products', [])
        markup = types.InlineKeyboardMarkup()
        for prod in products:
            markup.add(types.InlineKeyboardButton(
                f"✏️ {prod['title']}",
                callback_data=f'edit_product_{prod["id"]}'
            ))
        markup.add(types.InlineKeyboardButton("رجوع 🔙", callback_data='admin_panel'))
        bot.send_message(message.chat.id, "اختر المنتج لتعديل اسمه:", reply_markup=markup)
    else:
        bot.send_message(message.chat.id, "❌ فشل في جلب قائمة المنتجات!")
@bot.callback_query_handler(func=lambda call: call.data == 'manage_manual_categories')
def manage_manual_categories(call):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("➕ إضافة فئة جديدة", callback_data='add_manual_category'))
    
    categories = safe_db_execute("SELECT id, name FROM manual_categories")
    for cat_id, cat_name in categories:
        markup.add(types.InlineKeyboardButton(
            f"🗑️ حذف {cat_name}",
            callback_data=f'delete_manual_cat_{cat_id}'
        ))
    
    markup.add(types.InlineKeyboardButton("رجوع 🔙", callback_data='admin_panel'))
    bot.edit_message_text(
        "إدارة الفئات اليدوية:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data == 'add_manual_category')
def add_manual_category(call):
    msg = bot.send_message(call.message.chat.id, "أرسل اسم الفئة الجديدة:")
    bot.register_next_step_handler(msg, process_new_manual_category)

def process_new_manual_category(message):
    try:
        name = message.text.strip()
        if not name:
            bot.send_message(message.chat.id, "❌ اسم الفئة لا يمكن أن يكون فارغاً")
            return
            
        safe_db_execute("INSERT INTO manual_categories (name) VALUES (?)", (name,))
        bot.send_message(message.chat.id, f"✅ تمت إضافة الفئة '{name}' بنجاح")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ: {str(e)}")
@bot.callback_query_handler(func=lambda call: call.data == 'manage_manual_products')
def manage_manual_products(call):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("➕ إضافة منتج جديد", callback_data='add_manual_product'))
    markup.add(types.InlineKeyboardButton("🗑️ حذف منتج", callback_data='delete_manual_product'))
    markup.add(types.InlineKeyboardButton("رجوع 🔙", callback_data='admin_panel'))
    
    bot.edit_message_text(
        "إدارة المنتجات اليدوية:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data == 'add_manual_product')
def add_manual_product(call):
    categories = safe_db_execute("SELECT id, name FROM manual_categories")
    if not categories:
        bot.send_message(call.message.chat.id, "⚠️ لا توجد فئات متاحة، يرجى إضافة فئة أولاً")
        return
    
    markup = types.InlineKeyboardMarkup()
    for cat_id, cat_name in categories:
        markup.add(types.InlineKeyboardButton(cat_name, callback_data=f'select_cat_for_product_{cat_id}'))
    
    bot.edit_message_text(
        "اختر الفئة للمنتج الجديد:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )
@bot.callback_query_handler(func=lambda call: call.data == 'rejected_orders')
def show_rejected_orders(call):
    orders = safe_db_execute("""
        SELECT id, user_id, product_name, price, admin_note
        FROM manual_orders
        WHERE status='rejected'
        ORDER BY created_at DESC
        LIMIT 10
    """)
    
    if not orders:
        bot.send_message(call.message.chat.id, "لا توجد طلبات مرفوضة")
        return
    
    markup = types.InlineKeyboardMarkup()
    for order_id, user_id, product_name, price, note in orders:
        markup.add(types.InlineKeyboardButton(
            f"❌ {order_id}: {product_name}",
            callback_data=f'view_rejected_{order_id}'
        ))
    
    markup.add(types.InlineKeyboardButton("رجوع", callback_data='manage_manual_orders'))
    
    bot.edit_message_text(
        "الطلبات المرفوضة:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )
@bot.callback_query_handler(func=lambda call: call.data.startswith('view_rejected_'))
def view_rejected_order(call):
    order_id = call.data.split('_')[2]
    order = safe_db_execute("""
        SELECT user_id, product_name, price, admin_note, created_at
        FROM manual_orders
        WHERE id=?
    """, (order_id,))
    
    if not order:
        bot.send_message(call.message.chat.id, "الطلب غير موجود")
        return
    
    user_id, product_name, price, note, date = order[0]
    
    text = (
        f"❌ تفاصيل الطلب المرفوض\n\n"
        f"🆔 رقم الطلب: {order_id}\n"
        f"👤 المستخدم: {user_id}\n"
        f"📦 المنتج: {product_name}\n"
        f"💵 المبلغ: {price} ل.س\n"
        f"📅 التاريخ: {date}\n"
        f"📝 السبب: {note}"
    )
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id
    )
@bot.callback_query_handler(func=lambda call: call.data.startswith('select_cat_for_product_'))
def select_category_for_product(call):
    category_id = call.data.split('_')[4]
    msg = bot.send_message(call.message.chat.id, "أرسل اسم المنتج:")
    bot.register_next_step_handler(msg, process_product_name, category_id)

def process_product_name(message, category_id):
    try:
        name = message.text.strip()
        if not name:
            bot.send_message(message.chat.id, "❌ اسم المنتج لا يمكن أن يكون فارغاً")
            return
            
        message.text = name  # حفظ الاسم للخطوة التالية
        msg = bot.send_message(message.chat.id, "أرسل سعر المنتج بالليرة السورية:")
        bot.register_next_step_handler(msg, process_product_price, category_id, name)
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ: {str(e)}")

def process_product_price(message, category_id, name):
    try:
        price = int(message.text)
        if price <= 0:
            bot.send_message(message.chat.id, "❌ السعر يجب أن يكون أكبر من الصفر")
            return
            
        msg = bot.send_message(message.chat.id, "أرسل وصف المنتج (اختياري):")
        bot.register_next_step_handler(msg, process_product_description, category_id, name, price)
    except ValueError:
        bot.send_message(message.chat.id, "❌ يرجى إدخال رقم صحيح للسعر")
def send_rejection_notification(user_id, order_id, reason, refund_amount):
    try:
        # جلب تفاصيل الطلب
        order = safe_db_execute("""
            SELECT product_name 
            FROM manual_orders 
            WHERE id=?
        """, (order_id,))
        
        if order:
            product_name = order[0][0]
            
            # صياغة الرسالة
            message = (
                f"⚠️ تم رفض طلبك\n\n"
                f"🆔 رقم الطلب: {order_id}\n"
                f"📦 المنتج: {product_name}\n"
                f"💵 المبلغ المسترجع: {refund_amount} ل.س\n"
                f"📝 سبب الرفض: {reason}\n\n"
                f"للاستفسار، يرجى التواصل مع الإدارة"
            )
            
            # إرسال الرسالة
            bot.send_message(user_id, message)
            
    except Exception as e:
        print(f"فشل في إرسال إشعار الرفض: {str(e)}")
        # يمكنك إرسال رسالة إلى الأدمن هنا للإبلاغ عن الفشل
def process_product_description(message, category_id, name, price):
    description = message.text if message.text else None
    
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("نعم", callback_data=f'confirm_product_yes_{category_id}_{name}_{price}_{description}'),
        types.InlineKeyboardButton("لا", callback_data=f'confirm_product_no_{category_id}_{name}_{price}_{description}')
    )
    
    bot.send_message(
        message.chat.id,
        f"هل يطلب هذا المنتج إدخال معرف اللاعب؟",
        reply_markup=markup
    )
@bot.callback_query_handler(func=lambda call: call.data.startswith('accept_recharge_'))
def accept_recharge(call):
    try:
        parts = call.data.split('_')
        user_id = int(parts[2])
        amount = int(parts[3])
        
        # تحديث رصيد المستخدم
        update_balance(user_id, amount)
        
        # تحرير الرسالة الأصلية (إزالة الأزرار)
        try:
            if call.message.photo:  # إذا كانت الرسالة تحتوي على صورة
                bot.edit_message_caption(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    caption=f"{call.message.caption}\n\n✅ تمت الموافقة على الطلب بواسطة @{call.from_user.username}"
                )
            else:  # إذا كانت رسالة نصية
                bot.edit_message_text(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    text=f"{call.message.text}\n\n✅ تمت الموافقة على الطلب بواسطة @{call.from_user.username}"
                )
        except Exception as edit_error:
            print(f"Error editing message: {str(edit_error)}")
        
        # إعلام الأدمن
        bot.answer_callback_query(call.id, f"✅ تمت الموافقة على الطلب وإضافة {amount} ل.س")
        
        # إعلام المستخدم
        bot.send_message(
            user_id,
            f"🎉 تمت الموافقة على طلبك!\n\n"
            f"💰 تم إضافة {amount} ل.س إلى رصيدك\n"
            f"💳 رصيدك الحالي: {get_balance(user_id)} ل.س"
        )
        
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ خطأ: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('reject_recharge_'))
def reject_recharge(call):
    try:
        parts = call.data.split('_')
        user_id = int(parts[2])
        amount = int(parts[3])
        
        # تحرير الرسالة الأصلية (إزالة الأزرار)
        try:
            if call.message.photo:  # إذا كانت الرسالة تحتوي على صورة
                bot.edit_message_caption(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    caption=f"{call.message.caption}\n\n❌ تم رفض الطلب بواسطة @{call.from_user.username}"
                )
            else:  # إذا كانت رسالة نصية
                bot.edit_message_text(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    text=f"{call.message.text}\n\n❌ تم رفض الطلب بواسطة @{call.from_user.username}"
                )
        except Exception as edit_error:
            print(f"Error editing message: {str(edit_error)}")
        
        # إعلام الأدمن
        bot.answer_callback_query(call.id, "❌ تم رفض الطلب")
        
        # إعلام المستخدم
        bot.send_message(
            user_id,
            f"⚠️ تم رفض طلب تعبئة الرصيد\n\n"
            f"💰 المبلغ: {amount} ل.س\n\n"
            f"للاستفسار، يرجى التواصل مع الإدارة"
        )
        
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ خطأ: {str(e)}")
@bot.callback_query_handler(func=lambda call: call.data.startswith('confirm_product_'))
def confirm_product_requires_id(call):
    parts = call.data.split('_')
    requires_id = parts[2] == 'yes'
    category_id = parts[3]
    name = parts[4]
    price = parts[5]
    description = '_'.join(parts[6:]) if len(parts) > 6 else None
    
    if description == 'None':
        description = None
    
    try:
        safe_db_execute(
            "INSERT INTO manual_products (category_id, name, price, description, requires_player_id) VALUES (?, ?, ?, ?, ?)",
            (category_id, name, price, description, requires_id)
        )
        
        bot.send_message(call.message.chat.id, f"✅ تمت إضافة المنتج '{name}' بنجاح")
        
    except Exception as e:
        bot.send_message(call.message.chat.id, f"❌ حدث خطأ: {str(e)}")
@bot.message_handler(func=lambda msg: msg.text == 'طلباتي 🗂️')
def show_user_orders(message):
    user_id = message.from_user.id
    orders = safe_db_execute("""
        SELECT id, product_name, price, status, created_at 
        FROM user_orders 
        WHERE user_id=?
        ORDER BY created_at DESC
        LIMIT 10
    """, (user_id,))
    
    if not orders:
        bot.send_message(message.chat.id, "📭 لا توجد طلبات سابقة")
        return
    
    markup = types.InlineKeyboardMarkup()
    for order_id, product_name, price, status, created_at in orders:
        status_icon = "🟡" if status == 'pending' else "✅" if status == 'completed' else "❌"
        markup.add(types.InlineKeyboardButton(
            f"{status_icon} {product_name} - {price} ل.س ({created_at.split()[0]})",
            callback_data=f'view_my_order_{order_id}'
        ))
    
    bot.send_message(message.chat.id, "📋 طلباتك السابقة:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('view_my_order_'))
def view_user_order_details(call):
    order_id = call.data.split('_')[3]
    order = safe_db_execute("""
        SELECT product_name, price, status, created_at, player_id, admin_note
        FROM user_orders 
        WHERE id=? AND user_id=?
    """, (order_id, call.from_user.id))
    
    if not order:
        bot.send_message(call.message.chat.id, "⚠️ الطلب غير موجود")
        return
    
    product_name, price, status, created_at, player_id, admin_note = order[0]
    status_text = {
        'pending': 'قيد المعالجة 🟡',
        'completed': 'مكتمل ✅',
        'rejected': 'مرفوض ❌'
    }.get(status, status)
    
    text = (
        f"📦 تفاصيل الطلب #{order_id}\n\n"
        f"🛒 المنتج: {product_name}\n"
        f"💵 السعر: {price} ل.س\n"
        f"🔄 الحالة: {status_text}\n"
        f"📅 التاريخ: {created_at}\n"
        f"{f'👤 معرف اللاعب: {player_id}' if player_id else ''}\n"
        f"{f'📝 ملاحظة الإدارة: {admin_note}' if admin_note and status == 'rejected' else ''}"
    )
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id
    )
@bot.callback_query_handler(func=lambda call: call.data.startswith('buy_manual_'))
def handle_manual_purchase(call):
    try:
        product_id = call.data.split('_')[2]
        product = safe_db_execute("SELECT name, price, requires_player_id FROM manual_products WHERE id=?", (product_id,))
        
        if not product:
            bot.send_message(call.message.chat.id, "⚠️ المنتج غير متوفر")
            return
        
        name, price, requires_id = product[0]
        user_id = call.from_user.id
        balance = get_balance(user_id)
        
        if balance < price:
            bot.send_message(call.message.chat.id, f"⚠️ رصيدك غير كافي. السعر: {price} ل.س | رصيدك: {balance} ل.س")
            return
        
        # إذا كان المنتج يتطلب معرف لاعب
        if requires_id:
            msg = bot.send_message(call.message.chat.id, "الرجاء إدخال معرف اللاعب:")
            bot.register_next_step_handler(msg, lambda m: process_player_id_for_purchase(m, product_id, price, user_id))
        else:
            # إذا كان لا يتطلب معرف لاعب، نكمل الشراء مباشرة
            complete_manual_purchase_with_deduction(call.message, product_id, price, user_id)
            
    except Exception as e:
        bot.send_message(call.message.chat.id, f"❌ حدث خطأ: {str(e)}")

def process_player_id_for_purchase(message, product_id, price, user_id):
    try:
        player_id = message.text.strip()
        if not player_id:
            bot.send_message(message.chat.id, "❌ يجب إدخال معرف اللاعب")
            return
            
        complete_manual_purchase_with_deduction(message, product_id, price, user_id, player_id)
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ: {str(e)}")
def complete_manual_purchase_with_deduction(message, product_id, price, user_id, player_id=None):
    try:
        # خصم الرصيد أولاً
        if not update_balance(user_id, -price):
            raise Exception("فشل في خصم الرصيد")

        product_name = safe_db_execute('SELECT name FROM manual_products WHERE id=?', (product_id,))[0][0]
        
        # تسجيل الطلب في السجل
        order_id = log_user_order(
            user_id=user_id,
            order_type='manual',
            product_id=product_id,
            product_name=product_name,
            price=price,
            player_id=player_id
        )
        
        if not order_id:
            raise Exception("فشل في تسجيل الطلب")

        # إرسال التأكيدات
        send_order_confirmation(user_id, order_id, product_name, price, player_id)
        notify_admin(order_id, user_id, product_name, price, player_id)

    except Exception as e:
        # استعادة الرصيد في حالة الخطأ
        update_balance(user_id, price)
        bot.send_message(user_id, f"❌ فشلت عملية الشراء: {str(e)}")

def complete_manual_purchase(message, product_id, price, user_id=None, player_id=None):
    if user_id is None:
        user_id = message.from_user.id
        player_id = message.text.strip()
    
    try:
        # الحصول على اسم المنتج
        product_name = safe_db_execute('SELECT name FROM manual_products WHERE id=?', (product_id,))[0][0]
        
        # تسجيل الطلب في قاعدة البيانات
        safe_db_execute(
            "INSERT INTO manual_orders (user_id, product_id, product_name, price, player_id) VALUES (?, ?, ?, ?, ?)",
            (user_id, product_id, product_name, price, player_id if player_id else None)
        )
        
        # إرسال تفاصيل الطلب للمستخدم
        order_id = safe_db_execute("SELECT last_insert_rowid()")[0][0]
        bot.send_message(
            user_id,
            f"✅ تمت عملية الشراء بنجاح!\n\n"
            f"🆔 رقم الطلب: {order_id}\n"
            f"📦 المنتج: {product_name}\n"
            f"💵 المبلغ: {price} ل.س\n"
            f"{f'👤 معرف اللاعب: {player_id}' if player_id else ''}\n\n"
            f"سيتم إرسال التفاصيل قريباً"
        )
        
        # إرسال إشعار للأدمن مع زر للموافقة
        admin_markup = types.InlineKeyboardMarkup()
        admin_markup.row(
            types.InlineKeyboardButton("✅ إتمام الطلب", callback_data=f'complete_order_{order_id}'),
            types.InlineKeyboardButton("❌ رفض الطلب", callback_data=f'reject_order_{order_id}')
        )
        
        admin_msg = (
            f"🛒 طلب شراء جديد\n\n"
            f"🆔 رقم الطلب: {order_id}\n"
            f"👤 المستخدم: {user_id}\n"
            f"📦 المنتج: {product_name}\n"
            f"💵 المبلغ: {price} ل.س\n"
            f"{f'🎮 معرف اللاعب: {player_id}' if player_id else ''}"
        )
        bot.send_message(ADMIN_ID, admin_msg, reply_markup=admin_markup)
        
    except Exception as e:
        bot.send_message(user_id if user_id else message.from_user.id, f"❌ حدث خطأ: {str(e)}")
@bot.callback_query_handler(func=lambda call: call.data.startswith('delete_manual_cat_'))
def delete_manual_category(call):
    category_id = call.data.split('_')[3]
    safe_db_execute("DELETE FROM manual_categories WHERE id=?", (category_id,))
    bot.send_message(call.message.chat.id, "✅ تم حذف الفئة بنجاح")
    manage_manual_categories(call)

@bot.callback_query_handler(func=lambda call: call.data == 'delete_manual_product')
def delete_manual_product_menu(call):
    products = safe_db_execute("SELECT id, name FROM manual_products")
    if not products:
        bot.send_message(call.message.chat.id, "⚠️ لا توجد منتجات متاحة للحذف")
        return
    
    markup = types.InlineKeyboardMarkup()
    for prod_id, prod_name in products:
        markup.add(types.InlineKeyboardButton(
            f"🗑️ {prod_name}",
            callback_data=f'delete_manual_prod_{prod_id}'
        ))
    
    bot.edit_message_text(
        "اختر المنتج للحذف:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('delete_manual_prod_'))
def delete_manual_product(call):
    product_id = call.data.split('_')[3]
    safe_db_execute("DELETE FROM manual_products WHERE id=?", (product_id,))
    bot.send_message(call.message.chat.id, "✅ تم حذف المنتج بنجاح")
    manage_manual_products(call)

@bot.callback_query_handler(func=lambda call: call.data == 'cancel_recharge_code_edit')
def handle_cancel_recharge_code_edit(call):
    try:
        bot.send_message(
            call.message.chat.id,
            "تم إلغاء تعديل كود الشحن",
            reply_markup=main_menu(call.from_user.id)
        )
    except Exception as e:
        bot.send_message(call.message.chat.id, f"❌ حدث خطأ: {str(e)}")
@bot.callback_query_handler(func=lambda call: call.data == 'manage_manual_orders')
def manage_manual_orders(call):
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("📋 الطلبات المعلقة", callback_data='pending_orders'),
        types.InlineKeyboardButton("✅ الطلبات المكتملة", callback_data='completed_orders')
    )
    markup.row(
        types.InlineKeyboardButton("❌ الطلبات المرفوضة", callback_data='rejected_orders'),
        types.InlineKeyboardButton("🔍 بحث عن طلب", callback_data='search_order')
    )
    markup.add(types.InlineKeyboardButton("رجوع 🔙", callback_data='admin_panel'))
    
    bot.edit_message_text(
        "إدارة الطلبات اليدوية:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data == 'pending_orders')
def show_pending_orders(call):
    orders = safe_db_execute("""
        SELECT id, user_id, product_name, price, created_at 
        FROM manual_orders 
        WHERE status='pending'
        ORDER BY created_at DESC
    """)
    
    if not orders:
        bot.send_message(call.message.chat.id, "⚠️ لا توجد طلبات معلقة")
        return
    
    markup = types.InlineKeyboardMarkup()
    for order_id, user_id, product_name, price, created_at in orders:
        markup.add(types.InlineKeyboardButton(
            f"🆔{order_id}: {product_name} - {price} ل.س",
            callback_data=f'view_order_{order_id}'
        ))
    
    markup.add(types.InlineKeyboardButton("رجوع 🔙", callback_data='manage_manual_orders'))
    bot.edit_message_text(
        "الطلبات المعلقة:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('view_order_'))
def view_order_details(call):
    order_id = call.data.split('_')[2]
    order = safe_db_execute("""
        SELECT user_id, product_name, price, player_id, created_at, status, admin_note
        FROM manual_orders 
        WHERE id=?
    """, (order_id,))
    
    if not order:
        bot.send_message(call.message.chat.id, "⚠️ الطلب غير موجود")
        return
    
    user_id, product_name, price, player_id, created_at, status, admin_note = order[0]
    status_icon = "🟡" if status == 'pending' else "✅" if status == 'completed' else "❌"
    
    text = (
        f"{status_icon} تفاصيل الطلب 🆔{order_id}\n\n"
        f"👤 المستخدم: {user_id}\n"
        f"📦 المنتج: {product_name}\n"
        f"💵 المبلغ: {price} ل.س\n"
        f"📅 التاريخ: {created_at}\n"
        f"🔄 الحالة: {status}\n"
        f"{f'🎮 معرف اللاعب: {player_id}' if player_id else ''}\n"
        f"{f'📝 ملاحظة الأدمن: {admin_note}' if admin_note else ''}"
    )
    
    markup = types.InlineKeyboardMarkup()
    if status == 'pending':
        markup.row(
            types.InlineKeyboardButton("✅ إتمام الطلب", callback_data=f'complete_order_{order_id}'),
            types.InlineKeyboardButton("❌ رفض الطلب", callback_data=f'reject_order_{order_id}')
        )
    markup.add(types.InlineKeyboardButton("📩 إرسال تفاصيل للمستخدم", callback_data=f'send_order_details_{order_id}'))
    markup.add(types.InlineKeyboardButton("رجوع 🔙", callback_data='pending_orders'))
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )
@bot.callback_query_handler(func=lambda call: call.data.startswith('complete_order_'))
def complete_order(call):
    order_id = call.data.split('_')[2]
    if log_order_status_update(order_id, 'completed', call.from_user.id, "تمت الموافقة من الأدمن"):
        bot.answer_callback_query(call.id, "✅ تم إتمام الطلب")
    else:
        bot.answer_callback_query(call.id, "❌ فشل في إتمام الطلب")

@bot.callback_query_handler(func=lambda call: call.data.startswith('reject_order_'))
def reject_order(call):
    order_id = call.data.split('_')[2]
    msg = bot.send_message(call.message.chat.id, "أرسل سبب الرفض:")
    bot.register_next_step_handler(msg, process_reject_reason, order_id)

def process_reject_reason(message, order_id):
    try:
        reason = message.text if message.text else "لا يوجد سبب محدد"
        
        # تحديث حالة الطلب في قاعدة البيانات
        safe_db_execute("""
            UPDATE manual_orders 
            SET status='rejected', admin_note=?
            WHERE id=?
        """, (reason, order_id))
        
        # استعادة الرصيد للمستخدم
        order = safe_db_execute("""
            SELECT user_id, price 
            FROM manual_orders 
            WHERE id=?
        """, (order_id,))
        
        if order:
            user_id, price = order[0]
            update_balance(user_id, price)
            
            # إرسال إشعار للمستخدم
            send_rejection_notification(user_id, order_id, reason, price)
            
        bot.send_message(
            message.chat.id,
            f"✅ تم رفض الطلب رقم {order_id} واستعادة الرصيد للمستخدم"
        )
        
    except Exception as e:
        bot.send_message(
            message.chat.id,
            f"❌ حدث خطأ أثناء رفض الطلب: {str(e)}"
        )

@bot.callback_query_handler(func=lambda call: call.data.startswith('send_order_details_'))
def send_order_details_to_user(call):
    order_id = call.data.split('_')[3]
    order = safe_db_execute("""
        SELECT user_id, product_name, price, player_id, status, admin_note
        FROM manual_orders 
        WHERE id=?
    """, (order_id,))
    
    if not order:
        bot.send_message(call.message.chat.id, "⚠️ الطلب غير موجود")
        return
    
    user_id, product_name, price, player_id, status, admin_note = order[0]
    
    if status == 'completed':
        message_text = (
            f"🎉 تم إتمام طلبك بنجاح!\n\n"
            f"🆔 رقم الطلب: {order_id}\n"
            f"📦 المنتج: {product_name}\n"
            f"💵 المبلغ: {price} ل.س\n"
            f"{f'🎮 معرف اللاعب: {player_id}' if player_id else ''}\n\n"
            f"شكراً لثقتك بنا ❤️"
        )
    elif status == 'rejected':
        message_text = (
            f"⚠️ تم رفض طلبك\n\n"
            f"🆔 رقم الطلب: {order_id}\n"
            f"📦 المنتج: {product_name}\n"
            f"💵 المبلغ: {price} ل.س (تم استعادة الرصيد)\n"
            f"📝 السبب: {admin_note or 'لا يوجد سبب محدد'}\n\n"
            f"للاستفسار، يرجى التواصل مع الإدارة"
        )
    else:
        message_text = (
            f"⏳ طلبك قيد المعالجة\n\n"
            f"🆔 رقم الطلب: {order_id}\n"
            f"📦 المنتج: {product_name}\n"
            f"💵 المبلغ: {price} ل.س\n"
            f"{f'🎮 معرف اللاعب: {player_id}' if player_id else ''}\n\n"
            f"سيتم إعلامك فور اكتمال الطلب"
        )
    
    try:
        bot.send_message(user_id, message_text)
        bot.answer_callback_query(call.id, "✅ تم إرسال التفاصيل للمستخدم")
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ فشل في الإرسال: {str(e)}")
@bot.callback_query_handler(func=lambda call: call.data == 'search_order')
def search_order(call):
    msg = bot.send_message(call.message.chat.id, "أرسل رقم الطلب أو جزء من اسم المنتج:")
    bot.register_next_step_handler(msg, process_order_search)

def process_order_search(message):
    search_term = message.text.strip()
    if not search_term:
        bot.send_message(message.chat.id, "❌ يرجى إدخال مصطلح البحث")
        return
    
    try:
        # البحث برقم الطلب إذا كان رقماً
        if search_term.isdigit():
            orders = safe_db_execute("""
                SELECT id, user_id, product_name, price, status 
                FROM manual_orders 
                WHERE id=?
                ORDER BY created_at DESC
            """, (int(search_term),))
        else:
            # البحث باسم المنتج
            orders = safe_db_execute("""
                SELECT id, user_id, product_name, price, status 
                FROM manual_orders 
                WHERE product_name LIKE ?
                ORDER BY created_at DESC
                LIMIT 10
            """, (f"%{search_term}%",))
        
        if not orders:
            bot.send_message(message.chat.id, "⚠️ لا توجد نتائج مطابقة للبحث")
            return
        
        markup = types.InlineKeyboardMarkup()
        for order_id, user_id, product_name, price, status in orders:
            status_icon = "🟡" if status == 'pending' else "✅" if status == 'completed' else "❌"
            markup.add(types.InlineKeyboardButton(
                f"{status_icon} {order_id}: {product_name} - {price} ل.س",
                callback_data=f'view_order_{order_id}'
            ))
        
        bot.send_message(message.chat.id, "نتائج البحث:", reply_markup=markup)
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ: {str(e)}")

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    
    if is_bot_paused() and not is_admin(call.from_user.id):
        return
    data = call.data
    user_id = call.from_user.id
    message = call.message
    if data.startswith('category_'):
        category_id = data.split('_')[1]
        show_products(call.message, category_id)
    elif data.startswith('product_'):
        product_id = data.split('_')[1]
        show_product_details(call.message, product_id)
    elif data.startswith('buy_'):
        product_id = data.split('_')[1]
        msg = bot.send_message(call.message.chat.id, "⏳ الرجاء إدخال الكمية المطلوبة:")
        bot.register_next_step_handler(msg, process_purchase_quantity, product_id)
    elif data == 'edit_balance' and is_admin(user_id):
        msg = bot.send_message(call.message.chat.id, "أرسل آيدي المستخدم والمبلغ (مثال: 123456789 50000):")
        bot.register_next_step_handler(msg, process_balance_update)
    elif data == 'list_users' and is_admin(user_id):
        show_all_users(call.message)
    elif data == 'edit_exchange_rate' and is_admin(user_id):
        msg = bot.send_message(call.message.chat.id, "أرسل سعر الصرف الجديد:")
        bot.register_next_step_handler(msg, process_exchange_rate_update)
    elif data.startswith('topup_'):
        offer_id = data.split('_')[1]
        msg = bot.send_message(call.message.chat.id, " الرجاء إدخال رقم اللاعب:")
        bot.register_next_step_handler(msg, process_topup_purchase, offer_id)
    elif data == 'recharge_balance':
        handle_recharge_request(call.message)
    elif data == 'toggle_bot' and is_admin(user_id):
        toggle_bot_status(call.message)
    elif data == 'manage_categories' and is_admin(user_id):
        manage_categories(call.message)
    elif data.startswith('toggle_category_'):
        category_id = data.split('_')[2]
        toggle_category_status(call.message, category_id)
    elif data == 'edit_category_names' and is_admin(user_id):  # معالج الزر الجديد
        show_editable_categories(call.message)
    elif data.startswith('edit_product_') and is_admin(user_id):
        product_id = data.split('_')[2]
        msg = bot.send_message(call.message.chat.id, "أرسل الاسم الجديد للمنتج:")
        bot.register_next_step_handler(msg, process_product_name_update, product_id)
    elif data == 'edit_products' and is_admin(user_id):
        manage_products(message)
    elif data == 'edit_recharge_msg' and is_admin(user_id):
        msg = bot.send_message(call.message.chat.id, "أرسل الرسالة الجديدة لإعادة الشحن:")
        bot.register_next_step_handler(msg, update_recharge_message)
    elif data == 'edit_category_names' and is_admin(user_id):
        show_editable_categories(message)
    elif data.startswith('edit_catname_') and is_admin(user_id):
        category_id = data.split('_')[1]
        msg = bot.send_message(
            message.chat.id,
            "✏️ أرسل الاسم الجديد للفئة:",
            reply_markup=types.ForceReply(selective=True)
        )
        bot.register_next_step_handler(msg, process_category_name_update, category_id)
    elif data == 'cancel_edit' and is_admin(user_id):
        bot.send_message(
            message.chat.id,
            "تم إلغاء التعديل",
            reply_markup=main_menu(user_id)
        )
    elif data == 'edit_recharge_code' and is_admin(user_id):
    # إضافة زر الإلغاء مباشرة في الرسالة
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("إلغاء ❌", callback_data='cancel_recharge_code_edit'))
    
        msg = bot.send_message(
        message.chat.id, 
        "أرسل كود الشحن الجديد:",
        reply_markup=markup
        )
        bot.register_next_step_handler(msg, process_recharge_code_update)
    elif data == 'cancel_recharge_code_edit' and is_admin(user_id):
        bot.send_message(
        message.chat.id,
        "تم إلغاء تعديل كود الشحن",
        reply_markup=main_menu(user_id)
        )
def ask_transaction_id(message, amount):
    try:
        user_id = message.from_user.id
        username = message.from_user.username or "بدون معرف"
        
        # التحقق مما إذا كان المستخدم أرسل صورة أو نص
        if message.photo:
            # إذا كانت صورة
            file_id = message.photo[-1].file_id
            proof_type = "صورة الإشعار"
            proof_content = file_id
        elif message.text:
            # إذا كان نص (رقم العملية)
            proof_type = "رقم العملية"
            proof_content = message.text.strip()
            if not proof_content:
                raise ValueError("يجب إدخال رقم العملية أو إرسال صورة")
        else:
            raise ValueError("يجب إرسال رقم العملية أو صورة الإشعار")

        # إرسال طلب التحقق للأدمن
        notify_admin_recharge_request(user_id, username, amount, proof_type, proof_content)
        
        # إرسال تأكيد للمستخدم
        confirmation_msg = (
            f"✅ تم استلام طلبك بنجاح!\n\n"
            f"👤 المعرف: @{username}\n"
            f"💰 المبلغ: {amount} ل.س\n"
            f"📝 نوع الإثبات: {proof_type}\n\n"
            f"سيتم مراجعة الطلب من قبل الإدارة"
        )
        
        # إذا كانت صورة، نرسلها للمستخدم كتأكيد
        if message.photo:
            bot.send_photo(
                message.chat.id,
                file_id,
                caption=confirmation_msg,
                reply_markup=main_menu(user_id)
            )
        else:
            bot.send_message(
                message.chat.id,
                confirmation_msg,
                reply_markup=main_menu(user_id)
            )
            
    except Exception as e:
        bot.send_message(
            message.chat.id,
            f"❌ حدث خطأ: {str(e)}\nيرجى المحاولة مرة أخرى",
            reply_markup=main_menu(message.from_user.id)
        )
def process_category_name_update(message, category_id):
    try:
        new_name = message.text.strip()
        if not new_name:
            bot.send_message(message.chat.id, "❌ الاسم لا يمكن أن يكون فارغًا!")
            show_editable_categories(message)  # إعادة عرض القائمة
            return

        headers = {'X-API-Key': G2BULK_API_KEY}
        payload = {'title': new_name}
        response = requests.patch(
            f"{BASE_URL}category/{category_id}",
            json=payload,
            headers=headers
        )

        if response.status_code == 200:
            bot.send_message(message.chat.id, "✅ تم تحديث اسم الفئة بنجاح!")
        else:
            bot.send_message(message.chat.id, "❌ فشل في تحديث اسم الفئة!")

        show_editable_categories(message)  # العودة إلى قائمة الفئات بعد التعديل

    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ: {str(e)}")
        show_editable_categories(message)  # العودة إلى القائمة حتى في حالة الخطأ
def show_editable_categories(message):
    response = requests.get(f"{BASE_URL}category")
    if response.status_code == 200:
        categories = response.json().get('categories', [])
        markup = types.InlineKeyboardMarkup()
        for cat in categories:
            markup.add(types.InlineKeyboardButton(
                f"✏️ {cat['title']}",  # رمز القلم للتعديل
                callback_data=f'edit_catname_{cat["id"]}'
            ))
        markup.add(types.InlineKeyboardButton("رجوع 🔙", callback_data='admin_panel'))  # للعودة إلى لوحة التحكم
        bot.send_message(message.chat.id, "اختر الفئة لتعديل اسمها:", reply_markup=markup)
    else:
        bot.send_message(message.chat.id, "❌ فشل في جلب قائمة الفئات!")
        markup.add(types.InlineKeyboardButton("رجوع 🔙", callback_data='admin_panel'))  # للعودة إلى لوحة التحكم

def show_products(message, category_id):
    response = requests.get(f"{BASE_URL}category/{category_id}")
    if response.status_code == 200:
        products = response.json().get('products', [])
        products = sorted(products, key=lambda x: convert_to_syp(x['unit_price']))
        
        markup = types.InlineKeyboardMarkup()
        for prod in products:
            if prod['stock'] > 0:
                price_syp = convert_to_syp(prod['unit_price'])
                markup.add(types.InlineKeyboardButton(
                    f"{prod['title']} - {price_syp} ل.س",
                    callback_data=f'product_{prod["id"]}'
                ))
        bot.send_message(message.chat.id, "المنتجات المتاحة (مرتبة من الأقل سعراً):", reply_markup=markup)
def show_categories(message):
    active_categories = safe_db_execute("SELECT category_id FROM active_categories")
    active_ids = [cat[0] for cat in active_categories] if active_categories else []
    
    response = requests.get(f"{BASE_URL}category")
    if response.status_code == 200:
        categories = response.json().get('categories', [])
        markup = types.InlineKeyboardMarkup()
        for cat in categories:
            if cat['id'] in active_ids:
                markup.add(types.InlineKeyboardButton(cat['title'], callback_data=f'category_{cat["id"]}'))
        bot.send_message(message.chat.id, "اختر فئة:", reply_markup=markup)

def show_products(message, category_id):
    response = requests.get(f"{BASE_URL}category/{category_id}")
    if response.status_code == 200:
        products = response.json().get('products', [])
        products = sorted(products, key=lambda x: convert_to_syp(x['unit_price']))
        
        markup = types.InlineKeyboardMarkup()
        for prod in products:
            if prod['stock'] > 0:
                price_syp = convert_to_syp(prod['unit_price'])
                markup.add(types.InlineKeyboardButton(
                    f"{prod['title']} - {price_syp} ل.س",
                    callback_data=f'product_{prod["id"]}'
                ))
        bot.send_message(message.chat.id, "المنتجات المتاحة:", reply_markup=markup)
def process_purchase_quantity(message, product_id):
    try:
        user_id = message.from_user.id
        quantity = int(message.text.strip())
        
        if quantity <= 0:
            bot.send_message(message.chat.id, "❌ الكمية يجب أن تكون أكبر من الصفر!")
            return
            
        product = get_product_details(product_id)
        if not product:
            bot.send_message(message.chat.id, "❌ المنتج غير متوفر!")
            return
            
        total_price = product['unit_price'] * quantity
        
        if get_balance(user_id) < total_price:
            bot.send_message(message.chat.id, "⚠️ رصيدك غير كافي!")
            return
            
        # تنفيذ عملية الشراء
        headers = {'X-API-Key': G2BULK_API_KEY}
        response = requests.post(
            f"{BASE_URL}products/{product_id}/purchase",
            json={"quantity": quantity},
            headers=headers
        )
        
        if response.status_code == 200:
            update_balance(user_id, -total_price)
            order_details = response.json()
            delivery_items = "\n".join(order_details["delivery_items"])
            bot.send_message(
                message.chat.id,
                f"✅ تمت العملية بنجاح!\nرقم الطلب: {order_details['order_id']}\n"
                f"الأكواد:\n"
                f"<code>{delivery_items}</code>",
                parse_mode='HTML'
            )
        else:
            error_msg = response.json().get('message', 'فشلت عملية الشراء')
            bot.send_message(message.chat.id, f"❌ {error_msg}")
            
    except ValueError:
        bot.send_message(message.chat.id, "❌ يرجى إدخال رقم صحيح!")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ غير متوقع: {str(e)}")
def show_product_details(message, product_id):
    product = get_product_details(product_id)
    if product:
        text = f"""
        🛒 المنتج: {product['title']}
        💵 السعر: {product['unit_price']} ل.س
        📦 المخزون: {product['stock']}
        """
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("شراء 🛒", callback_data=f"buy_{product['id']}"))
        bot.send_message(message.chat.id, text, reply_markup=markup)
def show_admin_panel(message):
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton('تعديل رصيد مستخدم', callback_data='edit_balance'),
        types.InlineKeyboardButton('عرض المستخدمين', callback_data='list_users')
    )
    markup.row(
        types.InlineKeyboardButton('تعديل سعر الصرف', callback_data='edit_exchange_rate'),
        types.InlineKeyboardButton('إدارة الفئات', callback_data='manage_categories')
    )
    markup.row(
        types.InlineKeyboardButton('تعديل أسماء الفئات', callback_data='edit_category_names'),
        types.InlineKeyboardButton('تعديل كود الشحن', callback_data='edit_recharge_code')  # تغيير هنا
    )
    markup.row(
        types.InlineKeyboardButton('إدارة الفئات اليدوية', callback_data='manage_manual_categories'),
        types.InlineKeyboardButton('إدارة المنتجات اليدوية', callback_data='manage_manual_products')
    )
    markup.row(
        types.InlineKeyboardButton('إدارة الطلبات اليدوية', callback_data='manage_manual_orders')
    )
    markup.row(
        types.InlineKeyboardButton('إيقاف/تشغيل البوت', callback_data='toggle_bot')
    )
    bot.send_message(message.chat.id, "⚙️ لوحة التحكم الإدارية:", reply_markup=markup)

def show_editable_categories(message):
    response = requests.get(f"{BASE_URL}category")
    if response.status_code == 200:
        categories = response.json().get('categories', [])
        markup = types.InlineKeyboardMarkup(row_width=2)
        
        for cat in categories:
            markup.add(
                types.InlineKeyboardButton(
                    f"✏️ {cat['title']}",
                    callback_data=f'edit_catname_{cat["id"]}'
                )
            )
        
        # إضافة أزرار التنقل
        markup.row(
            types.InlineKeyboardButton("رجوع 🔙", callback_data='admin_panel'),
            types.InlineKeyboardButton("إلغاء ❌", callback_data='cancel_edit')
        )
        
        bot.send_message(
            message.chat.id,
            "📁 اختر الفئة لتعديل اسمها:",
            reply_markup=markup
        )
    else:
        bot.send_message(message.chat.id, "❌ فشل في جلب قائمة الفئات!")
def process_recharge_code_update(message):
    try:
        if message.text == '❌ إلغاء ❌':
            bot.send_message(
                message.chat.id,
                "تم إلغاء تعديل كود الشحن",
                reply_markup=main_menu(message.from_user.id)
            )
            return
            
        new_code = message.text.strip()
        if not new_code:
            bot.send_message(message.chat.id, "❌ الكود لا يمكن أن يكون فارغًا!")
            return
            
        safe_db_execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
                       ('recharge_code', new_code))
        
        # جلب الكود الجديد للتأكيد
        result = safe_db_execute("SELECT value FROM bot_settings WHERE key='recharge_code'")
        current_code = result[0][0] if result else "غير محدد"
        
        # إرسال الرسالة بدون زر النسخ
        bot.send_message(
            message.chat.id,
            f"✅ تم تحديث كود الشحن بنجاح!\n\n"
            f"كود الشحن الحالي:\n"
            f"<code>{current_code}</code>",
            parse_mode='HTML'
        )
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ: {str(e)}")
def show_editable_categories(message):
    response = requests.get(f"{BASE_URL}category")
    if response.status_code == 200:
        categories = response.json().get('categories', [])
        markup = types.InlineKeyboardMarkup()
        for cat in categories:
            markup.add(types.InlineKeyboardButton(
                f"✏️ {cat['title']}",  # رمز القلم للتعديل
                callback_data=f'edit_catname_{cat["id"]}'
            ))
        markup.add(types.InlineKeyboardButton("رجوع 🔙", callback_data='admin_panel'))  # للعودة إلى لوحة التحكم
        bot.send_message(message.chat.id, "اختر الفئة لتعديل اسمها:", reply_markup=markup)
    else:
        bot.send_message(message.chat.id, "❌ فشل في جلب قائمة الفئات!")


def handle_recharge_request(message):
    try:
        # جلب كود الشحن من الإعدادات
        result = safe_db_execute("SELECT value FROM bot_settings WHERE key='recharge_code'")
        recharge_code = result[0][0] if result else "GGSTORE123"
        
        # إنشاء الرسالة مع تعليمات واضحة
        recharge_msg = (
            " لتعبة رصيدك، يرجى اتباع الخطوات التالية:\n\n"
            f" قم بإرسال المبلغ إلى كود السيريتل كاش: <code>{recharge_code}</code>\n\n"
            " أرسل المبلغ المرسل :\n"
        )
        
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        markup.row('❌ إلغاء ❌')
        
        msg = bot.send_message(
            message.chat.id, 
            recharge_msg,
            parse_mode='HTML',
            reply_markup=markup
        )
        
        # ننتقل لخطوة طلب المبلغ
        bot.register_next_step_handler(msg, ask_recharge_amount)
        
    except Exception as e:
        bot.send_message(message.chat.id, "❌ حدث خطأ في معالجة الطلب!", reply_markup=main_menu(message.from_user.id))
def ask_recharge_amount(message):
    if message.text == '❌ إلغاء ❌':
        bot.send_message(message.chat.id, "تم إلغاء العملية", reply_markup=main_menu(message.from_user.id))
        return
    
    try:
        # التحقق من أن الرسالة تحتوي على رقم صحيح موجب
        amount = int(message.text)
        if amount <= 0:
            raise ValueError
        
        # طلب رقم العملية أو الصورة
        msg = bot.send_message(
            message.chat.id,
            f"💰 المبلغ المرسل: {amount} ل.س\n\n"
            "أدخل رقم العملية أو سكرين شوت للتحويل :\n"

            "⚠️ يرجى التأكد من وضوح الصورة قبل ارسالها",
            parse_mode='Markdown',
            reply_markup=types.ReplyKeyboardRemove()
        )
        
        # ننتقل لخطوة طلب الإثبات مع حفظ المبلغ
        bot.register_next_step_handler(msg, ask_transaction_id, amount)
        
    except ValueError:
        msg = bot.send_message(
            message.chat.id,
            "❌ يرجى إدخال مبلغ صحيح أكبر من الصفر!\n"
            "مثال: 50000",
            reply_markup=types.ReplyKeyboardRemove()
        )
        bot.register_next_step_handler(msg, ask_recharge_amount)
def notify_admin_recharge_request(user_id, username, amount, proof_type, proof_content):
    try:
        markup = types.InlineKeyboardMarkup()
        markup.row(
            types.InlineKeyboardButton("✅ قبول الطلب", callback_data=f'accept_recharge_{user_id}_{amount}'),
            types.InlineKeyboardButton("❌ رفض الطلب", callback_data=f'reject_recharge_{user_id}_{amount}')
        )
        
        admin_msg = (
            f"🔄 طلب تعبئة رصيد جديد\n\n"
            f"👤 آيدي المستخدم: {user_id}\n"
            f"👤 معرف المستخدم: @{username}\n"
            f"💰 المبلغ المرسل: {amount} ل.س\n"
            f"📝 نوع الإثبات: {proof_type}\n"
        )

        # إرسال الصورة مع الأزرار في نفس الرسالة (إذا كانت صورة)
        if proof_type == "صورة الإشعار":
            bot.send_photo(
                ADMIN_ID,
                proof_content,
                caption=f"{admin_msg}\n🖼️ تم إرسال صورة الإشعار\n\nالرجاء التحقق:",
                reply_markup=markup
            )
        else:
            # إرسال الرسالة النصية مع الأزرار (إذا كان رقم عملية)
            full_msg = f"{admin_msg}\n🔢 رقم العملية: {proof_content}\n\nالرجاء التحقق:"
            bot.send_message(
                ADMIN_ID,
                full_msg,
                reply_markup=markup
            )
            
    except Exception as e:
        print(f"Error notifying admin: {str(e)}")
        try:
            bot.send_message(
                ADMIN_ID,
                f"🔄 طلب تعبئة رصيد جديد (حدث خطأ في العرض)\n\n"
                f"👤 المستخدم: {user_id}\n"
                f"💰 المبلغ: {amount} ل.س\n"
                f"📝 نوع الإثبات: {proof_type}",
                reply_markup=markup
            )
        except Exception as e2:
            print(f"Failed to send fallback notification: {str(e2)}")
def process_recharge_proof(message):
    try:
        # التحقق إذا كان المستخدم ضغط على زر الإلغاء
        if message.text == '❌ إلغاء ❌':
            bot.send_message(
                message.chat.id,
                "مرحبا بكم في متجر GG STORE !",
                reply_markup=main_menu(message.from_user.id)
            )
            return
        if message.text == '/start':
            bot.send_message(
                message.chat.id,
                "مرحبا بكم في متجر GG STORE !",
                reply_markup=main_menu(message.from_user.id)
            ) 
            return
            
        user_id = message.from_user.id
        username = message.from_user.username or "بدون معرف"
        
        if message.photo:  # إذا كانت صورة
            file_id = message.photo[-1].file_id
            file_info = bot.get_file(file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            
            # إرسال الإشعار إلى الأدمن
            bot.send_photo(
                ADMIN_ID, 
                downloaded_file, 
                caption=f"طلب إعادة شحن جديد\n\nآيدي المستخدم: {user_id}\nالمعرف: @{username}"
            )
            
        elif message.text:  # إذا كان رقم عملية
            bot.send_message(
                ADMIN_ID, 
                f"طلب إعادة شحن جديد\n\nآيدي المستخدم: {user_id}\nالمعرف: @{username}\nرقم العملية: {message.text}"
            )
            
        # إرسال تأكيد للمستخدم مع إزالة لوحة الأزرار
        bot.send_message(
            message.chat.id, 
            "✅ تم استلام طلبك بنجاح وسيتم معالجته قريبًا.",
            reply_markup=main_menu(message.from_user.id)
        )
        
    except Exception as e:
        bot.send_message(
            message.chat.id, 
            "❌ حدث خطأ في معالجة الطلب!",
            reply_markup=main_menu(message.from_user.id)
        )

def update_recharge_message(message):
    try:
        new_message = message.text.strip()
        safe_db_execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
                       ('recharge_message', new_message))
        bot.send_message(message.chat.id, "✅ تم تحديث رسالة إعادة الشحن بنجاح!")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ: {str(e)}")

def show_product_details(message, product_id):
    product = get_product_details(product_id)
    if product:
        text = f"""
        🛒 المنتج: {product['title']}
        💵 السعر: {product['unit_price']} ل.س
        📦 المخزون: {product['stock']}
        """
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("شراء 🛒", callback_data=f"buy_{product['id']}"))
        bot.send_message(message.chat.id, text, reply_markup=markup)

def show_topup_offers(message):
    """عرض العروض مع تحسين معالجة الاستجابة."""
    try:
        print(f"Requesting URL: {BASE_URL}topup/pubgMobile/offers")
        response = requests.get(
            f"{BASE_URL}topup/pubgMobile/offers",
            headers={'X-API-Key': G2BULK_API_KEY},
            timeout=10
        )
        
        print(f"Response Status: {response.status_code}")
        print(f"Response Content: {response.text[:200]}...")  # طباعة جزء من الاستجابة
        
        if response.status_code != 200:
            bot.send_message(message.chat.id, "⚠️ الخدمة غير متاحة حالياً. يرجى المحاولة لاحقاً.")
            return

        try:
            data = response.json()
            offers = data.get('offers', [])
        except json.JSONDecodeError as e:
            print(f"JSON Decode Error: {str(e)}")
            bot.send_message(message.chat.id, "❌ خطأ في تنسيق البيانات المردودة!")
            return

        if not offers:
            bot.send_message(message.chat.id, "⚠️ لا توجد عروض متاحة حالياً.")
            return

        markup = types.InlineKeyboardMarkup()
        for offer in sorted(offers, key=lambda x: convert_to_syp(x.get('unit_price', 0))):
            if offer.get('stock', 0) > 0:
                try:
                    price_syp = convert_to_syp(offer['unit_price'])
                    btn_text = f"{offer['title']} - {price_syp} ل.س"
                    markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"topup_{offer['id']}"))
                except Exception as e:
                    print(f"Skipping invalid offer: {str(e)}")
                    continue

        bot.send_message(message.chat.id, "🎮 عروض التعبئة المتاحة حالياً:", reply_markup=markup)

    except requests.exceptions.RequestException as e:
        print(f"Network Error: {str(e)}")
        bot.send_message(message.chat.id, "❌ تعذر الاتصال بالخادم!")
    except Exception as e:
        print(f"Unexpected Error: {str(e)}")
        bot.send_message(message.chat.id, "❌ حدث خطأ غير متوقع!")

# ============= وظائف الإدارة =============
def process_balance_update(message):
    try:
        parts = message.text.split()
        if len(parts) != 2:
            raise ValueError("صيغة غير صحيحة")
        user_id = int(parts[0])
        amount = int(parts[1])
        
        # تحديث الرصيد
        success = update_balance(user_id, amount)
        
        if success:
            # الحصول على الرصيد الجديد
            new_balance = get_balance(user_id)
            
            # إرسال تنبيه للمستخدم
            try:
                notify_user_balance_update(user_id, amount, new_balance, "تم التعديل من قبل الإدارة")
            except Exception as e:
                print(f"فشل في إرسال الإشعار للمستخدم: {str(e)}")
            
            bot.send_message(message.chat.id, f"✅ تم تحديث رصيد المستخدم {user_id} بمقدار {amount} ل.س")
        else:
            bot.send_message(message.chat.id, "❌ فشل في تحديث الرصيد (قد يكون الرصيد غير كافي للخصم)")
            
    except ValueError:
        bot.send_message(message.chat.id, "❌ يرجى إدخال رقم صحيح!")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ خطأ: {str(e)}")
def show_all_users(message):
    try:
        users = safe_db_execute("SELECT * FROM users")
        response = "📊 قائمة المستخدمين:\n\n"
        for user in users:
            response += f"▫️ آيدي: {user[0]}\n▫️ الرصيد: {user[1]} ل.س\n\n"
        bot.send_message(message.chat.id, response)
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ: {str(e)}")

def process_exchange_rate_update(message):
    try:
        new_rate = int(message.text)
        safe_db_execute("INSERT INTO exchange_rate (rate, updated_at) VALUES (?, ?)",
                        (new_rate, datetime.now()))
        bot.send_message(message.chat.id, f"✅ تم تحديث سعر الصرف إلى {new_rate} ليرة/دولار")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ خطأ: {str(e)}")

def toggle_bot_status(message):
    current_status = is_bot_paused()
    new_status = '0' if current_status else '1'
    safe_db_execute("UPDATE bot_settings SET value=? WHERE key='is_paused'", (new_status,))
    status_msg = "⏸️ تم إيقاف البوت مؤقتًا" if new_status == '1' else "▶️ تم تشغيل البوت"
    bot.send_message(message.chat.id, status_msg)

def manage_categories(message):
    response = requests.get(f"{BASE_URL}category")
    if response.status_code == 200:
        categories = response.json().get('categories', [])
        markup = types.InlineKeyboardMarkup()
        for cat in categories:
            is_active = safe_db_execute("SELECT 1 FROM active_categories WHERE category_id=?", (cat['id'],))
            status = "✅" if is_active else "❌"
            markup.add(types.InlineKeyboardButton(
                f"{status} {cat['title']}",
                callback_data=f"toggle_category_{cat['id']}"
            ))
        bot.send_message(message.chat.id, "تفعيل/إلغاء الفئات:", reply_markup=markup)
def process_category_name_update(message, category_id):
    new_name = message.text.strip()
    if not new_name:
        bot.send_message(message.chat.id, "❌ الاسم لا يمكن أن يكون فارغًا!")
        return

    headers = {'X-API-Key': G2BULK_API_KEY}
    payload = {'title': new_name}
    response = requests.patch(
        f"{BASE_URL}category/{category_id}",
        json=payload,
        headers=headers
    )

    if response.status_code == 200:
        bot.send_message(message.chat.id, "✅ تم تحديث اسم الفئة بنجاح!")
    else:
        bot.send_message(message.chat.id, "❌ فشل في تحديث اسم الفئة!")

    show_editable_categories(message)  # العودة إلى قائمة الفئات القابلة للتعديل
def toggle_category_status(message, category_id):
    if safe_db_execute("SELECT 1 FROM active_categories WHERE category_id=?", (category_id,)):
        safe_db_execute("DELETE FROM active_categories WHERE category_id=?", (category_id,))
        action = "❌ تم إلغاء تفعيل الفئة"
    else:
        safe_db_execute("INSERT INTO active_categories (category_id) VALUES (?)", (category_id,))
        action = "✅ تم تفعيل الفئة"
    bot.send_message(message.chat.id, action)
    manage_categories(message)

# ============= معالجة الشراء =============
def process_topup_purchase(message, offer_id):
    try:
        user_id = message.from_user.id
        player_id = message.text.strip()

        # التحقق من رقم اللاعب (8-12 رقمًا)
        if not (player_id.isdigit() and 8 <= len(player_id) <= 12):
            bot.send_message(message.chat.id, "❌ رقم اللاعب غير صالح! يجب أن يحتوي على 8 إلى 12 رقمًا فقط.")
            return

        headers = {
            'X-API-Key': G2BULK_API_KEY,
            'Content-Type': 'application/json'
        }

        # 1. جلب تفاصيل جميع العروض أولاً
        try:
            print("🔍 جلب قائمة العروض...")
            offers_response = requests.get(
                "https://api.g2bulk.com/v1/topup/pubgMobile/offers",
                headers=headers,
                timeout=10
            )

            # تسجيل الاستجابة للأغراض التشخيصية
            print(f"📊 استجابة العروض: {offers_response.status_code} - {offers_response.text[:200]}...")

            if offers_response.status_code != 200:
                error_msg = f"كود الخطأ: {offers_response.status_code}"
                try:
                    error_data = offers_response.json()
                    error_msg = error_data.get('message', error_msg)
                except:
                    pass
                bot.send_message(message.chat.id, f"❌ فشل في جلب العروض: {error_msg}")
                return

            try:
                offers_data = offers_response.json()
                if not isinstance(offers_data, dict) or not offers_data.get('success', False):
                    bot.send_message(message.chat.id, "❌ استجابة غير صالحة من الخادم")
                    return

                # البحث عن العرض المحدد
                offer = None
                for item in offers_data.get('offers', []):
                    if str(item.get('id')) == str(offer_id):
                        offer = item
                        break

                if not offer:
                    bot.send_message(message.chat.id, "❌ لم يتم العثور على العرض المحدد")
                    return

                # التحقق من البيانات الأساسية
                required_fields = ['id', 'title', 'unit_price', 'stock']
                if not all(field in offer for field in required_fields):
                    bot.send_message(message.chat.id, "❌ بيانات العرض ناقصة")
                    return

                # التحقق من المخزون
                if int(offer.get('stock', 0)) <= 0:
                    bot.send_message(message.chat.id, "⚠️ هذا العرض غير متوفر حالياً")
                    return

                # التحويل الآمن للسعر
                try:
                    price_syp = convert_to_syp(float(offer['unit_price']))
                except (ValueError, TypeError):
                    bot.send_message(message.chat.id, "❌ سعر العرض غير صالح")
                    return

                # التحقق من الرصيد
                current_balance = get_balance(user_id)
                if current_balance < price_syp:
                    bot.send_message(message.chat.id, 
                                   f"⚠️ الرصيد المطلوب: {price_syp} ل.س\nرصيدك الحالي: {current_balance} ل.س")
                    return

            except Exception as e:
                print(f"📛 خطأ في معالجة العرض: {str(e)}")
                bot.send_message(message.chat.id, "❌ خطأ في معالجة بيانات العرض")
                return

        except requests.exceptions.RequestException as e:
            print(f"📛 خطأ اتصال: {str(e)}")
            bot.send_message(message.chat.id, "❌ تعذر الاتصال بخادم العروض")
            return

        # 2. تنفيذ عملية الشراء
        try:
            print(f"🛒 محاولة شراء العرض {offer_id}...")
            purchase_data = {
                "quantity": 1,
                "player_id": player_id
            }

            purchase_response = requests.post(
                f"https://api.g2bulk.com/v1/topup/pubgMobile/offers/{offer_id}/purchase",
                json=purchase_data,
                headers=headers,
                timeout=15
            )

            # تسجيل استجابة الشراء
            print(f"📦 استجابة الشراء: {purchase_response.status_code} - {purchase_response.text[:200]}...")

            if purchase_response.status_code == 200:
                try:
                    result = purchase_response.json()
                    if not isinstance(result, dict) or not result.get('success', False):
                        raise ValueError(result.get('message', 'فشلت العملية'))

                    # العمليات الناجحة
                    update_balance(user_id, -price_syp)
                    
                    # رسالة التأكيد
                    confirmation = (
                        f"✅ تمت عملية الشراء بنجاح!\n\n"
                        f"📌 العرض: {offer['title']}\n"
                        f"👤 رقم اللاعب: {player_id}\n"
                        f"💳 المبلغ: {price_syp} ل.س\n"
                        f"🆔 رقم العملية: {result.get('topup_id', 'غير متوفر')}\n\n"
                        f"📝 ملاحظة: {result.get('message', 'سيتم إرسال التفاصيل قريباً')}"
                    )
                    bot.send_message(message.chat.id, confirmation)

                    # إشعار الأدمن
                    admin_msg = (
                        f"🛒 عملية شراء جديدة\n\n"
                        f"👤 المستخدم: {user_id}\n"
                        f"🎮 العرض: {offer['title']}\n"
                        f"🆔 اللاعب: {player_id}\n"
                        f"💰 المبلغ: {price_syp} ل.س\n"
                        f"📌 رقم العملية: {result.get('topup_id', 'غير متوفر')}"
                    )
                    bot.send_message(ADMIN_ID, admin_msg)

                except json.JSONDecodeError:
                    bot.send_message(message.chat.id, "⚠️ تمت العملية ولكن مع استجابة غير واضحة")
                except Exception as e:
                    bot.send_message(message.chat.id, f"⚠️ تمت العملية مع ملاحظة: {str(e)}")
            else:
                error_msg = "خطأ غير معروف"
                try:
                    error_data = purchase_response.json()
                    error_msg = error_data.get('message', str(purchase_response.text[:200]))
                except:
                    error_msg = purchase_response.text[:200] or f"كود الخطأ: {purchase_response.status_code}"
                
                bot.send_message(message.chat.id, f"❌ فشلت العملية: {error_msg}")

        except requests.exceptions.Timeout:
            bot.send_message(message.chat.id, "⏳ انتهى وقت الانتظار. يرجى التحقق من حالة الطلب لاحقاً.")
        except requests.exceptions.RequestException as e:
            bot.send_message(message.chat.id, f"🌐 خطأ في الاتصال: {str(e)}")
        except Exception as e:
            bot.send_message(message.chat.id, f"⚠️ خطأ غير متوقع: {str(e)}")

    except Exception as e:
        bot.send_message(message.chat.id, "🔥 حدث خطأ حرج! يرجى إبلاغ الإدارة.")
        print(f"🔥 خطأ حرج: {str(e)}")
def _extract_error_message(self, response):
    """استخراج رسالة الخطأ من الاستجابة"""
    try:
        error_data = response.json()
        if isinstance(error_data, dict):
            return error_data.get('message', 
                               error_data.get('error', 
                                           response.text[:200] or f"كود الخطأ: {response.status_code}"))
        return response.text[:200] or f"كود الخطأ: {response.status_code}"
    except:
        return response.text[:200] or f"كود الخطأ: {response.status_code}"
    
def handle_purchase(message, product_id, quantity):
    user_id = message.from_user.id
    product = get_product_details(product_id)
    total_price = product['unit_price'] * quantity
    
    if get_balance(user_id) < total_price:
        bot.send_message(message.chat.id, "⚠️ رصيدك غير كافي!")
        return
    
    headers = {'X-API-Key': G2BULK_API_KEY}
    try:
        response = requests.post(
            f"{BASE_URL}products/{product_id}/purchase",
            json={"quantity": quantity},
            headers=headers
        )
        if response.status_code == 200:
            update_balance(user_id, -total_price)
            order_details = response.json()
            
            # إنشاء نص المفاتيح مع تنسيق الكود
            delivery_items = "\n".join([
                f"<code>{item}</code>" 
                for item in order_details["delivery_items"]
            ])
            
            # إرسال الرسالة مع تنسيق HTML
            bot.send_message(
                message.chat.id,
                f"✅ تمت العملية بنجاح!\n\n"
                f"🆔 رقم الطلب: <code>{order_details['order_id']}</code>\n\n"
                f"🔑 المفاتيح:\n{delivery_items}",
                parse_mode='HTML'
            )
        else:
            bot.send_message(message.chat.id, "❌ فشلت عملية الشراء!")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ غير متوقع: {str(e)}")

# ============= تشغيل البوت =============
if __name__ == "__main__":
    bot.polling()
