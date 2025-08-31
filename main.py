import telebot
import requests
import sqlite3
import time
import json
import os
import shutil
import uuid
import threading
import html
from telebot import types
from datetime import datetime
from threading import Lock
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("API_KEY")
ADMIN_ID = 5134156042 # تأكد من أن هذا هو ID الأدمن الخاص بك
FREE_FIRE_NEW_API_BASE = os.getenv("FREE_FIRE_NEW_API_BASE")
FREE_FIRE_NEW_API_KEY = os.getenv("FREE_FIRE_NEW_API_KEY")
G2BULK_API_KEY = os.getenv("G2BULK_API_KEY")
BASE_URL = os.getenv("BASE_URL")
FREE_FIRE2_API_KEY = os.getenv("FREE_FIRE2_API_KEY")
FREE_FIRE2_BASE_URL = os.getenv("FREE_FIRE2_BASE_URL")
DEFAULT_EXCHANGE_RATE = 15000

FREE_FIRE_NEW_PRODUCTS = {
    1: {"item_id": "1", "name": "110 Diamonds", "price_usd": 0.78},
    2: {"item_id": "2", "name": "341 Diamonds", "price_usd": 2.34},
    3: {"item_id": "3", "name": "572 Diamonds", "price_usd": 3.9},
    4: {"item_id": "4", "name": "1166 Diamonds", "price_usd": 7.8},
    5: {"item_id": "5", "name": "2398 Diamonds", "price_usd": 15.6},
    6: {"item_id": "6", "name": "Weekly Membership", "price_usd": 1.6},
    7: {"item_id": "7", "name": "Monthly Membership", "price_usd": 5.5},
}
FREE_FIRE2_PRODUCTS = []
PUBG_OFFERS = []
LAST_PUBG_UPDATE = None
PUBG_UPDATE_INTERVAL = 900  # 15 دقيقة بالثواني
PUBG_MANUAL_CATEGORY_ID = 20
FREE_FIRE_MANUAL_CATEGORY_ID = 13


# ================================
# متغيرات جديدة لآلية التهدئة والقفل
last_callback_time = {} # {user_id: last_timestamp}
CALLBACK_COOLDOWN = 1.5 # ثانية واحدة ونصف كفترة تهدئة لجميع الكولباكات

# قفل لمنع العمليات المتزامنة لنفس المستخدم (خاص بعمليات الشراء الحساسة)
user_processing_lock = {} # {user_id: True/False}
# ================================

def update_pubg_offers():
    global PUBG_OFFERS, LAST_PUBG_UPDATE
    try:
        response = requests.get(
            f"{BASE_URL}topup/pubgMobile/offers",
            headers={'X-API-Key': G2BULK_API_KEY},
            timeout=15
        )
        if response.status_code == 200:
            PUBG_OFFERS = response.json().get('offers', [])
            LAST_PUBG_UPDATE = time.time()
            print("تم تحديث عروض PUBG Mobile بنجاح")
        else:
            print(f"فشل في تحديث عروض PUBG Mobile. كود الخطأ: {response.status_code}")
    except Exception as e:
        print(f"خطأ في تحديث عروض PUBG Mobile: {str(e)}")

update_pubg_offers()

def periodic_pubg_update():
    while True:
        time.sleep(PUBG_UPDATE_INTERVAL)
        update_pubg_offers()

update_thread = threading.Thread(target=periodic_pubg_update)
update_thread.daemon = True
update_thread.start()

# ============= إعداد قاعدة البيانات =============
conn = sqlite3.connect('wallet.db', check_same_thread=False)
db_lock = Lock()

def safe_db_execute(query, params=()):
    """تنفيذ استعلام آمن مع التحقق من أنواع البيانات"""
    with db_lock:
        cursor = conn.cursor()
        try:
            processed_params = []
            for param in params:
                if isinstance(param, (list, dict)):
                    processed_params.append(str(param))
                elif param is None:
                    processed_params.append(None)
                else:
                    processed_params.append(param)
            cursor.execute(query, processed_params)
            conn.commit()
            return cursor.fetchall()
        except Exception as e:
            conn.rollback()
            print(f"Database error: {str(e)}")
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
              name TEXT NOT NULL,
              is_active BOOLEAN DEFAULT TRUE)''')
safe_db_execute('''CREATE TABLE IF NOT EXISTS freefire_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                offer_id INTEGER NOT NULL,
                player_id TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
safe_db_execute('''CREATE TABLE IF NOT EXISTS manual_products
                (id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                description TEXT,
                requires_player_id BOOLEAN DEFAULT FALSE,
                is_active BOOLEAN DEFAULT TRUE,
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
                status TEXT DEFAULT 'completed', 
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                api_response TEXT,
                admin_note TEXT)''')
safe_db_execute('''CREATE TABLE IF NOT EXISTS user_order_history
                (id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                order_id INTEGER NOT NULL,
                action TEXT NOT NULL,  
                status TEXT,
                note TEXT,
                admin_note,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
safe_db_execute('''CREATE TABLE IF NOT EXISTS payment_methods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                type TEXT NOT NULL, -- 'daily_limit_syp', 'unlimited_syp', 'foreign_currency'
                instructions TEXT, -- رسالة التعليمات التي تظهر للمستخدم
                is_active BOOLEAN DEFAULT TRUE
                )''')

# جدول جديد لعناوين الدفع المرتبطة بكل طريقة
safe_db_execute('''CREATE TABLE IF NOT EXISTS payment_addresses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                method_id INTEGER NOT NULL,
                address TEXT NOT NULL, -- الرقم أو العنوان
                currency TEXT DEFAULT 'SYP',
                exchange_rate REAL, -- سعر الصرف الخاص بهذه العملة فقط
                daily_limit INTEGER, -- الحد اليومي بالليرة السورية بعد التحويل
                daily_used INTEGER DEFAULT 0,
                last_reset_date TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                FOREIGN KEY(method_id) REFERENCES payment_methods(id) ON DELETE CASCADE
                )''')

safe_db_execute('''CREATE TABLE IF NOT EXISTS recharge_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount_syp INTEGER NOT NULL, -- المبلغ بالليرة دائماً
                address_id INTEGER NOT NULL, -- الربط مع عنوان الدفع المستخدم
                transaction_id TEXT,
                proof_type TEXT,
                proof_content TEXT,
                status TEXT DEFAULT 'pending', -- pending, pending_admin, completed, rejected, cancelled
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(address_id) REFERENCES payment_addresses(id)
            )''')
safe_db_execute('''CREATE TABLE IF NOT EXISTS disabled_buttons
             (button_name TEXT PRIMARY KEY,
              is_disabled BOOLEAN DEFAULT FALSE)''')
if not safe_db_execute("SELECT * FROM bot_settings WHERE key='recharge_disabled'"):
    safe_db_execute("INSERT INTO bot_settings (key, value) VALUES ('recharge_disabled', '0')")

safe_db_execute('''CREATE TABLE IF NOT EXISTS admins
             (admin_id INTEGER PRIMARY KEY,
              username TEXT,
              added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
if not safe_db_execute("SELECT * FROM bot_settings WHERE key='recharge_disabled'"):
    safe_db_execute("INSERT INTO bot_settings (key, value) VALUES ('recharge_disabled', '0')")

if not safe_db_execute("SELECT * FROM admins WHERE admin_id=?", (ADMIN_ID,)):
    safe_db_execute("INSERT INTO admins (admin_id) VALUES (?)", (ADMIN_ID,))
if not safe_db_execute("SELECT * FROM exchange_rate"):
    safe_db_execute("INSERT INTO exchange_rate (rate, updated_at) VALUES (?, ?)",
                    (DEFAULT_EXCHANGE_RATE, datetime.now()))
if not safe_db_execute("SELECT * FROM bot_settings WHERE key='is_paused'"):
    safe_db_execute("INSERT INTO bot_settings (key, value) VALUES ('is_paused', '0')")
if not safe_db_execute("SELECT * FROM bot_settings WHERE key='recharge_code'"):
    safe_db_execute("INSERT INTO bot_settings (key, value) VALUES ('recharge_code', 'GGSTORE123')")
if not safe_db_execute("SELECT * FROM bot_settings WHERE key='channel_id'"):
    safe_db_execute("INSERT INTO bot_settings (key, value) VALUES ('channel_id', '')")

bot = telebot.TeleBot(API_KEY)

def ensure_manual_tables_updated():
    try:
        # التحقق من جدول الفئات
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(manual_categories)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'is_active' not in columns:
            safe_db_execute("ALTER TABLE manual_categories ADD COLUMN is_active BOOLEAN DEFAULT TRUE")
            print("تمت إضافة العمود is_active إلى جدول manual_categories")

        # التحقق من جدول المنتجات
        cursor.execute("PRAGMA table_info(manual_products)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'is_active' not in columns:
            safe_db_execute("ALTER TABLE manual_products ADD COLUMN is_active BOOLEAN DEFAULT TRUE")
            print("تمت إضافة العمود is_active إلى جدول manual_products")

    except Exception as e:
        print(f"خطأ في تحديث جداول المنتجات اليدوية: {str(e)}")
    finally:
        cursor.close()

ensure_manual_tables_updated()
# ============= إضافة الدوال للنسخ الاحتياطي والاستعادة =============

def upgrade_database_schema():
    """
    تضمن هذه الدالة أن هيكل قاعدة البيانات محدّث.
    تقوم بإضافة الجداول الجديدة وحذف الجداول القديمة.
    """
    print("بدء عملية ترقية بنية قاعدة البيانات...")
    try:
        # 1. إضافة الجداول الجديدة إذا لم تكن موجودة
        safe_db_execute('''CREATE TABLE IF NOT EXISTS payment_methods (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL UNIQUE,
                        type TEXT NOT NULL,
                        instructions TEXT,
                        is_active BOOLEAN DEFAULT TRUE
                        )''')
        safe_db_execute('''CREATE TABLE IF NOT EXISTS payment_addresses (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        method_id INTEGER NOT NULL,
                        address TEXT NOT NULL,
                        currency TEXT DEFAULT 'SYP',
                        exchange_rate REAL,
                        daily_limit INTEGER,
                        daily_used INTEGER DEFAULT 0,
                        last_reset_date TEXT,
                        is_active BOOLEAN DEFAULT TRUE,
                        FOREIGN KEY(method_id) REFERENCES payment_methods(id) ON DELETE CASCADE
                        )''')
        safe_db_execute('''CREATE TABLE IF NOT EXISTS banned_users (
                        user_id INTEGER PRIMARY KEY,
                        banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )''')

        # 2. حذف جدول recharge_codes القديم إذا كان موجودًا
        safe_db_execute("DROP TABLE IF EXISTS recharge_codes")
        
        # 3. إعادة إنشاء جدول recharge_requests بالهيكل الصحيح
        # نحذفه أولاً لضمان عدم وجود تعارض في الأعمدة ثم ننشئه من جديد
        safe_db_execute("DROP TABLE IF EXISTS recharge_requests")
        safe_db_execute('''CREATE TABLE IF NOT EXISTS recharge_requests (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        amount_syp INTEGER NOT NULL,
                        address_id INTEGER NOT NULL,
                        transaction_id TEXT,
                        proof_type TEXT,
                        proof_content TEXT,
                        status TEXT DEFAULT 'pending',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(address_id) REFERENCES payment_addresses(id)
                    )''')
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(payment_methods)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'min_amount' not in columns:
            safe_db_execute("ALTER TABLE payment_methods ADD COLUMN min_amount INTEGER DEFAULT 0")
            print("تمت إضافة عمود min_amount إلى جدول payment_methods")
        
        print("✅ تمت ترقية بنية قاعدة البيانات بنجاح.")
        return True
    except Exception as e:
        print(f"❌ فشلت عملية الترقية: {e}")
        return False
def ensure_columns_exist():
    try:
        columns = safe_db_execute("PRAGMA table_info(recharge_requests)")
        existing_columns = [col[1] for col in columns]
        if 'code_id' not in existing_columns:
            safe_db_execute("ALTER TABLE recharge_requests ADD COLUMN code_id INTEGER")
            print("تمت إضافة العمود code_id إلى جدول recharge_requests")
        if 'proof_type' not in existing_columns:
            safe_db_execute("ALTER TABLE recharge_requests ADD COLUMN proof_type TEXT")
            print("تمت إضافة العمود proof_type إلى جدول recharge_requests")
        if 'proof_content' not in existing_columns:
            safe_db_execute("ALTER TABLE recharge_requests ADD COLUMN proof_content TEXT")
            print("تمت إضافة العمود proof_content إلى جدول recharge_requests")
    except Exception as e:
        print(f"خطأ في التحقق من الأعمدة: {str(e)}")

ensure_columns_exist()

def close_db_connection():
    """إغلاق اتصالات قاعدة البيانات بشكل آمن"""
    global conn
    if conn:
        conn.close()
        conn = None

@bot.callback_query_handler(func=lambda call: call.data == 'backup_db')
def backup_database(call):
    try:
        close_db_connection()
        backup_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        temp_backup_name = f"temp_backup_{backup_time}.db"
        shutil.copyfile('wallet.db', temp_backup_name)
        global conn
        conn = sqlite3.connect('wallet.db', check_same_thread=False)
        with open(temp_backup_name, 'rb') as f:
            bot.send_document(
                chat_id=ADMIN_ID,
                document=f,
                caption=f'🔐 Backup: {backup_time}',
                timeout=30
            )
        os.remove(temp_backup_name)
        bot.answer_callback_query(call.id, "✅ تم إنشاء النسخة الاحتياطية")
    except Exception as e:
        print(f"Backup Error: {str(e)}")
        bot.answer_callback_query(call.id, f"❌ فشل النسخ الاحتياطي: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data == 'restore_db')
def restore_database(call):
    try:
        msg = bot.send_message(
            call.message.chat.id,
            "📤 أرسل ملف النسخة الاحتياطية (يجب أن يكون بصيغة .db):",
            reply_markup=types.ForceReply(selective=True)
        )
        bot.register_next_step_handler(msg, process_restore)
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ خطأ: {str(e)}")

def process_restore(message):
    try:
        if not message.document or not message.document.file_name.endswith('.db'):
            bot.send_message(message.chat.id, "❌ ملف غير صالح! يجب أن يكون بصيغة .db")
            return

        # 1. إغلاق الاتصال الحالي وتحميل الملف الجديد
        close_db_connection()
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        temp_name = f"restore_temp_{datetime.now().strftime('%Y%m%d%H%M%S')}.db"
        with open(temp_name, 'wb') as f:
            f.write(downloaded_file)
        
        # 2. استبدال قاعدة البيانات الحالية بالنسخة الاحتياطية
        shutil.move(temp_name, 'wallet.db')
        bot.send_message(message.chat.id, "⏳ تم استلام النسخة الاحتياطية، جاري تحديث الهيكل...")

        # 3. إعادة الاتصال بقاعدة البيانات الجديدة (المستعادة)
        global conn
        conn = sqlite3.connect('wallet.db', check_same_thread=False)
        
        # ================== الخطوة الأهم ==================
        # 4. استدعاء دالة الترقية لتحديث بنية قاعدة البيانات المستعادة
        if upgrade_database_schema():
            bot.send_message(message.chat.id, "✅ تمت استعادة النسخة الاحتياطية وتحديث هيكلها بنجاح!")
        else:
            bot.send_message(message.chat.id, "⚠️ تمت استعادة النسخة، لكن ربما حدث خطأ أثناء ترقية الهيكل. يرجى مراجعة السجلات.")
        # ===============================================

    except sqlite3.DatabaseError as e:
        bot.send_message(message.chat.id, f"❌ ملف قاعدة بيانات تالف: {str(e)}")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ فشلت عملية الاستعادة: {str(e)}")
    finally:
        # التأكد من إعادة الاتصال أو حذف الملف المؤقت في حال حدوث خطأ
        if 'conn' not in globals() or conn is None:
            conn = sqlite3.connect('wallet.db', check_same_thread=False)
        if 'temp_name' in locals() and os.path.exists(temp_name):
            os.remove(temp_name)

# ============= وظائف المساعدة =============
def is_admin(user_id):
    try:
        return bool(safe_db_execute("SELECT 1 FROM admins WHERE admin_id=?", (user_id,)))
    except Exception as e:
        print(f"Error checking admin status: {str(e)}")
        return user_id == ADMIN_ID

def get_notification_channel():
    try:
        result = safe_db_execute("SELECT value FROM bot_settings WHERE key='channel_id'")
        return result[0][0] if result else None
    except Exception as e:
        print(f"Error getting channel ID: {str(e)}")
        return None

def get_exchange_rate():
    """الحصول على سعر الصرف مع معالجة الأخطاء."""
    try:
        results = safe_db_execute("SELECT rate FROM exchange_rate ORDER BY id DESC LIMIT 1")
        return results[0][0] if results else DEFAULT_EXCHANGE_RATE
    except Exception as e:
        print(f"Error getting exchange rate: {str(e)}")
        return DEFAULT_EXCHANGE_RATE

def is_button_disabled(button_name):
    result = safe_db_execute("SELECT is_disabled FROM disabled_buttons WHERE button_name=?", (button_name,))
    return result and result[0][0] == 1

def log_user_order(user_id, order_type, product_id, product_name, price, player_id=None, api_response=None):
    try:
        api_response_json = json.dumps(api_response) if api_response else None
        columns = ["user_id", "order_type", "product_id", "product_name", "price", "player_id", "status"]
        placeholders = ["?", "?", "?", "?", "?", "?", "'completed'"]
        values = [user_id, order_type, product_id, product_name, price, player_id]
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(user_orders)")
        columns_info = cursor.fetchall()
        has_api_response = any(col[1] == 'api_response' for col in columns_info)
        if has_api_response:
            columns.append("api_response")
            placeholders.append("?")
            values.append(api_response_json)
        query = f"""INSERT INTO user_orders 
                  ({', '.join(columns)}) 
                  VALUES ({', '.join(placeholders)})"""
        safe_db_execute(query, tuple(values))
        return safe_db_execute("SELECT last_insert_rowid()")[0][0]
    except Exception as e:
        print(f"Error logging order: {str(e)}")
        return None

def convert_to_syp(usd_amount):
    """تحويل من الدولار إلى الليرة مع تقريب لأقرب 100"""
    try:
        raw = float(usd_amount) * get_exchange_rate()
        rounded = int(round(raw / 100.0)) * 100
        return rounded
    except (ValueError, TypeError) as e:
        print(f"Conversion error: {str(e)}")
        raise ValueError("❌ سعر المنتج غير صالح")

def get_balance(user_id):
    results = safe_db_execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    return results[0][0] if results else 0

def generate_order_id():
    return f"FF-{uuid.uuid4().hex[:12].upper()}"

def update_balance(user_id, amount):
    try:
        safe_db_execute("INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, 0)", (user_id,))
        safe_db_execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
        return True
    except Exception as e:
        print(f"Error updating balance: {str(e)}")
        return False

def skip_product_description(message, category_id, name, price):
    """تخطي إدخال وصف المنتج والمتابعة مباشرة"""
    try:
        safe_db_execute(
            "INSERT INTO manual_products (category_id, name, price, requires_player_id) VALUES (?, ?, ?, 0)",
            (category_id, name, price)
        )
        bot.send_message(message.chat.id, f"✅ تمت إضافة المنتج '{name}' بنجاح بدون وصف")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ: {str(e)}")

def ensure_user_orders_columns():
    required_columns = {
        'api_response': 'TEXT',
        'admin_note': 'TEXT'
    }
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(user_orders)")
        existing_columns = {col[1]: col[2] for col in cursor.fetchall()}
        for col_name, col_type in required_columns.items():
            if col_name not in existing_columns:
                print(f"Adding missing column {col_name} to user_orders table")
                safe_db_execute(f"ALTER TABLE user_orders ADD COLUMN {col_name} {col_type}")
    except Exception as e:
        print(f"Error ensuring user_orders columns: {str(e)}")

ensure_user_orders_columns()

def update_freefire2_products():
    global FREE_FIRE2_PRODUCTS
    try:
        headers = {'X-API-Key': FREE_FIRE2_API_KEY}
        response = requests.get(
            f"{FREE_FIRE2_BASE_URL}game/freefire/offers",
            headers=headers,
            timeout=10
        )
        if response.status_code == 200:
            FREE_FIRE2_PRODUCTS = response.json().get('data', [])
        else:
            print(f"فشل في جلب المنتجات. كود الخطأ: {response.status_code}")
    except Exception as e:
        print(f"خطأ في تحديث المنتجات: {str(e)}")

#update_freefire2_products() # لا تستدعيها هنا، دعها تحدث عند الحاجة أو في خيط منفصل

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
        safe_db_execute(
            "UPDATE user_orders SET status=?, admin_note=? WHERE id=?",
            (new_status, note, order_id)
        )
        user_id = safe_db_execute("SELECT user_id FROM user_orders WHERE id=?", (order_id,))[0][0]
        safe_db_execute(
            "INSERT INTO user_order_history (user_id, order_id, action, status, note) VALUES (?, ?, ?, ?, ?)",
            (user_id, order_id, 'status_update', new_status, note)
        )
        return True
    except Exception as e:
        print(f"Error updating order status: {str(e)}")

def get_freefire2_offers():
    try:
        headers = {'X-API-Key': FREE_FIRE2_API_KEY}
        response = requests.get(
            f"{FREE_FIRE2_BASE_URL}game/freefire/offers",
            headers=headers,
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            return data.get('data', [])
        return []
    except Exception as e:
        print(f"Error fetching Free Fire 2 offers: {str(e)}")
        return []

def notify_user_of_status_change(user_id, order_id, new_status, note=None):
    try:
        order = safe_db_execute("""
            SELECT product_name, price, order_type, player_id
            FROM user_orders 
            WHERE id=?
        """, (order_id,))[0]
        product_name, price, order_type, player_id = order
        order_type_icon = {
            'manual': '🛍️',
            'pubg': '⚡',
            'freefire': '🔥',
            'freefire2': '🔥'
        }.get(order_type, '📦')
        status_msgs = {
            'completed': f'{order_type_icon} تم إكمال طلبك بنجاح 🎉',
            'rejected': '❌ تم رفض طلبك'
        }
        message = (
            f"{status_msgs.get(new_status, new_status)}\n\n"
            f"🆔 رقم الطلب: {order_id}\n"
            f"📦 المنتج: {product_name}\n"
            f"💵 المبلغ: {price} ل.س\n"
            f"{f'🎮 معرف اللاعب: {player_id}' if player_id else ''}\n"
            f"{f'📝 الملاحظة: {note}' if note else ''}"
        )
        markup = None
        if new_status == 'rejected':
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("📞 التواصل مع الدعم", url="https://t.me/GG_STORE_SUPPORT"))
        bot.send_message(
            user_id, 
            message,
            reply_markup=markup
        )
    except Exception as e:
        print(f"Error notifying user {user_id}: {str(e)}")
        bot.send_message(
            ADMIN_ID,
            f"⚠️ فشل في إرسال إشعار إلى المستخدم {user_id} عن الطلب {order_id}"
        )

def get_product_details(product_id):
    try:
        response = requests.get(
            f"{BASE_URL}products/{product_id}",
            headers={'X-API-Key': G2BULK_API_KEY},
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        if 'product' not in data:
            raise ValueError("استجابة API غير صالحة")
        product = data['product']
        # تأكد أن التحويل يتم بعد استلام السعر الأصلي
        product['unit_price_syp'] = convert_to_syp(product['unit_price'])
        return product
    except requests.exceptions.RequestException as e:
        print(f"Error fetching product: {str(e)}")
        return None
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        print(f"Error parsing product data: {str(e)}")
        return None

def send_order_confirmation(user_id, order_id, product_name, price, player_id=None):
    """إرسال تأكيد الطلب للمستخدم مع إظهار القائمة الرئيسية"""
    try:
        message = (
            f"✅ تمت عملية الشراء بنجاح!\n\n"
            f"🆔 رقم الطلب: {order_id}\n"
            f"📦 المنتج: {product_name}\n"
            f"💵 المبلغ: {price} ل.س\n"
            f"{f'👤 معرف اللاعب: {player_id}' if player_id else ''}\n\n"
            f"طلبك قيد المعالجة من قبل الإدارة وسيتم إعلامك عند اكتماله."
        )
        # تمت إضافة reply_markup لإظهار القائمة الرئيسية
        bot.send_message(user_id, message, reply_markup=main_menu(user_id))
    except Exception as e:
        print(f"Error sending confirmation: {str(e)}")

def notify_user_balance_update(user_id, amount, new_balance, admin_note=None):
    try:
        if amount > 0:
            message = (
                f"🎉 تم تحديث رصيدك بنجاح!\n\n"
                f"💰 المبلغ المضاف: {amount} ل.س\n"
                f"💳 الرصيد الجديد: {new_balance} ل.س\n"
            )
        else:
            message = (
                f"⚠️ تم خصم مبلغ من رصيدك\n\n"
                f"💰 المبلغ المخصوم: {abs(amount)} ل.س\n"
                f"💳 الرصيد الجديد: {new_balance} ل.س\n"
            )
        if admin_note:
            message += f"\n📝 ملاحظة الإدارة: {admin_note}"
        bot.send_message(user_id, message)
    except Exception as e:
        print(f"فشل في إرسال الإشعار للمستخدم {user_id}: {str(e)}")

def notify_admin(order_id, user, product_name, price, player_id=None, order_type=None):
    try:
        # --- بداية التعديل ---
        
        # 1. استخلاص بيانات المستخدم وإنشاء الرابط
        user_id = user.id
        user_name = html.escape(f"{user.first_name or ''} {user.last_name or ''}".strip())
        user_link = f'<a href="tg://user?id={user_id}">{user_name}</a>'
        
        # --- نهاية التعديل ---

        type_info = {
            'manual': {'icon': '🛍️', 'text': 'منتج يدوي'},
            'pubg': {'icon': '⚡', 'text': 'PUBG Mobile'},
            'freefire': {'icon': '🔥', 'text': 'Free Fire'},
            'freefire2': {'icon': '🔥', 'text': 'Free Fire 2'}
        }.get(order_type, {'icon': '📦', 'text': 'طلب عام'})
        
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton(
                "✅ إتمام وإرسال رسالة", 
                callback_data=f'complete_with_msg_{order_id}'
            ),
            types.InlineKeyboardButton(
                "✅ إتمام فقط", 
                callback_data=f'complete_order_{order_id}'
            ),
            types.InlineKeyboardButton(
                "❌ رفض الطلب", 
                callback_data=f'reject_order_{order_id}'
            )
        )
        
        # 2. تحديث نص الرسالة لاستخدام الرابط الجديد
        admin_msg = (
            f"{type_info['icon']} طلب {type_info['text']} جديد\n\n"
            f"🆔 رقم الطلب: {order_id}\n"
            f"👤 المستخدم: {user_link}\n" # تم استخدام الرابط هنا
            f"📦 المنتج: {product_name}\n"
            f"💵 المبلغ: {price} ل.س\n"
            f"{f'🎮 معرف اللاعب: {player_id}' if player_id else ''}"
        )
        
        bot.send_message(
            ADMIN_ID, 
            admin_msg, 
            reply_markup=markup,
            parse_mode='HTML' # 3. إضافة parse_mode للسماح بالروابط
        )
    except Exception as e:
        print(f"Error notifying admin: {str(e)}")
        bot.send_message(
            ADMIN_ID, 
            f"🛒 طلب جديد #{order_id} (حدث خطأ في الأزرار)"
        )

def is_bot_paused():
    result = safe_db_execute("SELECT value FROM bot_settings WHERE key='is_paused'")
    return result[0][0] == '1' if result else False

# ============= واجهة المستخدم =============
def main_menu(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, is_persistent=True)
    buttons = [
        ('PUBG MOBILE ⚡', 'pubg'),
        ('FREE FIRE 🔥', 'freefire'),
        ('أكواد وبطاقات', 'cards'),
        ('🛍️ المنتجات اليدوية', 'manual'),
        ('طلباتي 🗂️', 'orders'),
        ('رصيدي 💰', 'balance'),
        ('📞 الدعم', 'support')
    ]
    enabled_buttons = [btn[0] for btn in buttons if not is_button_disabled(btn[1])]
    
    # التأكد من وجود أزرار مفعّلة قبل محاولة إنشاء صفوف
    if enabled_buttons:
        # إنشاء صفوف ثنائية للأزرار
        rows = [enabled_buttons[i:i+2] for i in range(0, len(enabled_buttons), 2)]
        for row in rows:
            markup.row(*row)

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

@bot.message_handler(commands=['broadcast'])
def start_broadcast(message):
    if not is_admin(message.from_user.id):
        return
    msg = bot.send_message(message.chat.id, "📝 أرسل الرسالة التي تريد إذاعتها لجميع المستخدمين:")
    bot.register_next_step_handler(msg, confirm_broadcast_message)

def confirm_broadcast_message(message):
    text = message.text
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ نعم، أرسل", callback_data=f'send_broadcast_{text}'),
        types.InlineKeyboardButton("❌ إلغاء", callback_data='cancel_broadcast')
    )
    bot.send_message(message.chat.id, f"📬 تأكيد إرسال الرسالة التالية:\n\n{text}", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('send_broadcast_'))
def send_broadcast_to_all(call):
    text = call.data.replace('send_broadcast_', '', 1)
    users = safe_db_execute("SELECT user_id FROM users")
    sent, failed = 0, 0
    for (user_id,) in users:
        try:
            bot.send_message(user_id, text)
            sent += 1
        except Exception as e:
            failed += 1
            print(f"❌ فشل إرسال إلى {user_id}: {e}")
    bot.edit_message_text(
        f"✅ تم إرسال الرسالة لـ {sent} مستخدم.\n❌ فشل الإرسال إلى {failed}.",
        call.message.chat.id,
        call.message.message_id
    )

@bot.message_handler(commands=['list_manual_categories'])
def list_manual_categories(message):
    if not is_admin(message.from_user.id):
        return
    categories = safe_db_execute("SELECT id, name FROM manual_categories")
    if not categories:
        bot.send_message(message.chat.id, "⚠️ لا توجد فئات يدوية")
        return
    text = "📚 الفئات اليدوية المتوفرة:\n\n"
    for cat_id, name in categories:
        text += f"🔹 {name} (ID: {cat_id})\n"
    bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda msg: msg.text == '📞 الدعم')
def support_info_handler(message):
    support_text = (
        "📬 تواصل مع الدعم في حال واجهت أي مشاكل \n\n"
        "🔹 حساب الدعم : @GG_Store_Support \n\n"
        "📬 لمتابعة اخر التحديثات والعروض \n\n"
        "🔹 قناة البوت : @GGStoreSy \n\n"
    )
    bot.send_message(message.chat.id, support_text)

@bot.message_handler(func=lambda msg: msg.text == '🔙 الرجوع للقائمة الرئيسية')
def back_to_main_menu(message):
    bot.send_message(
        message.chat.id,
        "مرحبا بكم في متجر GG STORE !",
        reply_markup=main_menu(message.from_user.id)
    )

@bot.message_handler(func=lambda msg: msg.text == 'FREE FIRE 🔥' and not is_button_disabled('freefire'))
def free_fire_main_menu(message):
    if is_bot_paused() and not is_admin(message.from_user.id):
        return
    markup = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
        is_persistent=True
    )
    
    ff_buttons = []
    # التحقق من أن الأزرار الفرعية ليست معطلة
    if not is_button_disabled('freefire_1'):
        ff_buttons.append('🔥 Free Fire 1')
    if not is_button_disabled('freefire_2'):
        ff_buttons.append('🔥 Free Fire 2')
    
    if ff_buttons:
        markup.row(*ff_buttons)

    # إضافة زر الشحن اليدوي الجديد لفري فاير
    if not is_button_disabled('freefire_manual'):
        markup.row('شحن يدوي (فري فاير) 👨🏻‍💻')

    markup.row('🔙 الرجوع للقائمة الرئيسية')
    
    try:
        bot.send_message(
            message.chat.id,
            f"اختر أحد السيرفرات أو نوع الشحن:",
            reply_markup=markup
        )
    except Exception as e:
        print(f"Error sending message: {str(e)}")
        bot.send_message(
            message.chat.id,
            f"اختر أحد السيرفرات أو نوع الشحن:",
            reply_markup=markup
        )


@bot.callback_query_handler(func=lambda call: call.data == 'clean_pending_recharges' and is_admin(call.from_user.id))
def clean_pending_recharges(call):
    try:
        # We need to get the number of affected rows.
        # For UPDATE, a cursor's rowcount is what we need.
        # Let's adjust safe_db_execute to return this.
        # A quick fix for now is to just commit.
        with db_lock:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE recharge_requests 
                SET status = 'failed' 
                WHERE status = 'pending' OR status = 'pending_admin'
            """)
            affected = cursor.rowcount
            conn.commit()
            cursor.close()
        
        bot.answer_callback_query(call.id, f"✅ تم تنظيف {affected} طلب معلق")
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ حدث خطأ: {str(e)}")


@bot.callback_query_handler(func=lambda call: call.data == 'manage_buttons' and is_admin(call.from_user.id))
def handle_manage_buttons(call):
    buttons = [
        ('PUBG MOBILE ⚡', 'pubg'),
        ('FREE FIRE 🔥', 'freefire'),
        ('أكواد وبطاقات', 'cards'),
        ('🛍️ المنتجات اليدوية', 'manual'),
        ('طلباتي 🗂️', 'orders'),
        ('رصيدي 💰', 'balance')
    ]
    markup = types.InlineKeyboardMarkup()
    for name, key in buttons:
        status = "❌" if is_button_disabled(key) else "✅"
        # إضافة 'main' لتمييز مصدر الطلب
        markup.add(types.InlineKeyboardButton(
            f"{status} {name}",
            callback_data=f'toggle_button_main_{key}'
        ))
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data='admin_panel'))
    bot.edit_message_text(
        "إدارة أزرار القائمة الرئيسية:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )

# دالة جديدة لإدارة الأزرار الفرعية (داخل الخدمات)
@bot.callback_query_handler(func=lambda call: call.data == 'manage_sub_buttons' and is_admin(call.from_user.id))
def handle_manage_sub_buttons(call):
    sub_buttons = [
        ('Auto ⚡ (PUBG)', 'pubg_auto'),
        ('شحن يدوي (ببجي) 👨🏻‍💻', 'pubg_manual'),
        ('🔥 Free Fire 1', 'freefire_1'),
        ('🔥 Free Fire 2', 'freefire_2'),
        ('شحن يدوي (فري فاير) 👨🏻‍💻', 'freefire_manual')
    ]
    markup = types.InlineKeyboardMarkup()
    for name, key in sub_buttons:
        status = "❌" if is_button_disabled(key) else "✅"
        # إضافة 'sub' لتمييز مصدر الطلب
        markup.add(types.InlineKeyboardButton(
            f"{status} {name}",
            callback_data=f'toggle_button_sub_{key}'
        ))
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data='admin_panel'))
    bot.edit_message_text(
        "إدارة أزرار الخدمات (PUBG & Free Fire):",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith('toggle_button_') and is_admin(call.from_user.id))
def handle_toggle_button(call):
    parts = call.data.split('_')
    menu_type = parts[2]
    button_key = '_'.join(parts[3:]) # للتعامل مع أسماء أزرار قد تحتوي على '_'

    current_status = is_button_disabled(button_key)
    safe_db_execute(
        "INSERT OR REPLACE INTO disabled_buttons (button_name, is_disabled) VALUES (?, ?)",
        (button_key, not current_status)
    )
    bot.answer_callback_query(call.id, f"تم {'تعطيل' if not current_status else 'تفعيل'} الزر")
    
    # إعادة التوجيه إلى القائمة الصحيحة
    if menu_type == 'main':
        handle_manage_buttons(call)
    elif menu_type == 'sub':
        handle_manage_sub_buttons(call)


@bot.callback_query_handler(func=lambda call: call.data == 'manage_channel' and is_admin(call.from_user.id))
def handle_manage_channel(call):
    channel_id = get_notification_channel()
    status = "✅ معينة" if channel_id else "❌ غير معينة"
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("تعيين القناة", callback_data='set_channel'))
    if channel_id:
        markup.add(types.InlineKeyboardButton("إزالة القناة", callback_data='remove_channel'))
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data='admin_panel'))
    bot.edit_message_text(
        f"إدارة القناة:\n\nالحالة الحالية: {status}",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data == 'set_channel' and is_admin(call.from_user.id))
def handle_set_channel(call):
    msg = bot.send_message(
        call.message.chat.id,
        "أرسل معرف القناة (يجب أن يبدأ ب @ أو يكون آيدي رقمي):",
        reply_markup=types.ForceReply()
    )
    bot.register_next_step_handler(msg, process_set_channel)

def process_set_channel(message):
    try:
        channel_id = message.text.strip()
        if not (channel_id.startswith('@') or channel_id.lstrip('-').isdigit()):
            raise ValueError("معرف القناة غير صالح")
        safe_db_execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
                       ('channel_id', channel_id))
        bot.send_message(message.chat.id, f"✅ تم تعيين القناة بنجاح: {channel_id}")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data == 'remove_channel' and is_admin(call.from_user.id))
def handle_remove_channel(call):
    safe_db_execute("DELETE FROM bot_settings WHERE key='channel_id'")
    bot.answer_callback_query(call.id, "✅ تمت إزالة القناة بنجاح")
    handle_manage_channel(call)

@bot.callback_query_handler(func=lambda call: call.data == 'manage_admins' and is_admin(call.from_user.id))
def handle_manage_admins(call):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("➕ إضافة مشرف", callback_data='add_admin'))
    markup.add(types.InlineKeyboardButton("🗑️ حذف مشرف", callback_data='remove_admin'))
    markup.add(types.InlineKeyboardButton("📋 قائمة المشرفين", callback_data='list_admins'))
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data='admin_panel'))
    bot.edit_message_text(
        "إدارة المشرفين:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data == 'add_admin' and is_admin(call.from_user.id))
def handle_add_admin(call):
    msg = bot.send_message(
        call.message.chat.id,
        "أرسل آيدي المستخدم الذي تريد ترقيته إلى مشرف:",
        reply_markup=types.ForceReply()
    )
    bot.register_next_step_handler(msg, process_add_admin)

def process_add_admin(message):
    try:
        new_admin_id = int(message.text)
        if is_admin(new_admin_id):
            bot.send_message(message.chat.id, "⚠️ هذا المستخدم مشرف بالفعل!")
            return
        safe_db_execute(
            "INSERT INTO admins (admin_id, username) VALUES (?, ?)",
            (new_admin_id, f"@{message.from_user.username}" if message.from_user.username else None)
        )
        bot.send_message(message.chat.id, f"✅ تمت ترقية المستخدم {new_admin_id} إلى مشرف")
    except ValueError:
        bot.send_message(message.chat.id, "❌ يرجى إدخال آيدي صحيح!")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data == 'remove_admin' and is_admin(call.from_user.id))
def handle_remove_admin(call):
    admins = safe_db_execute("SELECT admin_id, username FROM admins WHERE admin_id != ?", (ADMIN_ID,))
    if not admins:
        bot.answer_callback_query(call.id, "⚠️ لا يوجد مشرفين آخرين")
        return
    markup = types.InlineKeyboardMarkup()
    for admin_id, username in admins:
        markup.add(types.InlineKeyboardButton(
            f"{username or admin_id}",
            callback_data=f'confirm_remove_admin_{admin_id}'
        ))
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data='manage_admins'))
    bot.edit_message_text(
        "اختر المشرف الذي تريد إزالته:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('confirm_remove_admin_') and is_admin(call.from_user.id))
def handle_confirm_remove_admin(call):
    admin_id = call.data.split('_')[3]
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("✅ نعم، أحذف", callback_data=f'execute_remove_admin_{admin_id}'),
        types.InlineKeyboardButton("❌ إلغاء", callback_data='manage_admins')
    )
    bot.edit_message_text(
        f"⚠️ هل أنت متأكد من حذف المشرف {admin_id}؟",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('execute_remove_admin_') and is_admin(call.from_user.id))
def handle_execute_remove_admin(call):
    admin_id = call.data.split('_')[3]
    safe_db_execute("DELETE FROM admins WHERE admin_id=?", (admin_id,))
    bot.edit_message_text(
        f"✅ تم حذف المشرف {admin_id} بنجاح",
        call.message.chat.id,
        call.message.message_id
    )
# =================================================================
# |||           بداية دوال الشحن الجديدة للمستخدم                |||
# =================================================================

@bot.message_handler(func=lambda msg: msg.text == 'رصيدي 💰')
def show_balance_handler(message):
    user_id = message.from_user.id
    if is_bot_paused() and not is_admin(user_id):
        bot.send_message(message.chat.id, "⏸️ البوت متوقف مؤقتًا.")
        return

    try:
        balance = get_balance(user_id)
        markup = types.InlineKeyboardMarkup()
        
        recharge_disabled_setting = safe_db_execute("SELECT value FROM bot_settings WHERE key='recharge_disabled'")
        is_recharge_disabled = recharge_disabled_setting and recharge_disabled_setting[0][0] == '1'

        if not is_recharge_disabled or is_admin(user_id):
            markup.add(types.InlineKeyboardButton("إعادة تعبئة الرصيد 💳", callback_data="recharge_balance"))

        bot.send_message(message.chat.id, f"رصيدك الحالي: {balance:,} ل.س", reply_markup=markup)
    except Exception as e:
        print(f"Error showing balance: {str(e)}")
        bot.send_message(message.chat.id, "❌ حدث خطأ في جلب رصيدك!")

# 1. تبدأ عملية الشحن من هنا
def handle_recharge_request(message):
    try:
        recharge_disabled = safe_db_execute("SELECT value FROM bot_settings WHERE key='recharge_disabled'")[0][0] == '1'
        if recharge_disabled and not is_admin(message.from_user.id):
            bot.send_message(message.chat.id, "⏸️ خدمة إعادة تعبئة الرصيد متوقفة حالياً.")
            return

        active_methods = safe_db_execute("SELECT id, name FROM payment_methods WHERE is_active = 1")
        if not active_methods:
            bot.send_message(message.chat.id, "⚠️ لا توجد طرق دفع متاحة حالياً.")
            return

        markup = types.InlineKeyboardMarkup()
        for method_id, name in active_methods:
            markup.add(types.InlineKeyboardButton(name, callback_data=f'select_method_{method_id}'))
        
        bot.send_message(message.chat.id, "اختر طريقة الدفع المناسبة:", reply_markup=markup)

    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ: {str(e)}")

# 2. المستخدم يختار طريقة الدفع
@bot.callback_query_handler(func=lambda call: call.data.startswith('select_method_'))
def handle_payment_method_selection(call):
    try:
        method_id = int(call.data.split('_')[2])
        
        active_requests_count = safe_db_execute(
            "SELECT COUNT(*) FROM recharge_requests WHERE user_id=? AND (status='pending' OR status='pending_admin')",
            (call.from_user.id,)
        )[0][0]

        if active_requests_count > 0:
            bot.answer_callback_query(call.id, "لديك طلب شحن قيد المعالجة بالفعل!", show_alert=True)
            return

        method_type = safe_db_execute("SELECT type FROM payment_methods WHERE id=?", (method_id,))[0][0]

        if method_type == 'foreign_currency':
            # 1. تعديل الاستعلام لجلب سعر الصرف والعملة بالإضافة إلى الـ ID
            address_info_query = safe_db_execute(
                "SELECT id, currency, exchange_rate FROM payment_addresses WHERE method_id=? AND is_active=1 LIMIT 1",
                (method_id,)
            )
            
            if not address_info_query:
                bot.answer_callback_query(call.id, "عفواً، طريقة الدفع هذه غير متاحة حالياً.", show_alert=True)
                return
            
            # 2. استخراج البيانات الجديدة
            address_id, currency, rate = address_info_query[0]
            
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            markup.add('❌ إلغاء العملية')
            
            # 3. بناء رسالة جديدة ومحسّنة لعرض سعر الصرف للمستخدم
            user_message = (
                f"✅ طريقة الدفع المحددة: **{currency}**\n"
                f"💱 سعر الصرف الحالي: **{rate:,}** ل.س لكل 1 {currency}\n\n"
                f"الآن، أدخل المبلغ الذي تريد أن يصل إلى رصيدك **بالليرة السورية**:"
            )
            
            # 4. إرسال الرسالة الجديدة
            msg = bot.send_message(
                call.message.chat.id,
                user_message,
                reply_markup=markup,
                parse_mode="Markdown"
            )
            bot.register_next_step_handler(msg, process_foreign_currency_amount, address_id)

        else: # (daily_limit_syp أو unlimited_syp)
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            markup.add('❌ إلغاء العملية')
            msg = bot.send_message(
                call.message.chat.id,
                "💰 الرجاء إدخال المبلغ الذي تريد شحنه **بالليرة السورية**:",
                reply_markup=markup
            )
            bot.register_next_step_handler(msg, process_recharge_amount, method_id)
            
    except Exception as e:
        print(f"Error in handle_payment_method_selection: {e}")
        bot.answer_callback_query(call.id, "حدث خطأ ما، يرجى المحاولة لاحقًا.", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith('select_fc_addr_'))
def handle_foreign_currency_address_selection(call):
    address_id = int(call.data.split('_')[3])
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add('❌ إلغاء العملية')

    # نطلب من المستخدم إدخال المبلغ بالليرة السورية
    msg = bot.send_message(
        call.message.chat.id,
        "أدخل المبلغ الذي تريد أن يصل إلى رصيدك **بالليرة السورية**:",
        reply_markup=markup
    )
    bot.register_next_step_handler(msg, process_foreign_currency_amount, address_id)

def process_foreign_currency_amount(message, address_id):
    if message.text == '❌ إلغاء العملية':
        bot.send_message(message.chat.id, "تم إلغاء العملية.", reply_markup=main_menu(message.from_user.id))
        return
    try:
        amount_syp = int(message.text.strip())
        if amount_syp <= 0:
            raise ValueError("المبلغ يجب أن يكون أكبر من صفر")

        # ================== إضافة جديدة للتحقق من الحد الأدنى ==================
        # 1. نحصل على method_id من address_id
        method_id_query = safe_db_execute("SELECT method_id FROM payment_addresses WHERE id=?", (address_id,))
        if not method_id_query:
            bot.send_message(message.chat.id, "❌ خطأ: لم يتم العثور على طريقة الدفع.")
            return
        method_id = method_id_query[0][0]

        # 2. نحصل على الحد الأدنى من طريقة الدفع
        min_amount_query = safe_db_execute("SELECT min_amount FROM payment_methods WHERE id=?", (method_id,))
        min_amount = min_amount_query[0][0] if min_amount_query else 0

        # 3. نقارن المبلغ بالحد الأدنى
        if min_amount and amount_syp < min_amount:
            bot.send_message(message.chat.id, f"❌ المبلغ الذي أدخلته أقل من الحد الأدنى المسموح به لهذه الطريقة وهو: {min_amount:,} ل.س")
            # نطلب من المستخدم إدخال المبلغ مرة أخرى
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            markup.add('❌ إلغاء العملية')
            msg = bot.send_message(message.chat.id, "يرجى إدخال مبلغ صحيح بالليرة السورية:", reply_markup=markup)
            bot.register_next_step_handler(msg, process_foreign_currency_amount, address_id)
            return # نوقف تنفيذ الدالة هنا
        # ===================================================================

        address_info = safe_db_execute(
            "SELECT address, currency, exchange_rate FROM payment_addresses WHERE id=?",
            (address_id,)
        )[0]
        address, currency, rate = address_info

        # **هنا يتم حساب المبلغ بالعملة الأجنبية**
        foreign_amount = round(amount_syp / rate, 4)

        # إنشاء طلب مبدئي
        safe_db_execute(
            "INSERT INTO recharge_requests (user_id, amount_syp, address_id, status) VALUES (?, ?, ?, 'pending')",
            (message.from_user.id, amount_syp, address_id)
        )
        request_id = safe_db_execute("SELECT last_insert_rowid()")[0][0]

        # عرض التعليمات النهائية للمستخدم
        final_instructions = (
            f"لإضافة `{amount_syp:,}` ل.س إلى رصيدك،\n"
            f"الرجاء إرسال مبلغ  **`{foreign_amount}` {currency}**\n"
            f"إلى العنوان التالي:\n\n`{address}`\n\n"
            f"⚠️ **بعد التحويل، أرسل صورة الإشعار أو معرف العملية (TxID) هنا.**"
        )
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        markup.add('❌ إلغاء العملية')
        msg = bot.send_message(message.chat.id, final_instructions, reply_markup=markup, parse_mode='Markdown')
        bot.register_next_step_handler(msg, process_recharge_proof, request_id, address_id, amount_syp)

    except ValueError:
        msg = bot.send_message(message.chat.id, "❌ مبلغ غير صالح. الرجاء إدخال رقم صحيح بالليرة السورية.")
        bot.register_next_step_handler(msg, process_foreign_currency_amount, address_id)
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ: {e}")

# 3. المستخدم يدخل المبلغ
def process_recharge_amount(message, method_id):
    if message.text == '❌ إلغاء العملية':
        bot.send_message(message.chat.id, "تم إلغاء العملية.", reply_markup=main_menu(message.from_user.id))
        return
    try:
        amount_syp = int(message.text.strip())
        
        # ================== منطق التحقق الجديد ==================
        method_details = safe_db_execute("SELECT type, instructions, min_amount FROM payment_methods WHERE id=?", (method_id,))[0]
        method_type, instructions, min_amount = method_details
        
        if min_amount and amount_syp < min_amount:
            bot.send_message(message.chat.id, f"❌ المبلغ الذي أدخلته أقل من الحد الأدنى المسموح به لهذه الطريقة وهو: {min_amount:,} ل.س")
            # نطلب منه المحاولة مرة أخرى
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            markup.add('❌ إلغاء العملية')
            msg = bot.send_message(message.chat.id, "يرجى إدخال مبلغ صحيح:", reply_markup=markup)
            bot.register_next_step_handler(msg, process_recharge_amount, method_id)
            return
        # =======================================================

        # ... (باقي كود الدالة يبقى كما هو دون تغيير) ...
        
        selected_address = None
        today = datetime.now().strftime("%Y-%m-%d")

        if method_type == 'daily_limit_syp':
            safe_db_execute("UPDATE payment_addresses SET daily_used=0, last_reset_date=? WHERE last_reset_date != ? OR last_reset_date IS NULL", (today, today))
            addresses = safe_db_execute(
                "SELECT id, address, daily_limit, daily_used FROM payment_addresses WHERE method_id=? AND is_active=1", (method_id,)
            )
            for addr_id, address, limit, used in addresses:
                if (limit - used) >= amount_syp:
                    selected_address = (addr_id, address)
                    break
            if not selected_address:
                bot.send_message(message.chat.id, "⚠️ عفواً، لا يوجد خط يستقبل هذا المبلغ حالياً. يرجى المحاولة بمبلغ أقل أو في وقت لاحق.")
                return

        elif method_type in ['unlimited_syp', 'foreign_currency']:
            address = safe_db_execute("SELECT id, address FROM payment_addresses WHERE method_id=? AND is_active=1 LIMIT 1", (method_id,))
            if not address:
                bot.send_message(message.chat.id, "⚠️ لا يوجد عنوان دفع متاح لهذه الطريقة حالياً.")
                return
            selected_address = address[0]

        address_id, address_text = selected_address

        safe_db_execute(
            "INSERT INTO recharge_requests (user_id, amount_syp, address_id, status) VALUES (?, ?, ?, 'pending')",
            (message.from_user.id, amount_syp, address_id)
        )
        request_id = safe_db_execute("SELECT last_insert_rowid()")[0][0]

        final_instructions = (
            f"{instructions}\n\n"
            f"الرجاء إرسال مبلغ: `{amount_syp:,}` ل.س\n"
            f"إلى العنوان التالي:\n`{address_text}`\n\n"
            f"⚠️ **بعد التحويل، قم بإرسال صورة الإشعار أو رقم العملية هنا لإتمام الطلب.**"
        )
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        markup.add('❌ إلغاء العملية')
        msg = bot.send_message(message.chat.id, final_instructions, reply_markup=markup, parse_mode='Markdown')
        bot.register_next_step_handler(msg, process_recharge_proof, request_id, address_id, amount_syp)

    except ValueError:
        msg = bot.send_message(message.chat.id, "❌ مبلغ غير صالح. الرجاء إدخال رقم صحيح.")
        bot.register_next_step_handler(msg, process_recharge_amount, method_id)
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ فادح: {e}")
# 4. المستخدم يرسل إثبات الدفع
def process_recharge_proof(message, request_id, address_id, amount_syp):
    if message.text == '❌ إلغاء العملية':
        safe_db_execute("UPDATE recharge_requests SET status='cancelled' WHERE id=?", (request_id,))
        bot.send_message(message.chat.id, "تم إلغاء الطلب.", reply_markup=main_menu(message.from_user.id))
        return
        
    try:
        proof_type, proof_content, transaction_id = None, None, None
        if message.photo:
            proof_type = "صورة"
            proof_content = message.photo[-1].file_id
        elif message.text:
            proof_type = "رقم العملية"
            proof_content = message.text.strip()
            transaction_id = proof_content
        else:
            bot.send_message(message.chat.id, "نوع الإثبات غير مدعوم. أرسل صورة أو نص.")
            bot.register_next_step_handler(message, process_recharge_proof, request_id, address_id, amount_syp)
            return

        safe_db_execute(
            "UPDATE recharge_requests SET transaction_id=?, proof_type=?, proof_content=?, status='pending_admin' WHERE id=?",
            (transaction_id, proof_type, proof_content, request_id)
        )
        
        notify_admin_recharge_request(message.from_user, request_id, amount_syp, proof_type, proof_content, address_id)
        
        bot.send_message(message.chat.id, "✅ تم استلام طلبك بنجاح وهو قيد المراجعة الآن.", reply_markup=main_menu(message.from_user.id))

    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ أثناء معالجة الإثبات: {e}")
        safe_db_execute("UPDATE recharge_requests SET status='failed' WHERE id=?", (request_id,))

# 5. إشعار الأدمن
def notify_admin_recharge_request(user, request_id, amount_syp, proof_type, proof_content, address_id):
    try:
        address_info = safe_db_execute("SELECT p_addr.address, p_meth.name FROM payment_addresses p_addr JOIN payment_methods p_meth ON p_addr.method_id = p_meth.id WHERE p_addr.id=?", (address_id,))[0]
        address, method_name = address_info

        markup = types.InlineKeyboardMarkup()
        markup.row(
            types.InlineKeyboardButton("✅ الموافقة", callback_data=f"approve_recharge_{request_id}"),
            types.InlineKeyboardButton("❌ الرفض", callback_data=f"reject_recharge_{request_id}")
        )
        user_name = html.escape(f"{user.first_name or ''} {user.last_name or ''}".strip())
        user_link = f'<a href="tg://user?id={user.id}">{user_name}</a>'

        admin_msg = (
            f"🔄 طلب شحن جديد #{request_id}\n\n"
            f"👤 المستخدم: {user_link}\n"
            f"💰 المبلغ: {amount_syp:,} ل.س\n"
            f"💳 الطريقة: {method_name}\n"
            f"📍 العنوان: `{address}`\n"
        )
        
        if proof_type == "صورة":
            bot.send_photo(
                ADMIN_ID,
                proof_content,
                caption=admin_msg,
                reply_markup=markup,
                parse_mode='HTML'
            )
        else: # رقم العملية
            admin_msg += f"🔢 رقم العملية: `{proof_content}`"
            bot.send_message(
                ADMIN_ID,
                admin_msg,
                reply_markup=markup,
                parse_mode='HTML'
            )
    except Exception as e:
        print(f"Error in notify_admin_recharge_request: {e}")

# =================================================================
# |||            نهاية دوال الشحن الجديدة للمستخدم                |||
# =================================================================
#========== free fire 2 ==================
@bot.message_handler(func=lambda msg: msg.text == '🔥 Free Fire 2'and not is_button_disabled('freefire'))
def show_freefire2_offers_handler(message):
    if is_bot_paused() and not is_admin(message.from_user.id):
        return
    update_freefire2_products() # تأكد أنها تحدث هنا عند الطلب

    if not FREE_FIRE2_PRODUCTS:
        bot.send_message(message.chat.id, "⚠️ لا توجد عروض متاحة حالياً لـ Free Fire 2")
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for product in FREE_FIRE2_PRODUCTS:
        try:
            price_syp = convert_to_syp(product['price'])
            btn_text = f"{product['offerName']} - {price_syp:,} ل.س"
            markup.add(types.InlineKeyboardButton(
                btn_text, 
                callback_data=f'ff2_offer_{product["offerId"]}'
            ))
        except Exception as e:
            print(f"خطأ في معالجة المنتج: {str(e)}")
            continue
    bot.send_message(
        message.chat.id, 
        "🎮 عروض Free Fire 2 المتاحة:",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('ff2_offer_'))
def handle_freefire2_offer_selection(call):
    user_id = call.from_user.id
    if user_processing_lock.get(user_id, False):
        bot.answer_callback_query(call.id, "لديك عملية قيد المعالجة بالفعل. الرجاء الانتظار.")
        return

    try:
        user_processing_lock[user_id] = True # قفل العملية
        offer_id = call.data.split('_')[2]
        selected_product = next((p for p in FREE_FIRE2_PRODUCTS if str(p['offerId']) == offer_id), None)
        if not selected_product:
            bot.answer_callback_query(call.id, "⚠️ المنتج غير متوفر حالياً")
            user_processing_lock[user_id] = False
            return
        
        # تعديل الرسالة لإخفاء الزر وطلب ID اللاعب
        product_name = selected_product['offerName']
        price_syp = convert_to_syp(selected_product['price'])
        
        updated_text = (
            f"🎮 عرض Free Fire 2:\n"
            f"📌 {product_name}\n"
            f"💰 السعر: {price_syp:,} ل.س\n\n"
        
        )
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=updated_text,
            reply_markup=None # إزالة الأزرار
        )

        msg = bot.send_message(
            call.message.chat.id,
            "أدخل ID أو رقم اللاعب :",
            reply_markup=types.ForceReply(selective=True)
        )
        bot.register_next_step_handler(
            msg, # استخدم msg هنا لتتبع الخطوة التالية بشكل صحيح
            process_freefire2_purchase, 
            selected_product
        )

    except Exception as e:
        print(f"Error in offer selection: {str(e)}")
        bot.send_message(call.message.chat.id, "❌ حدث خطأ في اختيار العرض!")
        user_processing_lock[user_id] = False # تأكد من تحرير القفل

def process_freefire2_purchase(message, product):
    user_id = message.from_user.id
    try:
        player_id = message.text.strip()
        if not player_id.isdigit() or len(player_id) < 6:
            bot.send_message(message.chat.id, "❌ رقم اللاعب غير صالح! يجب أن يكون رقماً ويحتوي على 6 خانات على الأقل")
            return
        price_syp = convert_to_syp(product['price'])
        if get_balance(user_id) < price_syp:
            bot.send_message(message.chat.id, f"⚠️ رصيدك غير كافي. السعر: {price_syp:,} ل.س")
            return
        markup = types.InlineKeyboardMarkup()
        markup.row(
            types.InlineKeyboardButton("✅ تأكيد الشراء", callback_data=f'ff2_confirm_{product["offerId"]}_{player_id}_{price_syp}'),
            types.InlineKeyboardButton("❌ إلغاء", callback_data='cancel_purchase')
        )
        bot.send_message(
            message.chat.id,
            f"🛒 تأكيد عملية الشراء:\n\n"
            f"📌 العرض: {product['offerName']}\n"
            f"💰 السعر: {price_syp:,} ل.س\n"
            f"👤 آيدي اللاعب: {player_id}\n\n"
            f"هل أنت متأكد من المعلومات أعلاه؟",
            reply_markup=markup
        )
    except Exception as e:
        print(f"Error in purchase process: {str(e)}")
        bot.send_message(message.chat.id, "❌ حدث خطأ غير متوقع في المعالجة!")
    finally:
        user_processing_lock[user_id] = False # تحرير القفل

@bot.callback_query_handler(func=lambda call: call.data.startswith('ff2_confirm_'))
def confirm_freefire2_purchase(call):
    user_id = call.from_user.id
    if user_processing_lock.get(user_id, False):
        bot.answer_callback_query(call.id, "لديك عملية قيد المعالجة بالفعل. الرجاء الانتظار.")
        return
    
    user_processing_lock[user_id] = True # قفل العملية
    try:
        # إخفاء أزرار التأكيد/الإلغاء
        bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=None # إزالة الأزرار
        )
        bot.answer_callback_query(call.id, "⏳ جاري معالجة طلبك...")

        parts = call.data.split('_')
        # تأكد من استخراج offer_id بشكل صحيح، وليس product_id
        offer_id = parts[2]
        player_id = parts[3]
        price_syp = int(parts[4])
        
        username = f"@{call.from_user.username}" if call.from_user.username else "غير متوفر"
        user_name = f"{call.from_user.first_name or ''} {call.from_user.last_name or ''}".strip()
        
        # البحث عن المنتج في القائمة المحلية باستخدام offer_id
        product = next((p for p in FREE_FIRE2_PRODUCTS if str(p['offerId']) == offer_id), None)
        
        if not product:
            raise ValueError("المنتج لم يعد متوفراً")

        if get_balance(user_id) < price_syp:
            raise ValueError("رصيدك غير كافي!")

        headers = {'X-API-Key': FREE_FIRE2_API_KEY}
        payload = {
            "playerId": player_id,
            "offerName": product['offerName'] # استخدم offerName من المنتج
        }
        response = requests.post(
            f"{FREE_FIRE2_BASE_URL}topup",
            json=payload,
            headers=headers,
            timeout=40
        )

        if response.status_code == 200:
            update_balance(user_id, -price_syp)
            result = response.json().get('data', {})
            transaction_id = result.get('transaction_id', 'N/A')

            order_id = log_user_order(
                user_id=user_id,
                order_type='freefire2',
                product_id=offer_id, # سجل offer_id كـ product_id
                product_name=product.get('offerName', 'Free Fire 2 Product'),
                price=price_syp,
                player_id=player_id,
                api_response=result
            )
            bot.edit_message_text(
                f"✅ تمت عملية الشراء بنجاح!\n\n"
                f"📌 المنتج: {product['offerName']}\n"
                f"🆔 آيدي اللاعب: {player_id}\n"
                f"💳 السعر: {price_syp:,} ل.س\n"
                f"📌 رقم العملية: {transaction_id}",
                call.message.chat.id,
                call.message.message_id
            )
            admin_msg = (
                f"🛒 عملية شراء جديدة\n"
                f" #Free_Fire_imabou\n\n"
                f"👤 الاسم: {user_name}\n"
                f"👤 المستخدم: {username}\n"
                f"🆔 ID: {user_id}\n"
                f"📌 العرض: {product['offerName']}\n"
                f"🆔 اللاعب: {player_id}\n"
                f"💰 المبلغ: {price_syp} ل.س\n"
                f"📌 رقم العملية: {transaction_id}"
            )
            channel_id = get_notification_channel()
            if channel_id:
                try:
                    bot.send_message(channel_id, admin_msg)
                except Exception as e:
                    print(f"Failed to send to channel: {str(e)}")
                    bot.send_message(ADMIN_ID, f"فشل إرسال إلى القناة:\n\n{admin_msg}")
            else:
                bot.send_message(ADMIN_ID, admin_msg)

            bot.send_message(call.message.chat.id, "⬇️ القائمة الرئيسية", reply_markup=main_menu(call.from_user.id))
        else:
            error_msg = response.json().get('message', 'فشلت العملية دون تفاصيل')
            raise Exception(error_msg)
            

    except Exception as e:
        print(f"Purchase Error: {str(e)}")
        bot.edit_message_text(
            f"❌ حدث خطأ غير متوقع! يرجى التواصل مع الدعم ",
            call.message.chat.id,
            call.message.message_id
        )
        bot.send_message(
            ADMIN_ID,
            f"⚠️ خطأ في عملية شراء Free Fire 2\nUser: {call.from_user.id}\nError: {str(e)}"
        )
    finally:
        user_processing_lock[user_id] = False # تحرير القفل

#============== free fire 2 end ====================

@bot.message_handler(func=lambda msg: msg.text == 'أكواد وبطاقات' and not is_button_disabled('cards'))
def show_categories_handler(message):
    if is_bot_paused() and not is_admin(message.from_user.id):
        return
    show_categories(message)

@bot.message_handler(func=lambda msg: msg.text == '🛍️ المنتجات اليدوية' and not is_button_disabled('manual'))
def show_manual_categories(message): 
    if is_bot_paused() and not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⏸️ البوت متوقف مؤقتًا.")
        return
    
    categories = safe_db_execute("SELECT id, name FROM manual_categories WHERE is_active = TRUE")
    
    if not categories:
        bot.send_message(message.chat.id, "⚠️ لا توجد فئات متاحة حالياً.")
        return

    # تحديد عرض الصف ليكون 2
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    # إنشاء قائمة أزرار الفئات
    category_buttons = [
        types.InlineKeyboardButton(cat_name, callback_data=f'manual_cat_{cat_id}') 
        for cat_id, cat_name in categories
    ]
    
    # إضافة الأزرار دفعة واحدة ليتم ترتيبها تلقائيًا
    markup.add(*category_buttons)
    
    bot.send_message(message.chat.id, "اختر احد الفئات :", reply_markup=markup)


# دالة مساعدة جديدة لتوحيد منطق الإرسال/التعديل (كما هي، لكن ستُستخدم بشكل مختلف الآن)
# هذه الدالة ستبقى كما هي، المشكلة كانت في طريقة استدعائها من show_manual_categories
def _send_or_edit_manual_categories(chat_id, message_id, markup, text):
    try:
        # حاول تعديل الرسالة إذا كان message_id متوفرًا
        if message_id:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=markup
            )
        else:
            # وإلا، أرسل رسالة جديدة (هذا الجزء لن يتم استخدامه بعد التعديل أعلاه مباشرة)
            bot.send_message(chat_id, text, reply_markup=markup)
    except telebot.apihelper.ApiTelegramException as e:
        # إذا فشل التعديل، أرسل رسالة جديدة كحل بديل
        # هذا الجزء مهم لمعالجة الأخطاء مثل "message not modified"
        if "message to edit not found" in str(e).lower() or "message is not modified" in str(e).lower():
            bot.send_message(chat_id, text, reply_markup=markup)
        else:
            raise e # أعد إثارة أي أخطاء أخرى

@bot.callback_query_handler(func=lambda call: call.data.startswith('edit_manual_prod_'))
def edit_manual_product(call):
    product_id = call.data.split('_')[-1]
    product = safe_db_execute("SELECT id, name, price, description, is_active, category_id, requires_player_id FROM manual_products WHERE id=?", (product_id,))
    if not product:
        bot.answer_callback_query(call.id, "⚠️ المنتج غير موجود")
        return
        
    prod_id, name, price, desc, is_active, cat_id, req_id = product[0]
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    # زر التفعيل والتعطيل
    toggle_text = "❌ تعطيل المنتج" if is_active else "✅ تفعيل المنتج"
    markup.add(types.InlineKeyboardButton(toggle_text, callback_data=f'toggle_prod_active_{prod_id}'))
    
    markup.add(
        types.InlineKeyboardButton("✏️ تعديل الاسم", callback_data=f'edit_prod_name_{prod_id}'),
        types.InlineKeyboardButton("💵 تعديل السعر", callback_data=f'edit_prod_price_{prod_id}')
    )
    markup.add(
        types.InlineKeyboardButton("📝 تعديل الوصف", callback_data=f'edit_prod_desc_{prod_id}'),
        types.InlineKeyboardButton("🔄 تبديل ID اللاعب", callback_data=f'toggle_prod_id_{prod_id}')
    )
    markup.add(types.InlineKeyboardButton("🗑️ حذف المنتج", callback_data=f'delete_prod_{prod_id}'))
    # زر الرجوع يعود إلى قائمة المنتجات في نفس الفئة
    markup.add(types.InlineKeyboardButton("🔙 رجوع للمنتجات", callback_data=f'manage_prods_in_cat_{cat_id}'))
    
    desc_text = desc if desc else "لا يوجد وصف"
    status_text = "مفعل ✅" if is_active else "معطل ❌"
    id_req_text = 'نعم' if req_id else 'لا'

    text = (
        f"🛍️ *إدارة المنتج: {name}*\n\n"
        f"💰 *السعر:* {price} ل.س\n"
        f"📄 *الوصف:* {desc_text}\n"
        f"🎮 *معرف اللاعب مطلوب:* {id_req_text}\n"
        f"🔄 *الحالة:* {status_text}"
    )
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode='Markdown'
    )
@bot.callback_query_handler(func=lambda call: call.data.startswith('toggle_prod_active_'))
def toggle_product_active_status(call):
    product_id = call.data.split('_')[-1]
    
    # عكس القيمة الحالية لـ is_active
    safe_db_execute("UPDATE manual_products SET is_active = NOT is_active WHERE id=?", (product_id,))
    
    current_status = safe_db_execute("SELECT is_active FROM manual_products WHERE id=?", (product_id,))[0][0]
    status_msg = "تم تفعيل المنتج" if current_status else "تم تعطيل المنتج"
    bot.answer_callback_query(call.id, status_msg)
    
    # إعادة تحميل صفحة تعديل المنتج لعرض التغييرات
    edit_manual_product(call)
@bot.callback_query_handler(func=lambda call: call.data.startswith('edit_prod_name_'))
def edit_product_name(call):
    product_id = call.data.split('_')[3]
    msg = bot.send_message(call.message.chat.id, "أرسل الاسم الجديد للمنتج:")
    bot.register_next_step_handler(msg, process_edit_product_name, call, product_id)

def process_edit_product_name(message, call, product_id):
    new_name = message.text.strip()
    if not new_name:
        bot.send_message(message.chat.id, "❌ الاسم لا يمكن أن يكون فارغاً")
        return
    safe_db_execute("UPDATE manual_products SET name=? WHERE id=?", (new_name, product_id))
    bot.send_message(message.chat.id, "✅ تم تحديث اسم المنتج بنجاح")
    edit_manual_product(call) # العودة إلى صفحة تعديل المنتج بعد التحديث

@bot.callback_query_handler(func=lambda call: call.data == 'search_balance')
def handle_search_balance(call):
    msg = bot.send_message(
        call.message.chat.id,
        "أدخل آيدي المستخدم أو اسمه للبحث:",
        reply_markup=types.ForceReply()
    )
    bot.register_next_step_handler(msg, process_user_search)

def process_user_search(message):
    try:
        search_term = message.text.strip()
        if search_term.isdigit():
            user_id = int(search_term)
            results = safe_db_execute(
                "SELECT user_id, balance FROM users WHERE user_id=?",
                (user_id,)
            )
        else:
            results = safe_db_execute(
                """SELECT u.user_id, u.balance 
                FROM users u
                LEFT JOIN user_profiles p ON u.user_id = p.user_id
                WHERE p.username LIKE ? OR p.first_name LIKE ?""",
                (f"%{search_term}%", f"%{search_term}%")
            )
        if not results:
            bot.send_message(message.chat.id, "⚠️ لا يوجد مستخدم بهذه البيانات")
            return
        response = "نتائج البحث:\n\n"
        for user_id, balance in results:
            response += f"👤 آيدي: {user_id}\n💰 الرصيد: {balance} ل.س\n\n"
        bot.send_message(message.chat.id, response)
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data == 'total_balances')
def handle_total_balances(call):
    try:
        total = safe_db_execute("SELECT SUM(balance) FROM users")[0][0] or 0
        count = safe_db_execute("SELECT COUNT(*) FROM users")[0][0]
        top_users = safe_db_execute(
            "SELECT user_id, balance FROM users ORDER BY balance DESC LIMIT 5"
        )
        response = (
            f"📊 إحصائيات الأرصدة:\n\n"
            f"👥 عدد المستخدمين: {count}\n"
            f"💰 الإجمالي: {total:,} ل.س\n\n"
            f"🏆 أعلى 5 أرصدة:\n"
        )
        for i, (user_id, balance) in enumerate(top_users, 1):
            response += f"{i}. {user_id}: {balance:,} ل.س\n"
        bot.send_message(call.message.chat.id, response)
    except Exception as e:
        bot.send_message(call.message.chat.id, f"❌ حدث خطأ: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data == 'user_management')
def handle_user_management(call):
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton('بحث بالآيدي', callback_data='search_by_id'),
        types.InlineKeyboardButton('بحث بالاسم', callback_data='search_by_name'))
    markup.row(
        types.InlineKeyboardButton('إجمالي أرصدة المستخدمين', callback_data='total_balances'),
        types.InlineKeyboardButton('خصم من المستخدم', callback_data='deduct_balance'))
    markup.row(
        types.InlineKeyboardButton('تعديل رصيد مستخدم', callback_data='edit_balance'),
        types.InlineKeyboardButton('رجوع', callback_data='admin_panel'))
    bot.edit_message_text(
        "إدارة المستخدمين:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data == 'manage_manual')
def handle_manage_manual(call):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton('إدارة المنتجات', callback_data='manage_manual_products'),
        types.InlineKeyboardButton('إدارة الفئات', callback_data='manage_manual_categories'),
    )
    markup.add(
        types.InlineKeyboardButton('الطلبات', callback_data='manage_manual_orders'),
        types.InlineKeyboardButton('رجوع', callback_data='admin_panel')
    )
    bot.edit_message_text(
        "إدارة العمليات اليدوية:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data in ['search_by_id', 'search_by_name'])
def handle_advanced_search(call):
    search_type = call.data.split('_')[-1]
    if search_type == 'id':
        msg = bot.send_message(
            call.message.chat.id,
            "أدخل الآيدي الخاص بالمستخدم:",
            reply_markup=types.ForceReply()
        )
        bot.register_next_step_handler(msg, process_id_search)
    else:
        msg = bot.send_message(
            call.message.chat.id,
            "أدخل اسم المستخدم للبحث:",
            reply_markup=types.ForceReply()
        )
        bot.register_next_step_handler(msg, process_name_search)

def process_id_search(message):
    try:
        user_id = int(message.text)
        result = safe_db_execute(
            "SELECT user_id, balance FROM users WHERE user_id=?",
            (user_id,)
        )
        if result:
            user_id, balance = result[0]
            bot.send_message(
                message.chat.id,
                f"👤 آيدي: {user_id}\n💰 الرصيد: {balance:,} ل.س"
            )
        else:
            bot.send_message(message.chat.id, "⚠️ لا يوجد مستخدم بهذا الآيدي")
    except ValueError:
        bot.send_message(message.chat.id, "⚠️ يرجى إدخال رقم صحيح")

def process_name_search(message):
    try:
        name = message.text.strip()
        results = safe_db_execute(
            """SELECT u.user_id, u.balance, p.username 
            FROM users u
            LEFT JOIN user_profiles p ON u.user_id = p.user_id
            WHERE p.username LIKE ? OR p.first_name LIKE ?""",
            (f"%{name}%", f"%{name}%")
        )
        if results:
            response = "نتائج البحث:\n\n"
            for user_id, balance, username in results:
                response += f"👤 {username or user_id}\n💰 {balance:,} ل.س\n\n"
            bot.send_message(message.chat.id, response)
        else:
            bot.send_message(message.chat.id, "⚠️ لا يوجد مستخدم بهذا الاسم")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('edit_prod_price_'))
def edit_product_price(call):
    product_id = call.data.split('_')[3]
    msg = bot.send_message(call.message.chat.id, "أرسل السعر الجديد للمنتج:")
    bot.register_next_step_handler(msg, process_edit_product_price, call, product_id)

def process_edit_product_price(message, call, product_id):
    try:
        new_price = float(message.text)
        if new_price <= 0:
            bot.send_message(message.chat.id, "❌ السعر يجب أن يكون أكبر من الصفر")
            return
        safe_db_execute("UPDATE manual_products SET price=? WHERE id=?", (new_price, product_id))
        bot.send_message(message.chat.id, "✅ تم تحديث سعر المنتج بنجاح")
        edit_manual_product(call)
    except ValueError:
        bot.send_message(message.chat.id, "❌ يرجى إدخال رقم صحيح")

@bot.callback_query_handler(func=lambda call: call.data.startswith('edit_prod_desc_'))
def edit_product_description(call):
    product_id = call.data.split('_')[3]
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add('⏭ حذف الوصف')
    msg = bot.send_message(
        call.message.chat.id, 
        "أرسل الوصف الجديد أو 'حذف الوصف':",
        reply_markup=markup
    )
    bot.register_next_step_handler(msg, process_edit_product_description, product_id)

def process_edit_product_description(message, product_id):
    new_desc = None if message.text == '⏭ حذف الوصف' else message.text
    safe_db_execute("UPDATE manual_products SET description=? WHERE id=?", (new_desc, product_id))
    if new_desc is None:
        bot.send_message(message.chat.id, "✅ تم حذف وصف المنتج")
    else:
        bot.send_message(message.chat.id, "✅ تم تحديث وصف المنتج")
    # لإعادة المستخدم إلى قائمة تعديل المنتج بعد التحديث
    # نحتاج إلى معرفة call_id الأصلي أو إعادة بنائه
    # الأفضل هو أن نجعل هذه الدالة لا تعتمد على 'call' مباشرة في نهايتها
    # ونعيد توجيهه إلى manage_manual_products أو غيرها.
    # ولكن إذا كنت تريد العودة لصفحة تعديل منتج محدد، ستحتاج لآيدي المنتج
    # سنقوم بإنشاء callback_data جديد يدويًا
    temp_call = types.CallbackQuery()
    temp_call.message = message
    temp_call.data = f'edit_manual_prod_{product_id}'
    edit_manual_product(temp_call) # استخدام temp_call

@bot.callback_query_handler(func=lambda call: call.data.startswith('delete_prod_'))
def delete_product_handler(call):
    try:
        product_id = call.data.split('_')[2]
        markup = types.InlineKeyboardMarkup()
        markup.row(
            types.InlineKeyboardButton("✅ نعم، احذف", callback_data=f'confirm_delete_{product_id}'),
            types.InlineKeyboardButton("❌ إلغاء", callback_data=f'cancel_delete_{product_id}')
        )
        product_name = safe_db_execute("SELECT name FROM manual_products WHERE id=?", (product_id,))[0][0]
        bot.edit_message_text(
            f"⚠️ هل أنت متأكد من حذف المنتج:\n{product_name}؟",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=markup
        )
    except Exception as e:
        bot.answer_callback_query(call.id, "❌ حدث خطأ في معالجة الطلب")
        print(f"Error in delete_product_handler: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('confirm_delete_'))
def confirm_delete_product(call):
    try:
        product_id = call.data.split('_')[2]
        safe_db_execute("DELETE FROM manual_products WHERE id=?", (product_id,))
        bot.edit_message_text(
            "✅ تم حذف المنتج بنجاح",
            call.message.chat.id,
            call.message.message_id
        )
        time.sleep(2)
        manage_manual_products(call)
    except Exception as e:
        bot.answer_callback_query(call.id, "❌ فشل في حذف المنتج")
        print(f"Error in confirm_delete_product: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('cancel_delete_'))
def cancel_delete_product(call):
    try:
        product_id = call.data.split('_')[2]
        temp_call = types.CallbackQuery() # إنشاء كول باك مؤقت للعودة
        temp_call.message = call.message
        temp_call.data = f'edit_manual_prod_{product_id}'
        edit_manual_product(temp_call)
    except Exception as e:
        bot.answer_callback_query(call.id, "❌ حدث خطأ في الإلغاء")
        print(f"Error in cancel_delete_product: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('toggle_prod_id_'))
def toggle_product_player_id(call):
    product_id = call.data.split('_')[3]
    current = safe_db_execute("SELECT requires_player_id FROM manual_products WHERE id=?", (product_id,))[0][0]
    new_value = not current
    safe_db_execute("UPDATE manual_products SET requires_player_id=? WHERE id=?", (new_value, product_id))
    status = "مطلوب" if new_value else "غير مطلوب"
    bot.answer_callback_query(call.id, f"✅ معرف اللاعب الآن {status}")
    edit_manual_product(call) 

@bot.callback_query_handler(func=lambda call: call.data.startswith('manual_cat_'))
def show_manual_products(call):
    category_id = call.data.split('_')[2]
    # تعديل: جلب المنتجات المفعلة فقط
    products = safe_db_execute("SELECT id, name, price FROM manual_products WHERE category_id=? AND is_active = TRUE ORDER BY price ASC", (category_id,))

    markup = types.InlineKeyboardMarkup()
    if not products:
        text = "⚠️ لا توجد منتجات في هذه الفئة."
    else:
        text = "اختر المنتج المطلوب :"
        for prod_id, prod_name, prod_price in products:
            syp_price = convert_to_syp(prod_price)
            markup.add(types.InlineKeyboardButton(
                f"{prod_name} - {syp_price:,} ل.س",
                callback_data=f'manual_prod_{prod_id}'
            ))
    
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data='back_to_manual_categories'))

    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id, 
        reply_markup=markup,
    )
    bot.answer_callback_query(call.id)
# دالة جديدة لمعالجة الرجوع إلى قائمة الفئات اليدوية
@bot.callback_query_handler(func=lambda call: call.data == 'back_to_manual_categories')
def back_to_manual_categories(call):
    categories = safe_db_execute("SELECT id, name FROM manual_categories WHERE is_active = TRUE")
    
    # تحديد عرض الصف ليكون 2
    markup = types.InlineKeyboardMarkup(row_width=2)

    if categories:
        # إنشاء قائمة أزرار الفئات
        category_buttons = [
            types.InlineKeyboardButton(cat_name, callback_data=f'manual_cat_{cat_id}') 
            for cat_id, cat_name in categories
        ]
        # إضافة الأزرار دفعة واحدة
        markup.add(*category_buttons)

    bot.edit_message_text(
        "اختر احد الفئات :",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('manual_prod_'))
def show_manual_product_details(call):
    product_id = call.data.split('_')[2]
    
    # 1. جلب category_id مع تفاصيل المنتج
    product_details = safe_db_execute("SELECT name, price, description, requires_player_id, category_id FROM manual_products WHERE id=?", (product_id,))
    
    if not product_details:
        bot.send_message(call.message.chat.id, "⚠️ المنتج غير متوفر")
        return
        
    name, price_usd, desc, requires_id, category_id = product_details[0]
    price_syp = convert_to_syp(price_usd)
    
    text = (
        f"🛍️ {name}\n"
        f"💵 السعر: {price_syp:,} ل.س\n"  
        f"📄 الوصف: {desc or 'لا يوجد وصف'}"
    )
    
    # 2. إنشاء لوحة المفاتيح
    markup = types.InlineKeyboardMarkup(row_width=2) # عرض الصف 2
    
    # 3. إنشاء الأزرار
    buy_button = types.InlineKeyboardButton("شراء الآن 🛒", callback_data=f'buy_manual_{product_id}')
    # زر الرجوع سيعيد المستخدم إلى قائمة المنتجات الخاصة بنفس الفئة
    back_button = types.InlineKeyboardButton("🔙 رجوع", callback_data=f'manual_cat_{category_id}')
    
    # 4. إضافة الأزرار إلى لوحة المفاتيح
    markup.add(buy_button, back_button)
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )

@bot.message_handler(func=lambda msg: msg.text == '🔥 Free Fire 1'and not is_button_disabled('freefire'))
def show_new_freefire_products(message):
    if is_bot_paused() and not is_admin(message.from_user.id):
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for pid, prod in FREE_FIRE_NEW_PRODUCTS.items():
        price_syp = convert_to_syp(prod['price_usd'])
        btn_text = f"{prod['name']} - {price_syp:,} ل.س"
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f'ff_new_offer_{pid}'))
    bot.send_message(message.chat.id, "🎮 عروض Free Fire المتاحة:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('ff_new_offer_'))
def handle_new_freefire_offer(call):
    user_id = call.from_user.id
    if user_processing_lock.get(user_id, False):
        bot.answer_callback_query(call.id, "لديك عملية قيد المعالجة بالفعل. الرجاء الانتظار.")
        return
    user_processing_lock[user_id] = True # قفل العملية

    try:
        prod_id = int(call.data.split('_')[-1])
        product = FREE_FIRE_NEW_PRODUCTS.get(prod_id)
        if not product:
            bot.answer_callback_query(call.id, "⚠️ العرض غير متوفر")
            user_processing_lock[user_id] = False
            return
        
        # تعديل الرسالة لإخفاء الزر وطلب ID اللاعب
        product_name = product['name']
        price_syp = convert_to_syp(product['price_usd'])
        
        updated_text = (
            f"🎮 عرض Free Fire 1:\n"
            f"📌 {product_name}\n"
            f"💰 السعر: {price_syp:,} ل.س\n\n"
        )
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=updated_text,
            reply_markup=None # إزالة الأزرار
        )

        msg = bot.send_message(
            call.message.chat.id,
            "أدخل ID أو رقم اللاعب :",
            reply_markup=types.ForceReply(selective=True)
        )
        bot.register_next_step_handler(msg, process_new_freefire_purchase, product)

    except Exception as e:
        print(f"خطأ: {str(e)}")
        bot.send_message(call.message.chat.id, "❌ حدث خطأ في اختيار العرض!")
        user_processing_lock[user_id] = False # تحرير القفل

def process_new_freefire_purchase(message, product):
    user_id = message.from_user.id
    try:
        player_id = message.text.strip()
        if not player_id.isdigit() or len(player_id) < 6:
            bot.send_message(message.chat.id, "❌ رقم اللاعب غير صالح! يجب أن يحتوي على 6 خانات على الأقل")
            return
        price_syp = convert_to_syp(product['price_usd'])
        if get_balance(user_id) < price_syp:
            bot.send_message(message.chat.id, f"⚠️ رصيدك غير كافي. السعر: {price_syp:,} ل.س")
            return
        markup = types.InlineKeyboardMarkup()
        markup.row(
            types.InlineKeyboardButton("✅ تأكيد الشراء", callback_data=f"ff_new_confirm_{product['item_id']}_{player_id}_{price_syp}_{product['name']}"),
            types.InlineKeyboardButton("❌ إلغاء", callback_data='cancel_purchase')
        )
        bot.send_message(
            message.chat.id,
            f"🛒 تأكيد عملية الشراء:\n\n"
            f"📌 العرض: {product['name']}\n"
            f"💰 السعر: {price_syp:,} ل.س\n"
            f"👤 آيدي اللاعب: {player_id}\n\n"
            f"هل أنت متأكد من المعلومات أعلاه؟",
            reply_markup=markup
        )
    except Exception as e:
        print(f"Error: {str(e)}")
        bot.send_message(message.chat.id, "❌ حدث خطأ غير متوقع!")
    finally:
        user_processing_lock[user_id] = False # تحرير القفل

@bot.callback_query_handler(func=lambda call: call.data.startswith('ff_new_confirm_'))
def confirm_new_freefire_purchase(call):
    user_id = call.from_user.id
    if user_processing_lock.get(user_id, False):
        bot.answer_callback_query(call.id, "لديك عملية قيد المعالجة بالفعل. الرجاء الانتظار.")
        return
    user_processing_lock[user_id] = True # قفل العملية

    try:
        # إخفاء أزرار التأكيد/الإلغاء
        bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=None # إزالة الأزرار
        )
        bot.answer_callback_query(call.id, "⏳ جاري معالجة طلبك...")

        parts = call.data.split('_')
        item_id, player_id, price_syp_str = parts[3], parts[4], parts[5]
        item_name = '_'.join(parts[6:]) # لإعادة بناء الاسم إذا كان يحتوي على مسافات
        price_syp = int(price_syp_str)
        
        username = f"@{call.from_user.username}" if call.from_user.username else "غير متوفر"
        user_name = f"{call.from_user.first_name or ''} {call.from_user.last_name or ''}".strip()
        
        if get_balance(user_id) < price_syp:
            raise ValueError("رصيدك غير كافي!")

        order_id_api = generate_order_id() # استخدام ID فريد لـ API
        payload = {
            "player_id": player_id,
            "item_id": item_id,
            "order_id": order_id_api
        }
        headers = {
            "Content-Type": "application/json",
            "authorization": FREE_FIRE_NEW_API_KEY
        }
        response = requests.post(
            f"{FREE_FIRE_NEW_API_BASE}/api/freefireTopup",
            json=payload,
            headers=headers,
            timeout=20
        )

        if response.status_code == 200:
            update_balance(user_id, -price_syp)
            order_id_db = log_user_order(
                user_id=user_id,
                order_type='freefire',
                product_id=item_id,
                product_name=item_name, # استخدم item_name لاسم المنتج
                price=price_syp,
                player_id=player_id,
                api_response=response.json()
            )
            bot.edit_message_text(
                f"✅ تمت عملية الشراء بنجاح!\n\n"
                f"📌 المنتج: {item_name}\n"
                f"🆔 آيدي اللاعب: {player_id}\n"
                f"💳 السعر: {price_syp:,} ل.س\n"
                f"📌 رقم العملية: {order_id_db}",
                call.message.chat.id,
                call.message.message_id
            )
            admin_msg = (
                f"🛒 عملية شراء جديدة\n"
                f" #Free_Fire_AllTopup\n\n"
                f"👤 الاسم: {user_name}\n"
                f"👤 المستخدم: {username}\n"
                f"🆔 ID: {user_id}\n"
                f"📌 العرض: {item_name}\n"
                f"🆔 اللاعب: {player_id}\n"
                f"💰 المبلغ: {price_syp} ل.س\n"
                f"📌 رقم العملية: {order_id_db}"
            )
            channel_id = get_notification_channel()
            if channel_id:
                try:
                    bot.send_message(channel_id, admin_msg)
                except Exception as e:
                    print(f"Failed to send to channel: {str(e)}")
                    bot.send_message(ADMIN_ID, f"فشل إرسال إلى القناة:\n\n{admin_msg}")
            else:
                bot.send_message(ADMIN_ID, admin_msg)
            bot.send_message(call.message.chat.id, "⬇️ القائمة الرئيسية", reply_markup=main_menu(call.from_user.id))
        else:
            error_msg = response.json().get('message', 'فشلت العملية دون تفاصيل')
            raise Exception(error_msg)

    except Exception as e:
        print(f"Confirm Error: {str(e)}")
        bot.edit_message_text(
            f"❌ حدث خطأ أثناء تنفيذ العملية! يرجى التواصل مع الدعم ",
            call.message.chat.id,
            call.message.message_id
        )
        bot.send_message(ADMIN_ID, f"⚠️ خطأ في عملية شراء Free Fire 1\nUser: {call.from_user.id}\nError: {str(e)}")
    finally:
        user_processing_lock[user_id] = False # تحرير القفل

@bot.callback_query_handler(func=lambda call: call.data.startswith('check_status_'))
def check_order_status(call):
    order_id = call.data.split('_')[2]
    order = safe_db_execute("SELECT api_response FROM user_orders WHERE id=?", (order_id,))
    if not order:
        bot.answer_callback_query(call.id, "❌ الطلب غير موجود")
        return
    try:
        api_response = json.loads(order[0][0])
        status = api_response.get('status', 'unknown')
        status_msg = {
            'pending': 'قيد المعالجة 🟡',
            'completed': 'مكتمل ✅',
            'failed': 'فشل ❌'
        }.get(status, 'حالة غير معروفة')
        bot.answer_callback_query(
            call.id,
            f"حالة الطلب: {status_msg}",
            show_alert=True
        )
    except Exception as e:
        bot.answer_callback_query(call.id, "❌ تعذر تحميل حالة الطلب")

def handle_api_error(call, error_msg, price_syp=None):
    try:
        error_log = f"Free Fire API Error - User: {call.from_user.id}, Error: {error_msg}"
        print(error_log)
        bot.edit_message_text(
            f"❌ فشلت العملية: {error_msg}",
            call.message.chat.id,
            call.message.message_id
        )
        if price_syp:
            update_balance(call.from_user.id, price_syp)
        bot.send_message(
            ADMIN_ID,
            f"⚠️ فشل في عملية Free Fire\n"
            f"User: {call.from_user.id}\n"
            f"Error: {error_msg}"
        )
    except Exception as e:
        print(f"Error in error handling: {str(e)}")

@bot.message_handler(func=lambda msg: msg.text == 'PUBG MOBILE ⚡'and not is_button_disabled('pubg'))
def pubg_main_menu(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, is_persistent=True)
    row_buttons = []
    # التحقق من أن الأزرار الفرعية ليست معطلة
    if not is_button_disabled('pubg_auto'):
        row_buttons.append('Auto ⚡')
    if not is_button_disabled('pubg_manual'):
        # تغيير اسم الزر لتمييزه
        row_buttons.append('شحن يدوي (ببجي) 👨🏻‍💻')

    if row_buttons:
        markup.row(*row_buttons)

    markup.row('🔙 الرجوع للقائمة الرئيسية')
    bot.send_message(message.chat.id, "يرجى اختيار نوع الشحن:", reply_markup=markup)

@bot.message_handler(func=lambda msg: msg.text == 'شحن يدوي (ببجي) 👨🏻‍💻' and not is_button_disabled('pubg'))
def show_pubg_manual_products(message):
    # إصلاح: إضافة شرط للتحقق من أن المنتج مفعل (is_active)
    products = safe_db_execute("SELECT id, name, price FROM manual_products WHERE category_id=? AND is_active = TRUE ORDER BY price ASC", (PUBG_MANUAL_CATEGORY_ID,))
    if not products:
        bot.send_message(message.chat.id, "⚠️ لا توجد منتجات PUBG يدوية متاحة حالياً")
        return
    markup = types.InlineKeyboardMarkup()
    for prod_id, name, price in products:
        syp_price = convert_to_syp(price)
        markup.add(types.InlineKeyboardButton(f"{name} - {syp_price:,} ل.س", callback_data=f'manual_prod_{prod_id}'))
    bot.send_message(message.chat.id,
                    f"تستغرق عملية المعالجة من 10 دقائق الى نصف ساعة \n"
                    f"اختر المنتج الذي تريده:\n",
                    reply_markup=markup)

# إضافة دالة جديدة لعرض منتجات فري فاير اليدوية
@bot.message_handler(func=lambda msg: msg.text == 'شحن يدوي (فري فاير) 👨🏻‍💻' and not is_button_disabled('freefire'))
def show_freefire_manual_products(message):
    # استخدام ID الفئة اليدوية الخاص بـ Free Fire
    products = safe_db_execute("SELECT id, name, price FROM manual_products WHERE category_id=? AND is_active = TRUE ORDER BY price ASC", (FREE_FIRE_MANUAL_CATEGORY_ID,))
    if not products:
        bot.send_message(message.chat.id, "⚠️ لا توجد منتجات Free Fire يدوية متاحة حالياً")
        return
    markup = types.InlineKeyboardMarkup()
    for prod_id, name, price in products:
        syp_price = convert_to_syp(price)
        markup.add(types.InlineKeyboardButton(f"{name} - {syp_price:,} ل.س", callback_data=f'manual_prod_{prod_id}'))
    bot.send_message(message.chat.id,
                    f"تستغرق عملية المعالجة من 10 دقائق الى نصف ساعة \n"
                    f"اختر المنتج الذي تريده:\n",
                    reply_markup=markup)

@bot.message_handler(func=lambda msg: msg.text == 'Auto ⚡' and not is_button_disabled('pubg'))
def show_topup_offers_handler(message):
    if is_bot_paused() and not is_admin(message.from_user.id):
        return
    if not PUBG_OFFERS or (LAST_PUBG_UPDATE and (time.time() - LAST_PUBG_UPDATE) > PUBG_UPDATE_INTERVAL):
        try:
            update_pubg_offers()
            if not PUBG_OFFERS:
                bot.send_message(message.chat.id, "⚠️ جاري تحديث العروض، يرجى المحاولة بعد قليل")
                return
        except Exception as e:
            print(f"Error updating PUBG offers: {str(e)}")
    if not PUBG_OFFERS:
        bot.send_message(message.chat.id, "⚠️ لا توجد عروض متاحة حالياً.")
        return
    try:
        markup = types.InlineKeyboardMarkup()
        for offer in sorted(PUBG_OFFERS, key=lambda x: convert_to_syp(x.get('unit_price', 0))):
            if offer.get('stock', 0) > 0:
                try:
                    price_syp = convert_to_syp(offer['unit_price'])
                    btn_text = f"{offer['title']} - {price_syp:,} ل.س"
                    markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"topup_{offer['id']}"))
                except Exception as e:
                    print(f"Skipping invalid offer: {str(e)}")
                    continue
        bot.send_message(message.chat.id, "🎮 عروض التعبئة المتاحة حالياً:", reply_markup=markup)
    except Exception as e:
        print(f"Error showing PUBG offers: {str(e)}")
        bot.send_message(message.chat.id, "❌ حدث خطأ في عرض العروض!")

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
    
    categories = safe_db_execute("SELECT id, name, is_active FROM manual_categories")
    for cat_id, cat_name, is_active in categories:
        status_icon = "✅" if is_active else "❌"
        toggle_icon = "👁️ إخفاء" if is_active else "👁️‍🗨️ إظهار"
        
        row = [
            types.InlineKeyboardButton(f"{status_icon} {cat_name}", callback_data=f'no_action_{cat_id}'), # زر لا يفعل شيء، للعرض فقط
            types.InlineKeyboardButton(toggle_icon, callback_data=f'toggle_cat_vis_{cat_id}'),
            types.InlineKeyboardButton("🗑️", callback_data=f'delete_manual_cat_{cat_id}')
        ]
        markup.row(*row)

    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data='manage_manual'))
    bot.edit_message_text(
        "إدارة الفئات اليدوية:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('toggle_cat_vis_'))
def toggle_category_visibility(call):
    category_id = call.data.split('_')[-1]
    

    safe_db_execute("UPDATE manual_categories SET is_active = NOT is_active WHERE id=?", (category_id,))
    
    current_status = safe_db_execute("SELECT is_active FROM manual_categories WHERE id=?", (category_id,))[0][0]
    status_msg = "تم إظهار الفئة" if current_status else "تم إخفاء الفئة"
    bot.answer_callback_query(call.id, status_msg)

    manage_manual_categories(call)

@bot.callback_query_handler(func=lambda call: call.data.startswith('topup_'))
def handle_topup_selection(call):
    user_id = call.from_user.id
    if user_processing_lock.get(user_id, False):
        bot.answer_callback_query(call.id, "لديك عملية قيد المعالجة بالفعل. الرجاء الانتظار.")
        return
    user_processing_lock[user_id] = True # قفل العملية

    try:
        offer_id = call.data.split('_')[1]
        offer = next((o for o in PUBG_OFFERS if str(o['id']) == offer_id), None)
        if not offer:
            bot.answer_callback_query(call.id, "⚠️ هذا العرض غير متوفر حالياً")
            user_processing_lock[user_id] = False
            return
        
        # تعديل الرسالة لإخفاء الزر وطلب ID اللاعب
        product_name = offer['title']
        price_syp = convert_to_syp(offer['unit_price'])
        
        updated_text = (
            f"🎮 عرض PUBG Mobile:\n"
            f"📌 {product_name}\n"
            f"💰 السعر: {price_syp:,} ل.س\n\n"
        )
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=updated_text,
            reply_markup=None # إزالة الأزرار
        )

        msg = bot.send_message(
            call.message.chat.id,
            "أدخل ID اللاعب :",
            reply_markup=types.ForceReply(selective=True)
        )
        bot.register_next_step_handler(msg, process_topup_purchase, offer)

    except Exception as e:
        print(f"Error in topup selection: {str(e)}")
        bot.send_message(call.message.chat.id, "❌ حدث خطأ في اختيار العرض!")
        user_processing_lock[user_id] = False # تحرير القفل

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
    categories = safe_db_execute("SELECT id, name FROM manual_categories ORDER BY name")
    markup = types.InlineKeyboardMarkup()
    if not categories:
        markup.add(types.InlineKeyboardButton("⚠️ لا توجد فئات، أضف فئة أولاً", callback_data='manage_manual_categories'))
    else:
        for cat_id, cat_name in categories:
            markup.add(types.InlineKeyboardButton(cat_name, callback_data=f'manage_prods_in_cat_{cat_id}'))
    
    markup.add(types.InlineKeyboardButton("➕ إضافة منتج جديد", callback_data='add_manual_product'))
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data='manage_manual'))

    bot.edit_message_text(
        "اختر فئة لعرض وإدارة منتجاتها:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )
@bot.callback_query_handler(func=lambda call: call.data.startswith('manage_prods_in_cat_'))
def manage_products_in_category(call):
    category_id = call.data.split('_')[-1]
    products = safe_db_execute("SELECT id, name, is_active FROM manual_products WHERE category_id=?", (category_id,))
    category_name = safe_db_execute("SELECT name FROM manual_categories WHERE id=?", (category_id,))[0][0]
    
    markup = types.InlineKeyboardMarkup()
    text = f"🛍️ منتجات فئة: *{category_name}*\n\n"

    if not products:
        text += "لا توجد منتجات في هذه الفئة."
    else:
        for prod_id, prod_name, is_active in products:
            status_icon = "✅" if is_active else "❌"
            markup.add(types.InlineKeyboardButton(
                f"{status_icon} {prod_name}",
                callback_data=f'edit_manual_prod_{prod_id}'
            ))

    markup.add(types.InlineKeyboardButton("➕ إضافة منتج لهذه الفئة", callback_data=f'add_prod_to_cat_{category_id}'))
    markup.add(types.InlineKeyboardButton("🔙 رجوع لقائمة الفئات", callback_data='manage_manual_products'))
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode='Markdown'
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('add_prod_to_cat_'))
def add_product_to_category_handler(call):
    category_id = call.data.split('_')[-1]
    msg = bot.send_message(call.message.chat.id, "أرسل اسم المنتج الجديد:")
    bot.register_next_step_handler(msg, process_product_name, category_id)

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

@bot.callback_query_handler(func=lambda call: call.data == 'deduct_balance' and is_admin(call.from_user.id))
def handle_deduct_balance(call):
    msg = bot.send_message(call.message.chat.id, "أرسل آيدي المستخدم والمبلغ المطلوب خصمه (مثال: 123456789 50000):")
    bot.register_next_step_handler(msg, process_balance_deduction)

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
        message.text = name
        msg = bot.send_message(message.chat.id, "أرسل سعر المنتج بالليرة السورية:")
        bot.register_next_step_handler(msg, process_product_price, category_id, name)
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ: {str(e)}")

def process_product_price(message, category_id, name):
    try:
        if message.text == '⏭ تخطي الوصف':
            skip_product_description(message, category_id, name, 0)
            return
        price_usd = float(message.text)
        if price_usd <= 0:
            bot.send_message(message.chat.id, "❌ السعر يجب أن يكون أكبر من الصفر")
            return
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        markup.add('⏭ تخطي الوصف')
        msg = bot.send_message(
            message.chat.id, 
            "أرسل وصف المنتج (اختياري) أو اضغط 'تخطي الوصف':",
            reply_markup=markup
        )
        bot.register_next_step_handler(msg, process_product_description, category_id, name, price_usd)
    except ValueError:
        bot.send_message(message.chat.id, "❌ يرجى إدخال رقم صحيح للسعر")

def send_rejection_notification(user_id, order_id, reason, refund_amount):
    try:
        order = safe_db_execute("""
            SELECT product_name 
            FROM manual_orders 
            WHERE id=?
        """, (order_id,))
        if order:
            product_name = order[0][0]
            message = (
                f"⚠️ تم رفض طلبك\n\n"
                f"🆔 رقم الطلب: {order_id}\n"
                f"📦 المنتج: {product_name}\n"
                f"💵 المبلغ المسترجع: {refund_amount} ل.س\n"
                f"📝 سبب الرفض: {reason}\n\n"
                f"للاستفسار، يرجى التواصل مع الإدارة"
            )
            bot.send_message(user_id, message)
    except Exception as e:
        print(f"فشل في إرسال إشعار الرفض: {str(e)}")

def process_product_description(message, category_id, name, price):
    if message.text == '⏭ تخطي الوصف':
        description = None
    else:
        description = message.text
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
        update_balance(user_id, amount)
        try:
            if call.message.photo:
                bot.edit_message_caption(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    caption=f"{call.message.caption}\n\n✅ تمت الموافقة على الطلب بواسطة @{call.from_user.username}"
                )
            else:
                bot.edit_message_text(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    text=f"{call.message.text}\n\n✅ تمت الموافقة على الطلب بواسطة @{call.from_user.username}"
                )
        except Exception as edit_error:
            print(f"Error editing message: {str(edit_error)}")
        bot.answer_callback_query(call.id, f"✅ تمت الموافقة على الطلب وإضافة {amount} ل.س")
        bot.send_message(
            user_id,
            f"🎉 تمت الموافقة على طلبك!\n\n"
            f"💰 تم إضافة {amount} ل.س إلى رصيدك\n"
            f"💳 رصيدك الحالي: {get_balance(user_id)} ل.س"
        )
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ خطأ: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data.startswith(('approve_recharge_', 'reject_recharge_')))
def handle_recharge_decision(call):
    try:
        parts = call.data.split('_')
        action = parts[0]
        request_id = int(parts[2])

        request = safe_db_execute('''
            SELECT user_id, amount_syp, address_id, status 
            FROM recharge_requests 
            WHERE id = ?
        ''', (request_id,))
        
        if not request:
            bot.answer_callback_query(call.id, "⚠️ الطلب غير موجود.")
            return
        
        user_id, amount, address_id, status = request[0]
        if status != 'pending_admin':
            bot.answer_callback_query(call.id, f"⚠️ هذا الطلب تمت معالجته مسبقاً ({status}).")
            return

        if action == 'approve':
            update_balance(user_id, amount)
            # تحديث المبلغ المستخدم للعنوان إذا كان له حد يومي
            safe_db_execute('''
                UPDATE payment_addresses 
                SET daily_used = daily_used + ? 
                WHERE id = ? AND daily_limit IS NOT NULL
            ''', (amount, address_id))
            safe_db_execute("UPDATE recharge_requests SET status = 'completed' WHERE id = ?", (request_id,))
            
            bot.send_message(
                user_id,
                f"🎉 تمت الموافقة على طلب الشحن!\n\n💰 تم إضافة {amount:,} ل.س إلى رصيدك."
            )
            bot.answer_callback_query(call.id, "✅ تمت الموافقة بنجاح.")
        
        else: # action == 'reject'
            safe_db_execute("UPDATE recharge_requests SET status = 'rejected' WHERE id = ?", (request_id,))
            bot.send_message(
                user_id,
                f"❌ تم رفض طلب الشحن الخاص بك.\n\n"
                f"يرجى التواصل مع الدعم لمزيد من المعلومات."
            )
            bot.answer_callback_query(call.id, "❌ تم رفض الطلب.")

        # تحديث رسالة الأدمن
        new_status_text = '✅ تمت الموافقة' if action == 'approve' else '❌ تم الرفض'
        new_text = f"{call.message.caption or call.message.text}\n\n---\nتمت المعالجة بواسطة: @{call.from_user.username}\nالحالة: {new_status_text}"
        
        if call.message.photo:
            bot.edit_message_caption(chat_id=call.message.chat.id, message_id=call.message.message_id, caption=new_text, reply_markup=None)
        else:
            bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text=new_text, reply_markup=None)

    except Exception as e:
        print(f"Error in handle_recharge_decision: {str(e)}")
        bot.answer_callback_query(call.id, "❌ حدث خطأ أثناء المعالجة.")

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

@bot.callback_query_handler(func=lambda call: call.data.startswith('confirm_purchase_'))
def handle_purchase_confirmation(call):
    try:
        # إخفاء أزرار التأكيد/الإلغاء
        bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=None # إزالة الأزرار
        )
        bot.answer_callback_query(call.id, "⏳ جاري معالجة طلبك...")

        parts = call.data.split('_')
        offer_id = parts[2]
        player_id = parts[3]
        price = int(parts[4])
        user_id = call.from_user.id
        if get_balance(user_id) >= price:
            update_balance(user_id, -price)
            # هنا يجب أن يتم استدعاء دالة الشراء الحقيقية
            bot.edit_message_text("✅ تمت عملية الشراء بنجاح!", call.message.chat.id, call.message.message_id)
        else:
            bot.answer_callback_query(call.id, "❌ رصيدك غير كافي!")

    except Exception as e:
        print(f"Error in purchase confirmation: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data == 'cancel_purchase')
def handle_purchase_cancel(call):
    user_id = call.from_user.id # احصل على ID المستخدم
    
    # إزالة الأزرار وتعديل الرسالة عند الإلغاء
    bot.edit_message_text(
        "❌ تم إلغاء العملية", 
        call.message.chat.id, 
        call.message.message_id,
        reply_markup=None
    )
    
    # تحرير القفل للمستخدم
    if user_id in user_processing_lock:
        user_processing_lock[user_id] = False 
    
    bot.answer_callback_query(call.id, "تم إلغاء العملية.") # لإزالة حالة التحميل من الزر في واجهة المستخدم

@bot.message_handler(func=lambda msg: msg.text == 'طلباتي 🗂️')
def show_user_orders(message):
    user_id = message.from_user.id
    orders = safe_db_execute("""
        SELECT id, order_type, product_name, price, status, created_at 
        FROM user_orders 
        WHERE user_id=?
        ORDER BY created_at DESC
        LIMIT 10
    """, (user_id,))
    if not orders:
        bot.send_message(message.chat.id, "📭 لا توجد طلبات سابقة")
        return
    markup = types.InlineKeyboardMarkup()
    for order_id, order_type, product_name, price, status, created_at in orders:
        status_icon = "🟡" if status == 'pending' else "✅" if status == 'completed' else "❌"
        type_icon = "🛍️" if order_type == 'manual' else "⚡" if order_type == 'pubg' else "🔥" if order_type == 'freefire' else "📦"
        markup.add(types.InlineKeyboardButton(
            f"{type_icon} {status_icon} {product_name} - {price} ل.س ({created_at.split()[0]})",
            callback_data=f'view_my_order_{order_id}'
        ))
    bot.send_message(message.chat.id, "📋 طلباتك السابقة:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('view_my_order_'))
def view_user_order_details(call):
    order_id = call.data.split('_')[3]
    try:
        order = safe_db_execute("""
            SELECT order_type, product_name, price, status, created_at, player_id
            FROM user_orders 
            WHERE id=? AND user_id=?
        """, (order_id, call.from_user.id))
        if not order:
            bot.send_message(call.message.chat.id, "⚠️ الطلب غير موجود")
            return
        order_type, product_name, price, status, created_at, player_id = order[0]
        status_text = {
            'pending': 'قيد المعالجة 🟡',
            'completed': 'مكتمل ✅',
            'rejected': 'مرفوض ❌'
        }.get(status, status)
        type_text = {
            'manual': 'منتج يدوي 🛍️',
            'pubg': 'PUBG MOBILE ⚡',
            'freefire': 'FREE FIRE 🔥',
            'freefire2': 'FREE FIRE 2 🔥' # أضفنا هذا
        }.get(order_type, order_type)
        text = (
            f"📦 تفاصيل الطلب #{order_id}\n\n"
            f"📌 النوع: {type_text}\n"
            f"🛒 المنتج: {product_name}\n"
            f"💵 السعر: {price} ل.س\n"
            f"🔄 الحالة: {status_text}\n"
            f"📅 التاريخ: {created_at}\n"
            f"{f'🎮 معرف اللاعب: {player_id}' if player_id else ''}"
        )
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id
        )
    except Exception as e:
        print(f"Error in view_user_order_details: {str(e)}")
        bot.send_message(call.message.chat.id, "⚠️ حدث خطأ في عرض تفاصيل الطلب")

@bot.callback_query_handler(func=lambda call: call.data.startswith('buy_manual_'))
def handle_manual_purchase(call):
    user_id = call.from_user.id
    if user_processing_lock.get(user_id, False):
        bot.answer_callback_query(call.id, "لديك عملية قيد المعالجة بالفعل. الرجاء الانتظار.")
        return
    user_processing_lock[user_id] = True # قفل العملية

    try:
        product_id = call.data.split('_')[2]
        product = safe_db_execute("SELECT name, price, requires_player_id FROM manual_products WHERE id=?", (product_id,))
        if not product:
            bot.send_message(call.message.chat.id, "⚠️ المنتج غير متوفر")
            user_processing_lock[user_id] = False
            return
        name, price_usd, requires_id = product[0]
        price_syp = convert_to_syp(price_usd)
        balance = get_balance(user_id)
        if balance < price_syp:
            bot.send_message(call.message.chat.id, f"⚠️ رصيدك غير كافي. السعر: {price_syp} ل.س | رصيدك: {balance} ل.س")
            user_processing_lock[user_id] = False
            return
        
        # تعديل الرسالة لإظهار التفاصيل وطلب الكمية/المعرف
        updated_text = (
            f"🛍️ {name}\n"
            f"💵 السعر: {price_syp:,} ل.س\n\n"
        )
        if requires_id:
            updated_text += "أدخل ID أو رقم اللاعب:"
        else:
            updated_text += "أدخل الكمية المطلوبة:"

        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=updated_text,
            reply_markup=None # إزالة الأزرار
        )

        if requires_id:
            msg = bot.send_message(call.message.chat.id, "أدخل ID أو رقم اللاعب :", reply_markup=types.ForceReply(selective=True))
            bot.register_next_step_handler(msg, lambda m: process_player_id_for_manual_purchase(m, product_id, price_usd, user_id))
        else:
            msg = bot.send_message(call.message.chat.id, "أدخل الكمية المطلوبة:", reply_markup=types.ForceReply(selective=True))
            bot.register_next_step_handler(msg, lambda m: process_manual_quantity_purchase(m, product_id, price_usd, user_id))

    except Exception as e:
        bot.send_message(call.message.chat.id, f"❌ حدث خطأ: {str(e)}")
        user_processing_lock[user_id] = False # تحرير القفل

def process_player_id_for_manual_purchase(message, product_id, price_usd, user_id):
    player_id = message.text.strip()
    if not player_id:
        bot.send_message(message.chat.id, "❌ يجب إدخال معرف اللاعب")
        user_processing_lock[user_id] = False
        return
    
    product_name = safe_db_execute('SELECT name FROM manual_products WHERE id=?', (product_id,))[0][0]
    price_syp = convert_to_syp(price_usd)

    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("✅ تأكيد الشراء", callback_data=f'confirm_manual_{product_id}_{price_syp}_{player_id}'),
        types.InlineKeyboardButton("❌ إلغاء", callback_data='cancel_purchase')
    )
    bot.send_message(
        message.chat.id,
        f"🛒 تأكيد عملية الشراء اليدوية:\n\n"
        f"📌 المنتج: {product_name}\n"
        f"💰 السعر: {price_syp:,} ل.س\n"
        f"👤 آيدي اللاعب: {player_id}\n\n"
        f"هل أنت متأكد من المعلومات أعلاه؟",
        reply_markup=markup
    )
    
    user_processing_lock[user_id] = False

def process_manual_quantity_purchase(message, product_id, price_usd, user_id):
    try:
        quantity = int(message.text.strip())
        if quantity <= 0:
            bot.send_message(message.chat.id, "❌ الكمية يجب أن تكون أكبر من الصفر!")
            user_processing_lock[user_id] = False
            return
        
        product_name = safe_db_execute('SELECT name FROM manual_products WHERE id=?', (product_id,))[0][0]
        total_price_syp = convert_to_syp(price_usd) * quantity

        markup = types.InlineKeyboardMarkup()
        markup.row(
            types.InlineKeyboardButton("✅ تأكيد الشراء", callback_data=f'confirm_manual_qty_{product_id}_{total_price_syp}_{quantity}'),
            types.InlineKeyboardButton("❌ إلغاء", callback_data='cancel_purchase')
        )
        bot.send_message(
            message.chat.id,
            f"🛒 تأكيد عملية الشراء اليدوية:\n\n"
            f"📌 المنتج: {product_name} (الكمية: {quantity})\n"
            f"💰 السعر الإجمالي: {total_price_syp:,} ل.س\n\n"
            f"هل أنت متأكد من المعلومات أعلاه؟",
            reply_markup=markup
        )

        user_processing_lock[user_id] = False

    except ValueError:
        bot.send_message(message.chat.id, "❌ يرجى إدخال رقم صحيح للكمية!")
        user_processing_lock[user_id] = False
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ: {str(e)}")
        user_processing_lock[user_id] = False

@bot.callback_query_handler(func=lambda call: call.data.startswith(('confirm_manual_', 'confirm_manual_qty_')))
def confirm_manual_purchase(call):
    user_id = call.from_user.id
    if user_processing_lock.get(user_id, False):
        bot.answer_callback_query(call.id, "لديك عملية قيد المعالجة بالفعل. الرجاء الانتظار.")
        return
    user_processing_lock[user_id] = True # قفل العملية

    try:
        # إخفاء أزرار التأكيد/الإلغاء
        bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=None
        )
        bot.answer_callback_query(call.id, "⏳ جاري معالجة طلبك...")

        parts = call.data.split('_')
        product_id = None
        price_syp = 0 
        player_id = None
        quantity = 1 

        if call.data.startswith('confirm_manual_qty_'):
            if len(parts) < 5:
                raise ValueError("Callback data for quantity purchase is incomplete.")
            product_id = int(parts[3]) 
            price_syp = int(parts[4])
            quantity = int(parts[5]) 


        elif call.data.startswith('confirm_manual_'):
            if len(parts) < 4: 
                raise ValueError("Callback data for single item purchase is incomplete.")
            product_id = int(parts[2]) 
            price_syp = int(parts[3])
            if len(parts) > 4: 
                player_id = parts[4]

        else:
            raise ValueError("Unknown manual purchase callback data format.")

        product_name_query = safe_db_execute('SELECT name FROM manual_products WHERE id=?', (product_id,))
        if not product_name_query:
            raise ValueError("Product not found in database.")
        product_name = product_name_query[0][0]

        if get_balance(user_id) < price_syp:
            raise ValueError(f"رصيدك غير كافي. السعر: {price_syp:,} ل.س")

        if not update_balance(user_id, -price_syp):
            raise Exception("فشل في خصم الرصيد")

        order_id = log_user_order(
            user_id=user_id,
            order_type='manual',
            product_id=product_id,
            product_name=f"{product_name}" + (f" (الكمية: {quantity})" if quantity > 1 else ""),
            price=price_syp,
            player_id=player_id
        )

        send_order_confirmation(user_id, order_id, product_name, price_syp, player_id)
        notify_admin(order_id, call.from_user, product_name, price_syp, player_id, order_type='manual')

    except ValueError as ve:
        error_message = f"❌ فشلت عملية الشراء: {str(ve)}"
        bot.send_message(user_id, error_message)
        bot.send_message(ADMIN_ID, f"⚠️ خطأ في عملية الشراء اليدوية للمستخدم {user_id}: {str(ve)}")
    except Exception as e:
        error_message = f"❌ فشلت عملية الشراء: حدث خطأ غير متوقع. يرجى التواصل مع الدعم. {str(e)}"
        bot.send_message(user_id, error_message)
        bot.send_message(
            ADMIN_ID, 
            f"⚠️ فشل في عملية الشراء اليدوية للمستخدم {user_id}: {str(e)}\n"
            f"يرجى التحقق من السجل."
        )
    finally:
        if user_id in user_processing_lock:
            user_processing_lock[user_id] = False # تحرير القفل دائمًا


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
        FROM user_orders 
        WHERE id=?
    """, (order_id,))[0]
    user_id, product_name, price, player_id, created_at, status, admin_note = order
    text = (
        f"📦 تفاصيل الطلب 🆔{order_id}\n\n"
        f"👤 المستخدم: {user_id}\n"
        f"📦 المنتج: {product_name}\n"
        f"💵 المبلغ: {price} ل.س\n"
        f"📅 التاريخ: {created_at}\n"
        f"🔄 الحالة: {'🟡 قيد المعالجة' if status == 'pending' else '✅ مكتمل' if status == 'completed' else '❌ مرفوض'}\n"
        f"{f'🎮 معرف اللاعب: {player_id}' if player_id else ''}\n"
        f"{f'📝 ملاحظة الأدمن: {admin_note}' if admin_note else ''}"
    )
    markup = types.InlineKeyboardMarkup()
    if status == 'pending':
        markup.row(
            types.InlineKeyboardButton("✅ إتمام الطلب وإرسال رسالة", callback_data=f'complete_with_msg_{order_id}'),
            types.InlineKeyboardButton("❌ رفض الطلب", callback_data=f'reject_order_{order_id}')
        )
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data='pending_orders'))
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('complete_order_'))
def complete_order(call):
    try:
        order_id = call.data.split('_')[2]
        admin_id = call.from_user.id
        if log_order_status_update(order_id, 'completed', admin_id, "تمت الموافقة من الأدمن"):
            order = safe_db_execute("""
                SELECT user_id, product_name, price, player_id 
                FROM user_orders 
                WHERE id=?
            """, (order_id,))[0]
            user_id, product_name, price, player_id = order
            notify_user_of_status_change(user_id, order_id, 'completed')
            try:
                new_text = (
                    f"✅ تم إتمام الطلب (بواسطة @{call.from_user.username})\n\n"
                    f"🆔 رقم الطلب: {order_id}\n"
                    f"👤 المستخدم: {user_id}\n"
                    f"📦 المنتج: {product_name}\n"
                    f"💵 المبلغ: {price} ل.س\n"
                    f"{f'🎮 معرف اللاعب: {player_id}' if player_id else ''}"
                )
                bot.edit_message_text(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    text=new_text,
                    reply_markup=None
                )
            except Exception as e:
                print(f"Error updating admin message: {str(e)}")
            bot.answer_callback_query(call.id, "✅ تم إتمام الطلب بنجاح")
        else:
            bot.answer_callback_query(call.id, "❌ فشل في إتمام الطلب")
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ حدث خطأ: {str(e)}")
        print(f"Error in complete_order: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('complete_with_msg_'))
def handle_complete_with_message(call):
    order_id = call.data.split('_')[3]
    msg = bot.send_message(
        call.message.chat.id,
        f"✏️ أرسل الرسالة المخصصة للمستخدم لطلب #{order_id}:\n"
        "(أو اكتب /skip لعدم إرسال رسالة)",
        reply_markup=types.ForceReply(selective=True)
    )
    bot.register_next_step_handler(
        msg, 
        process_custom_message, 
        order_id, 
        call.from_user.id,
        call.message.message_id
    )

def process_custom_message(message, order_id, admin_id, admin_msg_id):
    try:
        custom_message = None if message.text == '/skip' else message.text
        success = log_order_status_update(order_id, 'completed', admin_id, "تمت الموافقة من الأدمن")
        if not success:
            bot.send_message(message.chat.id, "❌ فشل في تحديث حالة الطلب!")
            return
        order_details = safe_db_execute("""
            SELECT user_id, product_name, price, player_id 
            FROM user_orders 
            WHERE id=?
        """, (order_id,))[0]
        user_id, product_name, price, player_id = order_details
        user_message = (
            f"🎉 تم إكمال طلبك بنجاح!\n\n"
            f"🆔 رقم الطلب: {order_id}\n"
            f"📦 المنتج: {product_name}\n"
            f"💵 المبلغ: {price} ل.س\n"
            f"{f'🎮 معرف اللاعب: {player_id}' if player_id else ''}\n\n"
            f"{custom_message if custom_message else 'يتم العمل على تنفيذ طلبك ستصلك رسالة قريباً'}"
        )
        try:
            bot.send_message(user_id, user_message)
        except Exception as e:
            print(f"Failed to notify user: {str(e)}")
            bot.send_message(ADMIN_ID, f"⚠️ فشل إرسال الرسالة للمستخدم {user_id}")
        updated_text = (
            f"✅ تم إتمام الطلب بواسطة @{message.from_user.username}\n\n"
            f"🆔 رقم الطلب: {order_id}\n"
            f"👤 المستخدم: {user_id}\n"
            f"📦 المنتج: {product_name}\n"
            f"{f'📩 الرسالة المرسلة: {custom_message}' if custom_message else '🚫 لم يتم إرسال رسالة مخصصة'}"
        )
        try:
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=admin_msg_id,
                text=updated_text,
                reply_markup=None
            )
        except Exception as e:
            print(f"Error updating admin message: {str(e)}")
        bot.send_message(message.chat.id, "✅ تم إتمام الطلب وإرسال الرسالة للمستخدم")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ: {str(e)}")

def process_completion_message(message, order_id, admin_id, admin_msg_id):
    try:
        custom_message = None if message.text == '/skip' else message.text
        success = log_order_status_update(order_id, 'completed', admin_id, "تمت الموافقة من الأدمن")
        if not success:
            bot.send_message(message.chat.id, "❌ فشل في تحديث حالة الطلب!")
            return
        order = safe_db_execute("""
            SELECT user_id, product_name, price, player_id 
            FROM user_orders 
            WHERE id=?
        """, (order_id,))[0]
        user_id, product_name, price, player_id = order
        try:
            notification = (
                f"🎉 تم إكمال طلبك بنجاح!\n\n"
                f"🆔 رقم الطلب: {order_id}\n"
                f"📦 المنتج: {product_name}\n"
                f"💵 المبلغ: {price} ل.س\n"
                + (f"🎮 معرف اللاعب: {player_id}\n\n" if player_id else "\n")
                + (f"📬 رسالة من الإدارة:\n{custom_message}" if custom_message else "تمت العملية بنجاح ✅")
            )
            bot.send_message(user_id, notification)
        except Exception as e:
            print(f"فشل في إرسال الإشعار للمستخدم: {str(e)}")
            bot.send_message(ADMIN_ID, f"⚠️ فشل في إرسال إشعار للمستخدم {user_id}")
        try:
            new_text = (
                f"✅ تم إتمام الطلب (بواسطة @{message.from_user.username})\n\n"
                f"🆔 رقم الطلب: {order_id}\n"
                f"👤 المستخدم: {user_id}\n"
                f"📦 المنتج: {product_name}\n"
                f"💵 المبلغ: {price} ل.س\n"
                f"{f'🎮 معرف اللاعب: {player_id}' if player_id else ''}\n"
                f"{f'📝 الرسالة المرسلة: {custom_message}' if custom_message else ''}"
            )
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=admin_msg_id,
                text=new_text,
                reply_markup=None
            )
        except Exception as e:
            print(f"Error updating admin message: {str(e)}")
        bot.send_message(message.chat.id, f"✅ تم إتمام الطلب #{order_id} وإرسال الرسالة للمستخدم")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('reject_order_'))
def reject_order(call):
    order_id = call.data.split('_')[2]
    msg = bot.send_message(call.message.chat.id, "أرسل سبب الرفض:")
    bot.register_next_step_handler(msg, process_reject_reason, order_id, call.from_user.id, call.message.message_id)

def process_reject_reason(message, order_id, admin_id, admin_message_id):
    try:
        reason = message.text if message.text else "لا يوجد سبب محدد"
        success = log_order_status_update(order_id, 'rejected', admin_id, reason)
        if not success:
            bot.send_message(message.chat.id, "❌ فشل في تحديث حالة الطلب!")
            return
        order = safe_db_execute("""
            SELECT user_id, price 
            FROM user_orders 
            WHERE id=?
        """, (order_id,))
        if order:
            user_id, price = order[0]
            update_balance(user_id, price)
            try:
                notify_user_of_status_change(user_id, order_id, 'rejected', reason)
            except Exception as e:
                print(f"فشل في إرسال إشعار الرفض للمستخدم: {str(e)}")
        try:
            order_details = safe_db_execute("""
                SELECT product_name, price, player_id 
                FROM user_orders 
                WHERE id=?
            """, (order_id,))[0]
            product_name, price, player_id = order_details
            new_text = (
                f"❌ تم رفض الطلب (بواسطة @{message.from_user.username})\n\n"
                f"🆔 رقم الطلب: {order_id}\n"
                f"📦 المنتج: {product_name}\n"
                f"💵 المبلغ: {price} ل.س\n"
                f"📝 سبب الرفض: {reason}\n"
                f"{f'🎮 معرف اللاعب: {player_id}' if player_id else ''}"
            )
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=admin_message_id,
                text=new_text,
                reply_markup=None
            )
        except Exception as e:
            print(f"فشل في تحديث رسالة الأدمن: {str(e)}")
            bot.send_message(
                message.chat.id,
                f"❌ تعذر تحديث الرسالة الأصلية، إليك تفاصيل الرفض:\n{new_text}"
            )
        bot.send_message(
            message.chat.id,
            f"✅ تم رفض الطلب رقم {order_id} بنجاح"
        )
    except Exception as e:
        bot.send_message(
            message.chat.id,
            f"❌ حدث خطأ أثناء رفض الطلب: {str(e)}"
        )

@bot.callback_query_handler(func=lambda call: call.data.startswith('confirm_topup_'))
def handle_topup_confirmation(call):
    user_id = call.from_user.id
    if user_processing_lock.get(user_id, False):
        bot.answer_callback_query(call.id, "لديك عملية قيد المعالجة بالفعل. الرجاء الانتظار.")
        return
    user_processing_lock[user_id] = True # قفل العملية

    try:
        # إخفاء الأزرار فوراً وتغيير الرسالة
        bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=None
        )
        bot.answer_callback_query(call.id, "⏳ جاري معالجة طلبك...")

        parts = call.data.split('_')
        offer_id = parts[2]
        player_id = parts[3]
        price_syp = int(parts[4])
        
        username = f"@{call.from_user.username}" if call.from_user.username else "غير متوفر"
        user_name = f"{call.from_user.first_name or ''} {call.from_user.last_name or ''}".strip()
        
        offer = next((o for o in PUBG_OFFERS if str(o['id']) == offer_id), None)
        if not offer:
            raise ValueError("العرض غير متوفر")
        if get_balance(user_id) < price_syp:
            raise ValueError("رصيدك غير كافي")
        purchase_response = requests.post(
            f"{BASE_URL}topup/pubgMobile/offers/{offer_id}/purchase",
            json={"quantity": 1, "player_id": player_id},
            headers={'X-API-Key': G2BULK_API_KEY},
            timeout=15
        )

        if purchase_response.status_code == 200:
            update_balance(user_id, -price_syp)
            result = purchase_response.json()
            # تسجيل الطلب
            order_id_db = log_user_order(
                user_id=user_id,
                order_type='pubg',
                product_id=offer_id,
                product_name=offer['title'],
                price=price_syp,
                player_id=player_id,
                api_response=result
            )

            success_msg = (
                f"✅ تمت عملية الشراء بنجاح!\n\n"
                f"📌 المنتج: {offer['title']}\n"
                f"👤 آيدي اللاعب: {player_id}\n"
                f"💳 السعر: {price_syp:,} ل.س\n"
                f"🆔 رقم العملية: {result.get('topup_id', 'غير متوفر')}"
            )
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=success_msg
            )
            admin_msg = (
                f"🛒 عملية شراء جديدة\n"
                f" #PUBG_Mobile\n\n"
                f"👤 الاسم: {user_name}\n"
                f"👤 المستخدم: {username}\n"
                f"🆔 ID: {user_id}\n"
                f"🎮 العرض: {offer['title']}\n"
                f"🆔 اللاعب: {player_id}\n"
                f"💰 المبلغ: {price_syp:,} ل.س\n"
                f"📌 رقم العملية: {result.get('topup_id', 'غير متوفر')}"
            )
            channel_id = get_notification_channel()
            if channel_id:
                try:
                    bot.send_message(channel_id, admin_msg)
                except Exception as e:
                    print(f"Failed to send to channel: {str(e)}")
                    bot.send_message(ADMIN_ID, f"فشل إرسال إلى القناة:\n\n{admin_msg}")
            else:
                bot.send_message(ADMIN_ID, admin_msg)
            bot.send_message(call.message.chat.id, "⬇️ القائمة الرئيسية", reply_markup=main_menu(call.from_user.id))
        else:
            error_msg = purchase_response.json().get('message', 'فشلت العملية دون تفاصيل')
            raise Exception(error_msg)

    except Exception as e:
        error_msg = f"❌ فشلت العملية "
        try:
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=error_msg
            )
        except:
            bot.send_message(call.message.chat.id, error_msg)
    finally:
        user_processing_lock[user_id] = False # تحرير القفل

@bot.callback_query_handler(func=lambda call: call.data.startswith('cancel_topup_'))
def handle_topup_cancel(call):
    # إزالة الأزرار وتعديل الرسالة عند الإلغاء
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="❌ تم إلغاء العملية",
        reply_markup=None
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
            f"{f'🎮 معرف اللاعب: {player_id}' if player_id else ''}\n"
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
        if search_term.isdigit():
            orders = safe_db_execute("""
                SELECT id, user_id, product_name, price, status 
                FROM manual_orders 
                WHERE id=?
                ORDER BY created_at DESC
            """, (int(search_term),))
        else:
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

# =================================================================
# |||           بداية دوال إدارة طرق الدفع الجديدة              |||
# =================================================================

@bot.callback_query_handler(func=lambda call: call.data == 'manage_payment_methods' and is_admin(call.from_user.id))
def handle_manage_payment_methods(call):
    markup = types.InlineKeyboardMarkup(row_width=2)
    methods = safe_db_execute("SELECT id, name, type, is_active FROM payment_methods ORDER BY id")
    
    for method_id, name, method_type, is_active in methods:
        status_icon = "✅" if is_active else "⏸️"
        markup.add(types.InlineKeyboardButton(
            f"{status_icon} {name}",
            callback_data=f'view_method_{method_id}'
        ))
    
    markup.add(types.InlineKeyboardButton("➕ إضافة طريقة دفع جديدة", callback_data='add_payment_method'))
    recharge_disabled = safe_db_execute("SELECT value FROM bot_settings WHERE key='recharge_disabled'")[0][0] == '1'
    toggle_text = "▶️ تفعيل خدمة الشحن" if recharge_disabled else "⏸️ تعطيل خدمة الشحن"
    markup.add(types.InlineKeyboardButton(toggle_text, callback_data='toggle_recharge_service'))
    
    # ================== الزر الجديد ==================
    markup.add(types.InlineKeyboardButton("🧹 تنظيف طلبات الشحن المعلقة", callback_data='clean_pending_recharges'))
    # ===============================================

    markup.add(types.InlineKeyboardButton("🔙 رجوع للوحة التحكم", callback_data='admin_panel'))
    
    bot.edit_message_text(
        "💳 إدارة طرق الدفع:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data == 'clean_pending_recharges' and is_admin(call.from_user.id))
def clean_pending_recharges_handler(call):
    try:
        # استخدام قفل قاعدة البيانات لضمان الأمان
        with db_lock:
            cursor = conn.cursor()
            # استهداف الطلبات ذات الحالة 'pending' (بانتظار المستخدم) و 'pending_admin' (بانتظار الأدمن)
            cursor.execute("UPDATE recharge_requests SET status = 'failed' WHERE status = 'pending' OR status = 'pending_admin'")
            affected_rows = cursor.rowcount  # الحصول على عدد الطلبات التي تم تحديثها
            conn.commit()
            cursor.close()
        
        # إرسال رد للأدمن يفيد بنجاح العملية وعدد الطلبات التي تم تنظيفها
        bot.answer_callback_query(call.id, f"✅ تم تنظيف {affected_rows} طلب شحن معلق بنجاح.")
        
        # تحديث القائمة لإزالة أي إشعارات قديمة (اختياري)
        handle_manage_payment_methods(call)

    except Exception as e:
        print(f"Error cleaning pending recharges: {e}")
        bot.answer_callback_query(call.id, "❌ حدث خطأ أثناء عملية التنظيف.")

@bot.callback_query_handler(func=lambda call: call.data == 'add_payment_method' and is_admin(call.from_user.id))
def add_payment_method(call):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("محدود يومياً (سيريتل كاش)", callback_data='add_method_type_daily_limit_syp'))
    markup.add(types.InlineKeyboardButton("غير محدود (شام كاش, حوالات)", callback_data='add_method_type_unlimited_syp'))
    markup.add(types.InlineKeyboardButton("عملة أجنبية (USDT, etc.)", callback_data='add_method_type_foreign_currency'))
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data='manage_payment_methods'))
    bot.edit_message_text("اختر نوع طريقة الدفع الجديدة:", call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('add_method_type_') and is_admin(call.from_user.id))
def process_add_method_type(call):
    method_type = call.data.replace('add_method_type_', '')
    msg = bot.send_message(call.message.chat.id, "أرسل اسم طريقة الدفع (مثال: سيريتل كاش - خط أعمال):")
    bot.register_next_step_handler(msg, process_add_method_name, method_type)

def process_add_method_name(message, method_type):
    try:
        name = message.text.strip()
        instructions = "يرجى اتباع التعليمات لإتمام عملية الدفع." # يمكنك تغييرها لاحقاً
        safe_db_execute(
            "INSERT INTO payment_methods (name, type, instructions) VALUES (?, ?, ?)",
            (name, method_type, instructions)
        )
        bot.send_message(message.chat.id, f"✅ تم إضافة طريقة الدفع '{name}' بنجاح.")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ: {str(e)}")


@bot.callback_query_handler(func=lambda call: call.data.startswith('view_method_') and is_admin(call.from_user.id))
def view_specific_method(call):
    method_id = int(call.data.split('_')[2])
    method = safe_db_execute("SELECT name, is_active, min_amount FROM payment_methods WHERE id=?", (method_id,))[0]
    name, is_active, min_amount = method

    markup = types.InlineKeyboardMarkup(row_width=2)
    toggle_text = "❌ تعطيل" if is_active else "✅ تفعيل"
    
    markup.add(
        types.InlineKeyboardButton("➕ إضافة عنوان/رقم جديد", callback_data=f'add_address_{method_id}'),
        types.InlineKeyboardButton(f"{toggle_text} الطريقة", callback_data=f'toggle_method_{method_id}')
    )
    
    # ================== الزر الجديد ==================
    markup.add(types.InlineKeyboardButton(f"💰 تحديد الحد الأدنى ({min_amount or 0} ل.س)", callback_data=f'edit_min_amount_{method_id}'))
    # ===============================================

    addresses = safe_db_execute("SELECT id, address, is_active FROM payment_addresses WHERE method_id=?", (method_id,))
    if addresses:
        markup.add(types.InlineKeyboardButton("--- (العناوين المسجلة) ---", callback_data='no_action'))
        for addr_id, address, addr_is_active in addresses:
            addr_status_icon = "✅" if addr_is_active else "❌"
            markup.add(types.InlineKeyboardButton(
                f"{addr_status_icon} {address[:30]}...",
                callback_data=f'edit_address_{addr_id}'
            ))
    
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data='manage_payment_methods'))
    bot.edit_message_text(f"إدارة: {name}", call.message.chat.id, call.message.message_id, reply_markup=markup)
@bot.callback_query_handler(func=lambda call: call.data.startswith('edit_min_amount_') and is_admin(call.from_user.id))
def edit_method_min_amount(call):
    method_id = int(call.data.split('_')[3])
    msg = bot.send_message(
        call.message.chat.id,
        "أرسل الحد الأدنى للمبلغ بالليرة السورية (أرسل 0 لإلغائه):",
        reply_markup=types.ForceReply(selective=True)
    )
    bot.register_next_step_handler(msg, process_new_min_amount, method_id)

def process_new_min_amount(message, method_id):
    try:
        min_amount = int(message.text.strip())
        if min_amount < 0:
            bot.send_message(message.chat.id, "❌ لا يمكن أن يكون المبلغ سالبًا.")
            return
            
        safe_db_execute("UPDATE payment_methods SET min_amount=? WHERE id=?", (min_amount, method_id))
        bot.send_message(message.chat.id, f"✅ تم تحديث الحد الأدنى بنجاح إلى: {min_amount:,} ل.س")
        
        # للعودة، ننشئ call object مؤقت
        temp_call = types.CallbackQuery(id=0, from_user=message.from_user, data=f'view_method_{method_id}', chat_instance=0, json_string="")
        temp_call.message = message 
        view_specific_method(temp_call)

    except ValueError:
        bot.send_message(message.chat.id, "❌ يرجى إدخال رقم صحيح.")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ: {e}")
@bot.callback_query_handler(func=lambda call: call.data.startswith('toggle_method_') and is_admin(call.from_user.id))
def toggle_method_status(call):
    method_id = int(call.data.split('_')[2])
    safe_db_execute("UPDATE payment_methods SET is_active = NOT is_active WHERE id=?", (method_id,))
    bot.answer_callback_query(call.id, "✅ تم تغيير الحالة")
    view_specific_method(call) # تحديث العرض

@bot.callback_query_handler(func=lambda call: call.data.startswith('add_address_') and is_admin(call.from_user.id))
def add_address_to_method(call):
    method_id = int(call.data.split('_')[2])
    msg = bot.send_message(call.message.chat.id, "أرسل العنوان/الرقم الجديد:")
    bot.register_next_step_handler(msg, process_add_address_text, method_id)

def process_add_address_text(message, method_id):
    address = message.text.strip()
    method_type = safe_db_execute("SELECT type FROM payment_methods WHERE id=?", (method_id,))[0][0]
    
    # بناءً على نوع الطريقة، نطلب معلومات إضافية
    if method_type == 'daily_limit_syp':
        msg = bot.send_message(message.chat.id, "أرسل الحد اليومي للاستقبال بالليرة السورية (مثال: 540000):")
        bot.register_next_step_handler(msg, process_add_address_limit, method_id, address)
    elif method_type == 'foreign_currency':
        msg = bot.send_message(message.chat.id, "أرسل رمز العملة وسعر الصرف مقابل الليرة (مثال: USDT 15000):")
        bot.register_next_step_handler(msg, process_add_address_currency, method_id, address)
    else: # unlimited_syp
        safe_db_execute("INSERT INTO payment_addresses (method_id, address) VALUES (?, ?)", (method_id, address))
        bot.send_message(message.chat.id, "✅ تم إضافة العنوان بنجاح.")

def process_add_address_limit(message, method_id, address):
    try:
        limit = int(message.text.strip())
        safe_db_execute(
            "INSERT INTO payment_addresses (method_id, address, daily_limit) VALUES (?, ?, ?)",
            (method_id, address, limit)
        )
        bot.send_message(message.chat.id, "✅ تم إضافة العنوان مع الحد اليومي بنجاح.")
    except ValueError:
        bot.send_message(message.chat.id, "❌ رقم غير صالح. يرجى المحاولة مرة أخرى.")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ خطأ: {e}")

def process_add_address_currency(message, method_id, address):
    try:
        parts = message.text.split()
        currency = parts[0].upper()
        rate = float(parts[1])
        safe_db_execute(
            "INSERT INTO payment_addresses (method_id, address, currency, exchange_rate) VALUES (?, ?, ?, ?)",
            (method_id, address, currency, rate)
        )
        bot.send_message(message.chat.id, "✅ تم إضافة عنوان العملة الأجنبية بنجاح.")
    except (ValueError, IndexError):
        bot.send_message(message.chat.id, "❌ صيغة غير صالحة. الرجاء إرسال رمز العملة ثم السعر.")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ خطأ: {e}")


@bot.callback_query_handler(func=lambda call: call.data.startswith('edit_address_') and is_admin(call.from_user.id))
def edit_specific_address(call):
    address_id = int(call.data.split('_')[2])
    
    query = """
    SELECT 
        pa.address, pa.is_active, pa.currency, pa.exchange_rate, 
        pa.daily_limit, pa.daily_used, pm.type, pm.id
    FROM payment_addresses pa
    JOIN payment_methods pm ON pa.method_id = pm.id
    WHERE pa.id = ?
    """
    address_data = safe_db_execute(query, (address_id,))
    if not address_data:
        bot.answer_callback_query(call.id, "العنوان غير موجود!")
        return
    
    addr, is_active, currency, rate, limit, used, m_type, method_id = address_data[0]
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    toggle_text = "❌ تعطيل" if is_active else "✅ تفعيل"
    markup.add(types.InlineKeyboardButton(f"{toggle_text} العنوان", callback_data=f'toggle_addr_{address_id}'))

    if m_type == 'daily_limit_syp':
        markup.add(types.InlineKeyboardButton("🔧 تعديل الحد اليومي", callback_data=f'edit_limit_{address_id}'))
        # ================== الزر الجديد (يظهر هنا فقط) ==================
        markup.add(types.InlineKeyboardButton("🔄 إعادة تعيين يدوي", callback_data=f'reset_addr_limit_{address_id}'))
        # =============================================================
    elif m_type == 'foreign_currency':
        markup.add(types.InlineKeyboardButton("💱 تعديل سعر الصرف", callback_data=f'edit_rate_{address_id}'))
    
    markup.add(
        types.InlineKeyboardButton("✏️ تغيير العنوان", callback_data=f'change_addr_text_{address_id}'),
        types.InlineKeyboardButton("🗑️ حذف العنوان", callback_data=f'delete_addr_{address_id}')
    )
    
    markup.add(types.InlineKeyboardButton("🔙 رجوع للطريقة", callback_data=f'view_method_{method_id}'))
    
    status_text = f"إدارة العنوان:\n`{addr}`\n\n"
    if limit is not None:
        status_text += f"المستلم اليوم: **{used:,} / {limit:,} ل.س**\n"
    if currency != 'SYP':
        status_text += f"العملة: {currency}\nسعر الصرف: {rate}\n"
        
    bot.edit_message_text(status_text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith('toggle_addr_') and is_admin(call.from_user.id))
def toggle_address_status(call):
    address_id = int(call.data.split('_')[2])
    safe_db_execute("UPDATE payment_addresses SET is_active = NOT is_active WHERE id=?", (address_id,))
    bot.answer_callback_query(call.id, "✅ تم تغيير الحالة")
    edit_specific_address(call) # تحديث العرض
@bot.callback_query_handler(func=lambda call: call.data.startswith('edit_limit_') and is_admin(call.from_user.id))
def edit_address_limit(call):
    address_id = int(call.data.split('_')[2])
    msg = bot.send_message(
        call.message.chat.id,
        "أرسل الحد اليومي الجديد للرقم (مثال: 540000):",
        reply_markup=types.ForceReply(selective=True)
    )
    bot.register_next_step_handler(msg, process_new_limit, address_id)

def process_new_limit(message, address_id):
    try:
        new_limit = int(message.text.strip())
        if new_limit < 0:
            bot.send_message(message.chat.id, "❌ لا يمكن أن يكون الحد سالبًا.")
            return
            
        safe_db_execute("UPDATE payment_addresses SET daily_limit=? WHERE id=?", (new_limit, address_id))
        bot.send_message(message.chat.id, f"✅ تم تحديث الحد اليومي بنجاح إلى: {new_limit:,} ل.س")
    except ValueError:
        bot.send_message(message.chat.id, "❌ يرجى إدخال رقم صحيح.")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ: {e}")
@bot.callback_query_handler(func=lambda call: call.data.startswith('reset_addr_limit_') and is_admin(call.from_user.id))
def reset_address_limit_manually(call):
    try:
        # ================== تم تصحيح الخطأ هنا ==================
        # كان الفهرس خاطئًا ويشير إلى كلمة 'limit'
        # تم تصحيحه إلى 3 لجلب الـ ID بشكل صحيح
        address_id = int(call.data.split('_')[3])
        # ========================================================
        
        today = datetime.now().strftime("%Y-%m-%d")
        
        # إعادة تعيين العداد لهذا العنوان المحدد فقط
        safe_db_execute(
            "UPDATE payment_addresses SET daily_used=0, last_reset_date=? WHERE id=?",
            (today, address_id)
        )
        
        bot.answer_callback_query(call.id, "✅ تم إعادة تعيين عداد الاستخدام اليومي لهذا العنوان بنجاح.")
        
        # تحديث العرض لإظهار أن العداد أصبح صفراً
        edit_specific_address(call)
        
    except Exception as e:
        print(f"Error resetting address limit manually: {e}")
        bot.answer_callback_query(call.id, "❌ حدث خطأ أثناء إعادة التعيين.")

@bot.callback_query_handler(func=lambda call: call.data.startswith('edit_rate_') and is_admin(call.from_user.id))
def edit_address_rate(call):
    address_id = int(call.data.split('_')[2])
    msg = bot.send_message(
        call.message.chat.id,
        "أرسل سعر الصرف الجديد مقابل الليرة السورية (مثال: 15250.5):",
        reply_markup=types.ForceReply(selective=True)
    )
    bot.register_next_step_handler(msg, process_new_rate, address_id)

def process_new_rate(message, address_id):
    try:
        new_rate = float(message.text.strip())
        if new_rate <= 0:
            bot.send_message(message.chat.id, "❌ يجب أن يكون سعر الصرف أكبر من صفر.")
            return

        safe_db_execute("UPDATE payment_addresses SET exchange_rate=? WHERE id=?", (new_rate, address_id))
        bot.send_message(message.chat.id, f"✅ تم تحديث سعر الصرف بنجاح إلى: {new_rate}")
    except ValueError:
        bot.send_message(message.chat.id, "❌ يرجى إدخال رقم صحيح.")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('delete_addr_') and is_admin(call.from_user.id))
def confirm_delete_address(call):
    address_id = int(call.data.split('_')[2])
    address = safe_db_execute("SELECT address, method_id FROM payment_addresses WHERE id=?", (address_id,))
    if not address:
        bot.answer_callback_query(call.id, "⚠️ العنوان محذوف بالفعل.")
        return
    
    address_text, method_id = address[0]
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("✅ نعم، احذف", callback_data=f'exec_delete_addr_{address_id}'),
        types.InlineKeyboardButton("❌ إلغاء", callback_data=f'view_method_{method_id}')
    )
    bot.edit_message_text(
        f"⚠️ هل أنت متأكد من حذف العنوان التالي بشكل نهائي؟\n`{address_text}`",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode='Markdown'
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('exec_delete_addr_') and is_admin(call.from_user.id))
def execute_delete_address(call):
    address_id = int(call.data.split('_')[3])
    safe_db_execute("DELETE FROM payment_addresses WHERE id=?", (address_id,))
    bot.answer_callback_query(call.id, "✅ تم حذف العنوان بنجاح.")
    # نحتاج للرجوع إلى قائمة طرق الدفع الرئيسية
    # call.data هنا هو exec_delete_addr_{id}، سنقوم بتعديله للرجوع للقائمة الرئيسية
    call.data = 'manage_payment_methods'
    handle_manage_payment_methods(call)
# =================================================================
# |||            نهاية دوال إدارة طرق الدفع الجديدة              |||
# =================================================================
@bot.callback_query_handler(func=lambda call: call.data == 'admin_panel')
def handle_back_to_admin_panel(call):
    try:
        # نستدعي الدالة مع تفعيل خيار التعديل
        show_admin_panel(call.message, is_edit=True)
        bot.answer_callback_query(call.id) # نرسل إشعارًا صامتًا لتأكيد الضغط على الزر
    except Exception as e:
        print(f"Error returning to admin panel: {str(e)}")
        bot.answer_callback_query(call.id, "❌ فشل في العودة للوحة التحكم")

@bot.callback_query_handler(func=lambda call: call.data == 'toggle_recharge_service')
def toggle_recharge_feature(call):
    current = safe_db_execute("SELECT value FROM bot_settings WHERE key='recharge_disabled'")
    if not current:
        safe_db_execute("INSERT INTO bot_settings (key, value) VALUES ('recharge_disabled', '0')")
        current = [('0',)]

    new_value = '1' if current[0][0] == '0' else '0'
    safe_db_execute("UPDATE bot_settings SET value=? WHERE key='recharge_disabled'", (new_value,))

    # Ensure this line is correctly indented and executed before bot.answer_callback_query
    status = "⏸️ تم تعطيل خدمة إعادة الشحن" if new_value == '1' else "✅ تم تفعيل خدمة إعادة الشحن"
    
    bot.answer_callback_query(call.id, status)

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    user_id = call.from_user.id
    current_time = time.time()

    # ====== آلية التهدئة العامة لكل الـ callbacks ======
    if user_id in last_callback_time and (current_time - last_callback_time[user_id]) < CALLBACK_COOLDOWN:
        bot.answer_callback_query(call.id, "الرجاء الانتظار قليلاً قبل المحاولة مرة أخرى.")
        return
    last_callback_time[user_id] = current_time
    # ===============================================

    if is_bot_paused() and not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "البوت متوقف مؤقتاً.")
        return

    data = call.data
    message = call.message

    if data.startswith('category_'):
        category_id = data.split('_')[1]
        show_products(call.message, category_id)
    elif data.startswith('product_'):
        product_id = data.split('_')[1]
        show_product_details(call.message, product_id)
    elif data.startswith('buy_'):
        # ====== تعديل سلوك زر الشراء لـ G2BULK API ======
        product_id = data.split('_')[1]
        product = get_product_details(product_id) # جلب تفاصيل المنتج
        if not product:
            bot.answer_callback_query(call.id, "❌ المنتج غير متوفر!")
            return
        
        # تعديل الرسالة لإظهار التفاصيل وطلب الكمية
        updated_text = (
            f"🛒 المنتج: {product['title']}\n"
            f"💵 السعر: {product['unit_price_syp']:,} ل.س\n" # استخدام السعر المحول
            f"📦 المخزون: {product['stock']}\n\n"
        )
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=updated_text,
            reply_markup=None # إخفاء زر الشراء
        )
        # تسجيل next_step_handler بعد تعديل الرسالة
        msg = bot.send_message(call.message.chat.id, "⏳ الرجاء إدخال الكمية المطلوبة:") # إرسال رسالة منفصلة لطلب الكمية
        bot.register_next_step_handler(msg, process_purchase_quantity, product_id)
    # ===============================================

    elif data == 'edit_balance' and is_admin(user_id):
        msg = bot.send_message(call.message.chat.id, "أرسل آيدي المستخدم والمبلغ (مثال: 123456789 50000):")
        bot.register_next_step_handler(msg, process_balance_update)
    elif data == 'edit_exchange_rate' and is_admin(user_id):
        msg = bot.send_message(call.message.chat.id, "أرسل سعر الصرف الجديد:")
        bot.register_next_step_handler(msg, process_exchange_rate_update)
    elif data.startswith('topup_'):
        handle_topup_selection(call) # تم تعديلها سابقاً
    elif data == 'recharge_balance':
        handle_recharge_request(call.message)
    elif data == 'toggle_bot' and is_admin(user_id):
        toggle_bot_status(call.message)
    elif data == 'manage_categories' and is_admin(user_id):
        manage_categories(call.message)
    elif data.startswith('toggle_category_'):
        category_id = data.split('_')[2]
        toggle_category_status(call.message, category_id)
    elif data.startswith('edit_product_') and is_admin(user_id):
        product_id = data.split('_')[2]
        msg = bot.send_message(call.message.chat.id, "أرسل الاسم الجديد للمنتج:")
        bot.register_next_step_handler(msg, process_product_name_update, product_id)
    elif data == 'edit_products' and is_admin(user_id):
        manage_products(message)
        # bot.register_next_step_handler(msg, update_recharge_message) # هذا السطر يبدو في غير مكانه
    elif data == 'cancel_edit' and is_admin(user_id):
        bot.send_message(
            message.chat.id,
            "تم إلغاء التعديل",
            reply_markup=main_menu(user_id)
        )
    elif data == 'edit_recharge_code' and is_admin(user_id):
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
    # ===== إضافة معالجة لكولباكات الأكواد اليدوية Free Fire 2 و Manual Purchases =====
    elif data.startswith('ff2_offer_'):
        handle_freefire2_offer_selection(call) # تم تعديلها سابقاً
    elif data.startswith('ff_new_offer_'):
        handle_new_freefire_offer(call) # تم تعديلها سابقاً
    elif data.startswith('manual_prod_'):
        show_manual_product_details(call) # هذه الدالة تعرض تفاصيل المنتج Manual
    # ==============================================================================


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
                    f"{prod['title']} - {price_syp:,} ل.س", # تنسيق السعر
                    callback_data=f'product_{prod["id"]}'
                ))
        bot.send_message(message.chat.id, "المنتجات المتاحة حالياً :", reply_markup=markup)

def process_balance_deduction(message):
    try:
        parts = message.text.split()
        if len(parts) != 2:
            raise ValueError("صيغة غير صحيحة")
        user_id = int(parts[0])
        amount = int(parts[1])
        if amount <= 0:
            bot.send_message(message.chat.id, "❌ المبلغ يجب أن يكون أكبر من الصفر!")
            return
        current_balance = get_balance(user_id)
        if current_balance < amount:
            bot.send_message(message.chat.id, f"❌ رصيد المستخدم غير كافي! الرصيد الحالي: {current_balance} ل.س")
            return
        success = update_balance(user_id, -amount)
        if success:
            try:
                new_balance = get_balance(user_id)
                notify_msg = (
                    f"⚠️ تم خصم مبلغ من رصيدك\n\n"
                    f"💰 المبلغ المخصوم: {amount} ل.س\n"
                    f"💳 الرصيد الجديد: {new_balance} ل.س\n\n"
                    f"للاستفسار، يرجى التواصل مع الإدارة"
                )
                bot.send_message(user_id, notify_msg)
            except Exception as e:
                print(f"فشل في إرسال الإشعار للمستخدم: {str(e)}")
            bot.send_message(message.chat.id, f"✅ تم خصم {amount} ل.س من رصيد المستخدم {user_id} بنجاح")
        else:
            bot.send_message(message.chat.id, "❌ فشل في عملية الخصم")
    except ValueError:
        bot.send_message(message.chat.id, "❌ يرجى إدخال أرقام صحيحة بالصيغة الصحيحة!\nمثال: 123456789 50000")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ: {str(e)}")

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

def process_purchase_quantity(message, product_id):
    user_id = message.from_user.id
    if user_processing_lock.get(user_id, False):
        bot.send_message(message.chat.id, "لديك عملية قيد المعالجة بالفعل. الرجاء الانتظار.")
        return
    user_processing_lock[user_id] = True # قفل العملية

    try:
        quantity = int(message.text.strip())
        if quantity <= 0:
            bot.send_message(message.chat.id, "❌ الكمية يجب أن تكون أكبر من الصفر!")
            return
        product = get_product_details(product_id)
        if not product:
            bot.send_message(message.chat.id, "❌ المنتج غير متوفر!")
            return
        total_price = product['unit_price_syp'] * quantity # استخدام السعر المحول
        if get_balance(user_id) < total_price:
            bot.send_message(message.chat.id, "⚠️ رصيدك غير كافي!")
            return

        headers = {'X-API-Key': G2BULK_API_KEY}
        response = requests.post(
            f"{BASE_URL}products/{product_id}/purchase",
            json={"quantity": quantity},
            headers=headers
        )

        if response.status_code == 200:
            update_balance(user_id, -total_price)
            order_details = response.json()
            delivery_items = "\n".join([f"<code>{item}</code>" for item in order_details["delivery_items"]])
            
            # تسجيل الطلب
            log_user_order(
                user_id=user_id,
                order_type='cards',
                product_id=product_id,
                product_name=product['title'],
                price=total_price,
                api_response=order_details
            )

            bot.send_message(
                message.chat.id,
                f"✅ تمت العملية بنجاح!\nرقم الطلب: {order_details['order_id']}\n"
                f"الأكواد:\n"
                f"<code>{delivery_items}</code>",
                parse_mode='HTML',
                reply_markup=main_menu(message.from_user.id)
            )
        else:
            error_msg = response.json().get('message', 'فشلت عملية الشراء')
            bot.send_message(message.chat.id, f"❌ {error_msg}")

    except ValueError:
        bot.send_message(message.chat.id, "❌ يرجى إدخال رقم صحيح!")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ غير متوقع: {str(e)}")
    finally:
        user_processing_lock[user_id] = False # تحرير القفل

def show_product_details(message, product_id):
    product = get_product_details(product_id)
    if product:
        text = f"""
        🛒 المنتج: {product['title']}
        💵 السعر: {product['unit_price_syp']:,} ل.س
        📦 المخزون: {product['stock']}
        """
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("شراء 🛒", callback_data=f"buy_{product['id']}"))
        bot.send_message(message.chat.id, text, reply_markup=markup)

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
        result = safe_db_execute("SELECT value FROM bot_settings WHERE key='recharge_code'")
        current_code = result[0][0] if result else "غير محدد"
        bot.send_message(
            message.chat.id,
            f"✅ تم تحديث كود الشحن بنجاح!\n\n"
            f"كود الشحن الحالي:\n"
            f"<code>{current_code}</code>",
            parse_mode='HTML'
        )
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ حدث خطأ: {str(e)}")



# ============= وظائف الإدارة =============
def process_balance_update(message):
    try:
        parts = message.text.split()
        if len(parts) != 2:
            raise ValueError("صيغة غير صحيحة")
        user_id = int(parts[0])
        amount = int(parts[1])
        success = update_balance(user_id, amount)
        if success:
            new_balance = get_balance(user_id)
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
def process_topup_purchase(message, offer):
    user_id = message.from_user.id
    try:
        player_id = message.text.strip()
        if not (player_id.isdigit() and 8 <= len(player_id) <= 12):
            raise ValueError("رقم اللاعب غير صالح! يجب أن يحتوي على 8 إلى 12 رقمًا فقط")
        price_syp = convert_to_syp(offer['unit_price'])
        if get_balance(user_id) < price_syp:
            raise ValueError(f"رصيدك غير كافي. السعر: {price_syp:,} ل.س")
        markup = types.InlineKeyboardMarkup()
        markup.row(
            types.InlineKeyboardButton("✅ تأكيد الشراء", callback_data=f'confirm_topup_{offer["id"]}_{player_id}_{price_syp}'),
            types.InlineKeyboardButton("❌ إلغاء", callback_data=f'cancel_topup_{offer["id"]}')
        )
        confirmation_msg = (
            f"🛒 تأكيد عملية الشراء:\n\n"
            f"📌 العرض: {offer['title']}\n"
            f"💰 السعر: {price_syp:,} ل.س\n"
            f"👤 آيدي اللاعب: {player_id}\n\n"
            f"هل أنت متأكد من المعلومات أعلاه؟"
        )
        bot.send_message(
            message.chat.id,
            confirmation_msg,
            reply_markup=markup
        )
    except ValueError as e:
        bot.send_message(message.chat.id, f"❌ {str(e)}")
    except Exception as e:
        print(f"Error in purchase process: {str(e)}")
        bot.send_message(message.chat.id, "❌ حدث خطأ غير متوقع في المعالجة!")
    finally:
        user_processing_lock[user_id] = False # تحرير القفل

def handle_purchase(message, product_id, quantity): # هذه الدالة لم تعد تستخدم بشكل مباشر بعد التعديلات
    user_id = message.from_user.id
    product = get_product_details(product_id)
    total_price = product['unit_price_syp'] * quantity # استخدام السعر المحول

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
            delivery_items = "\n".join([f"<code>{item}</code>" for item in order_details["delivery_items"]])
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

def show_admin_panel(message, is_edit=False):
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton('تعديل سعر الصرف', callback_data='edit_exchange_rate'),
        types.InlineKeyboardButton("إدارة الأزرار الرئيسية", callback_data='manage_buttons') # تم تعديل الاسم للتمييز
    )
    # زر جديد لإدارة الأزرار الفرعية
    markup.row(
        types.InlineKeyboardButton("إدارة أزرار الخدمات", callback_data='manage_sub_buttons')
    )
    markup.row(
        types.InlineKeyboardButton('إدارة المستخدمين', callback_data='user_management'),
        types.InlineKeyboardButton('إدارة المشرفين', callback_data='manage_admins')
    )
    markup.row(
        types.InlineKeyboardButton('إدارة القناة', callback_data='manage_channel'),
        types.InlineKeyboardButton('إدارة الفئات', callback_data='manage_categories')
    )
    markup.row(
        types.InlineKeyboardButton('إدارة العمليات اليدوية', callback_data='manage_manual'),
        types.InlineKeyboardButton('إدارة طرق الدفع 💳', callback_data='manage_payment_methods')
    )
    markup.row(
        types.InlineKeyboardButton('📦 نسخ احتياطي', callback_data='backup_db'),
        types.InlineKeyboardButton('🔄 استعادة', callback_data='restore_db')
    )
    markup.row(
        types.InlineKeyboardButton('إيقاف/تشغيل البوت', callback_data='toggle_bot')
    )

    text_content = "⚙️ لوحة التحكم الإدارية:"
    if is_edit:
        # إذا طُلب التعديل، نستخدم edit_message_text
        try:
            bot.edit_message_text(
                text_content,
                message.chat.id,
                message.message_id,
                reply_markup=markup
            )
        except Exception as e:
            print(f"Failed to edit message for admin panel, sending new one: {e}")
            # في حال فشل التعديل (مثلاً الرسالة قديمة)، نرسل رسالة جديدة كخيار احتياطي
            bot.send_message(message.chat.id, text_content, reply_markup=markup)
    else:
        # السلوك الافتراضي: إرسال رسالة جديدة
        bot.send_message(message.chat.id, text_content, reply_markup=markup)

# ============= تشغيل البوت =============
if __name__ == '__main__':
    print("Bot is running...")
    bot.infinity_polling()
