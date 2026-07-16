#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ربات محافظ گروه تلگرام - نسخه پیشرفته
قابلیت‌ها: ضد اسپم، فیلتر کلمات رکیک، حالت شب، کپچا، سیستم اخطار، مدیریت ادمین‌ها
"""

import os
import re
import random
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple, List, Dict, Set
from dataclasses import dataclass, field
from collections import defaultdict

# کتابخانه‌های مورد نیاز
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, 
    ChatPermissions, ChatMember
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ChatMemberHandler, ContextTypes, filters
)
from telegram.constants import ParseMode
from dotenv import load_dotenv
import aiosqlite
import pytz

# ====================== تنظیمات اولیه ======================
load_dotenv()

# تنظیمات لاگینگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ====================== کانفیگ ======================
class Config:
    """تنظیمات ربات از متغیرهای محیطی"""
    
    BOT_TOKEN = os.getenv('BOT_TOKEN', '')
    OWNER_ID = int(os.getenv('OWNER_ID', '0'))
    LOG_CHANNEL_ID = int(os.getenv('LOG_CHANNEL_ID', '0'))
    
    # حالت شب
    NIGHT_MODE_START = int(os.getenv('NIGHT_MODE_START', '23'))
    NIGHT_MODE_END = int(os.getenv('NIGHT_MODE_END', '6'))
    
    # ادمین‌ها و مدیران
    ADMIN_IDS = [int(id.strip()) for id in os.getenv('ADMIN_IDS', '').split(',') if id.strip()]
    MODERATOR_IDS = [int(id.strip()) for id in os.getenv('MODERATOR_IDS', '').split(',') if id.strip()]
    
    # تنظیمات اسپم
    SPAM_SIMILAR_COUNT = 5
    SPAM_SIMILAR_TIME = 10
    SPAM_LINKS_COUNT = 3
    SPAM_LINKS_TIME = 5
    SPAM_FORWARDS_COUNT = 3
    SPAM_FORWARDS_TIME = 3
    SPAM_MEDIA_COUNT = 4
    SPAM_MEDIA_TIME = 6
    
    # کپچا
    CAPTCHA_TIMEOUT = 120
    
    # اخطارها
    WARN_RESET_HOURS = 24
    MUTE_DURATION_MINUTES = 30
    
    # محدودیت‌ها
    MAX_MESSAGE_LENGTH = 500
    COMMAND_FLOOD_TIME = 3
    
    # دیتابیس
    DB_PATH = 'database/bot.db'
    
    @classmethod
    def validate(cls):
        if not cls.BOT_TOKEN:
            raise ValueError("BOT_TOKEN تنظیم نشده است!")
        if not cls.OWNER_ID:
            raise ValueError("OWNER_ID تنظیم نشده است!")
        return True

# ====================== دیتابیس ======================
class Database:
    """مدیریت دیتابیس SQLite با aiosqlite"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    async def init(self):
        """ایجاد جداول"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript('''
                CREATE TABLE IF NOT EXISTS warnings (
                    user_id INTEGER,
                    chat_id INTEGER,
                    warn_count INTEGER DEFAULT 0,
                    last_warn_date TEXT,
                    PRIMARY KEY (user_id, chat_id)
                );
                
                CREATE TABLE IF NOT EXISTS muted_users (
                    user_id INTEGER,
                    chat_id INTEGER,
                    mute_until TEXT,
                    PRIMARY KEY (user_id, chat_id)
                );
                
                CREATE TABLE IF NOT EXISTS banned_users (
                    user_id INTEGER,
                    chat_id INTEGER,
                    banned_by INTEGER,
                    ban_date TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, chat_id)
                );
                
                CREATE TABLE IF NOT EXISTS group_locks (
                    chat_id INTEGER PRIMARY KEY,
                    lock_chat INTEGER DEFAULT 0,
                    lock_links INTEGER DEFAULT 0,
                    lock_forwards INTEGER DEFAULT 0,
                    lock_media INTEGER DEFAULT 0,
                    lock_all INTEGER DEFAULT 0
                );
                
                CREATE TABLE IF NOT EXISTS captcha_verifications (
                    user_id INTEGER,
                    chat_id INTEGER,
                    answer INTEGER,
                    message_id INTEGER,
                    created_at TEXT DEFAULT (datetime('now')),
                    PRIMARY KEY (user_id, chat_id)
                );
                
                CREATE TABLE IF NOT EXISTS message_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    chat_id INTEGER,
                    message_text TEXT,
                    message_type TEXT,
                    timestamp TEXT DEFAULT (datetime('now'))
                );
                
                CREATE TABLE IF NOT EXISTS staff (
                    user_id INTEGER,
                    chat_id INTEGER,
                    role TEXT,
                    added_by INTEGER,
                    added_date TEXT DEFAULT (datetime('now')),
                    PRIMARY KEY (user_id, chat_id)
                );
                
                CREATE INDEX IF NOT EXISTS idx_msg_history 
                ON message_history(user_id, chat_id, timestamp);
                
                CREATE INDEX IF NOT EXISTS idx_warnings 
                ON warnings(user_id, chat_id);
            ''')
            await db.commit()
    
    async def add_warning(self, user_id: int, chat_id: int) -> int:
        """افزودن اخطار و برگرداندن تعداد کل"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                'SELECT warn_count, last_warn_date FROM warnings WHERE user_id=? AND chat_id=?',
                (user_id, chat_id)
            )
            row = await cursor.fetchone()
            now = datetime.now()
            
            if row:
                count, last_date = row
                if last_date:
                    last = datetime.fromisoformat(last_date)
                    if (now - last) > timedelta(hours=Config.WARN_RESET_HOURS):
                        count = 0
                count += 1
                await db.execute(
                    'UPDATE warnings SET warn_count=?, last_warn_date=? WHERE user_id=? AND chat_id=?',
                    (count, now.isoformat(), user_id, chat_id)
                )
            else:
                count = 1
                await db.execute(
                    'INSERT INTO warnings VALUES (?,?,?,?)',
                    (user_id, chat_id, count, now.isoformat())
                )
            
            await db.commit()
            return count
    
    async def get_warnings(self, user_id: int, chat_id: int) -> int:
        """دریافت تعداد اخطارها"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                'SELECT warn_count, last_warn_date FROM warnings WHERE user_id=? AND chat_id=?',
                (user_id, chat_id)
            )
            row = await cursor.fetchone()
            if row:
                count, last_date = row
                if last_date:
                    last = datetime.fromisoformat(last_date)
                    if (datetime.now() - last) > timedelta(hours=Config.WARN_RESET_HOURS):
                        return 0
                return count
            return 0
    
    async def mute_user(self, user_id: int, chat_id: int, minutes: int = 30):
        """میوت کاربر"""
        until = datetime.now() + timedelta(minutes=minutes)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                'INSERT OR REPLACE INTO muted_users VALUES (?,?,?)',
                (user_id, chat_id, until.isoformat())
            )
            await db.commit()
    
    async def unmute_user(self, user_id: int, chat_id: int):
        """خروج از میوت"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                'DELETE FROM muted_users WHERE user_id=? AND chat_id=?',
                (user_id, chat_id)
            )
            await db.commit()
    
    async def is_muted(self, user_id: int, chat_id: int) -> bool:
        """بررسی وضعیت میوت"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                'SELECT mute_until FROM muted_users WHERE user_id=? AND chat_id=?',
                (user_id, chat_id)
            )
            row = await cursor.fetchone()
            if row:
                until = datetime.fromisoformat(row[0])
                if datetime.now() < until:
                    return True
                await self.unmute_user(user_id, chat_id)
            return False
    
    async def ban_user(self, user_id: int, chat_id: int, by: int):
        """بن کاربر"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                'INSERT OR REPLACE INTO banned_users (user_id, chat_id, banned_by) VALUES (?,?,?)',
                (user_id, chat_id, by)
            )
            await db.commit()
    
    async def unban_user(self, user_id: int, chat_id: int):
        """آنبن"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                'DELETE FROM banned_users WHERE user_id=? AND chat_id=?',
                (user_id, chat_id)
            )
            await db.commit()
    
    async def is_banned(self, user_id: int, chat_id: int) -> bool:
        """بررسی بن"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                'SELECT 1 FROM banned_users WHERE user_id=? AND chat_id=?',
                (user_id, chat_id)
            )
            return await cursor.fetchone() is not None
    
    async def log_message(self, user_id: int, chat_id: int, text: str, msg_type: str):
        """ثبت پیام برای ضد اسپم"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                'INSERT INTO message_history (user_id, chat_id, message_text, message_type) VALUES (?,?,?,?)',
                (user_id, chat_id, text, msg_type)
            )
            await db.execute(
                "DELETE FROM message_history WHERE timestamp < datetime('now', '-2 minutes')"
            )
            await db.commit()
    
    async def get_recent_messages(self, user_id: int, chat_id: int, seconds: int) -> List:
        """پیام‌های اخیر کاربر"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                '''SELECT message_text, message_type FROM message_history 
                   WHERE user_id=? AND chat_id=? 
                   AND timestamp > datetime('now', ?) 
                   ORDER BY timestamp DESC''',
                (user_id, chat_id, f'-{seconds} seconds')
            )
            return await cursor.fetchall()
    
    async def set_lock(self, chat_id: int, lock_type: str, status: bool):
        """تنظیم قفل"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                'INSERT OR IGNORE INTO group_locks (chat_id) VALUES (?)',
                (chat_id,)
            )
            col = f'lock_{lock_type}'
            await db.execute(
                f'UPDATE group_locks SET {col}=? WHERE chat_id=?',
                (1 if status else 0, chat_id)
            )
            await db.commit()
    
    async def get_locks(self, chat_id: int) -> Dict:
        """دریافت قفل‌ها"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                'SELECT * FROM group_locks WHERE chat_id=?',
                (chat_id,)
            )
            row = await cursor.fetchone()
            if row:
                return {
                    'lock_chat': bool(row[1]),
                    'lock_links': bool(row[2]),
                    'lock_forwards': bool(row[3]),
                    'lock_media': bool(row[4]),
                    'lock_all': bool(row[5])
                }
            return {
                'lock_chat': False,
                'lock_links': False,
                'lock_forwards': False,
                'lock_media': False,
                'lock_all': False
            }
    
    async def save_captcha(self, user_id: int, chat_id: int, answer: int, msg_id: int):
        """ذخیره کپچا"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                'INSERT OR REPLACE INTO captcha_verifications VALUES (?,?,?,?,datetime("now"))',
                (user_id, chat_id, answer, msg_id)
            )
            await db.commit()
    
    async def get_captcha(self, user_id: int, chat_id: int) -> Optional[int]:
        """دریافت جواب کپچا"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                '''SELECT answer FROM captcha_verifications 
                   WHERE user_id=? AND chat_id=? 
                   AND created_at > datetime('now', '-2 minutes')''',
                (user_id, chat_id)
            )
            row = await cursor.fetchone()
            return row[0] if row else None
    
    async def remove_captcha(self, user_id: int, chat_id: int):
        """حذف کپچا"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                'DELETE FROM captcha_verifications WHERE user_id=? AND chat_id=?',
                (user_id, chat_id)
            )
            await db.commit()
    
    async def add_staff(self, user_id: int, chat_id: int, role: str, by: int):
        """افزودن به کادر مدیریت"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                'INSERT OR REPLACE INTO staff VALUES (?,?,?,?,datetime("now"))',
                (user_id, chat_id, role, by)
            )
            await db.commit()
    
    async def remove_staff(self, user_id: int, chat_id: int):
        """حذف از کادر"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                'DELETE FROM staff WHERE user_id=? AND chat_id=?',
                (user_id, chat_id)
            )
            await db.commit()
    
    async def get_staff_role(self, user_id: int, chat_id: int) -> Optional[str]:
        """دریافت نقش"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                'SELECT role FROM staff WHERE user_id=? AND chat_id=?',
                (user_id, chat_id)
            )
            row = await cursor.fetchone()
            return row[0] if row else None

# ====================== کلمات رکیک ======================
BADWORDS_PERSIAN = [
    "کیر", "کوس", "کص", "جنده", "حرومزاده", "مادرجنده", "بیناموس",
    "گاییدم", "گایید", "خارکصه", "خارکسه", "کسکش", "کصکش",
    "دیوث", "لاشی", "آشغال", "گوه", "گه", "عن", "کس",
    "اسکل", "خرفت", "بیشعور", "نفهم", "بی‌عقل", "توله‌سگ",
    "کونی", "ممه", "پستان", "سگ‌پدر", "سگ‌مادر", "ناموس",
]

BADWORDS_ENGLISH = [
    "fuck", "shit", "bitch", "asshole", "bastard", "damn",
    "dick", "pussy", "cock", "whore", "slut", "motherfucker",
    "idiot", "stupid", "moron", "retard", "dumbass",
    "faggot", "nigger", "cunt", "douche", "twat",
]

def create_badword_pattern(word: str):
    """ساخت پترن برای تشخیص کلمه با کاراکترهای مخفی"""
    chars = list(word)
    pattern = r'[\s.\-_\*\'\"]*'.join(re.escape(c) for c in chars)
    return re.compile(pattern, re.IGNORECASE)

BADWORD_PATTERNS = [create_badword_pattern(w) for w in BADWORDS_PERSIAN + BADWORDS_ENGLISH]

# ====================== کلاس اصلی ربات ======================
class GuardianBot:
    """ربات محافظ گروه"""
    
    def __init__(self):
        self.db = Database(Config.DB_PATH)
        # کش موقت برای ضد فلاد
        self.last_command_time: Dict[str, float] = {}
        # کاربران در انتظار کپچا
        self.pending_captcha: Set[Tuple[int, int]] = set()
    
    # ====================== توابع کمکی ======================
    
    def get_user_level(self, user_id: int, chat_id: int) -> int:
        """
        دریافت سطح دسترسی کاربر
        3: Founder
        2: Admin
        1: Moderator
        0: User
        """
        if user_id == Config.OWNER_ID:
            return 3
        if user_id in Config.ADMIN_IDS:
            return 2
        if user_id in Config.MODERATOR_IDS:
            return 1
        return 0
    
    def is_night_time(self) -> bool:
        """بررسی حالت شب"""
        now = datetime.now()
        hour = now.hour
        start, end = Config.NIGHT_MODE_START, Config.NIGHT_MODE_END
        
        if start > end:
            return hour >= start or hour < end
        return start <= hour < end
    
    async def check_flood(self, user_id: int) -> bool:
        """بررسی ضد فلاد (True یعنی محدود شده)"""
        now = datetime.now().timestamp()
        key = f"cmd_{user_id}"
        
        if key in self.last_command_time:
            if now - self.last_command_time[key] < Config.COMMAND_FLOOD_TIME:
                return True
        
        self.last_command_time[key] = now
        return False
    
    async def delete_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """حذف پیام با خطایابی"""
        try:
            await update.message.delete()
        except Exception as e:
            logger.warning(f"خطا در حذف پیام: {e}")
    
    async def send_log(self, context: ContextTypes.DEFAULT_TYPE, text: str):
        """ارسال لاگ به کانال"""
        if Config.LOG_CHANNEL_ID:
            try:
                await context.bot.send_message(
                    chat_id=Config.LOG_CHANNEL_ID,
                    text=f"📋 {text}",
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.error(f"خطا در ارسال لاگ: {e}")
    
    async def restrict_user(self, chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE):
        """محدود کردن کامل کاربر (میوت)"""
        try:
            permissions = ChatPermissions(
                can_send_messages=False,
                can_send_media_messages=False,
                can_send_polls=False,
                can_send_other_messages=False,
                can_add_web_page_previews=False,
                can_change_info=False,
                can_invite_users=False,
                can_pin_messages=False
            )
            await context.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=permissions
            )
        except Exception as e:
            logger.error(f"خطا در محدود کردن کاربر: {e}")
    
    async def unrestrict_user(self, chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE):
        """رفع محدودیت کاربر"""
        try:
            permissions = ChatPermissions.all_permissions()
            await context.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=permissions
            )
        except Exception as e:
            logger.error(f"خطا در رفع محدودیت: {e}")
    
    # ====================== سیستم‌های فیلترینگ ======================
    
    async def check_spam(self, user_id: int, chat_id: int, text: str, msg_type: str) -> Tuple[bool, str]:
        """بررسی اسپم"""
        recent = await self.db.get_recent_messages(user_id, chat_id, 10)
        
        # پیام تکراری
        if msg_type == 'text' and text:
            same = sum(1 for m in recent if m[0] == text)
            if same >= Config.SPAM_SIMILAR_COUNT - 1:
                return True, "ارسال پیام‌های تکراری"
        
        # لینک زیاد
        if text and re.findall(r'https?://\S+', text):
            links = sum(1 for m in recent if m[1] == 'text' and m[0] and re.findall(r'https?://\S+', m[0]))
            if links >= Config.SPAM_LINKS_COUNT - 1:
                return True, "ارسال لینک‌های متعدد"
        
        # فوروارد پشت سر هم
        if msg_type == 'forward':
            fwds = sum(1 for m in recent if m[1] == 'forward')
            if fwds >= Config.SPAM_FORWARDS_COUNT - 1:
                return True, "فورواردهای متعدد"
        
        # بمباران رسانه
        if msg_type in ('photo', 'video', 'animation', 'sticker'):
            media = sum(1 for m in recent if m[1] in ('photo', 'video', 'animation', 'sticker'))
            if media >= Config.SPAM_MEDIA_COUNT - 1:
                return True, "ارسال رسانه‌های متعدد"
        
        return False, ""
    
    def check_badwords(self, text: str) -> Tuple[bool, str]:
        """بررسی کلمات رکیک"""
        if not text:
            return False, ""
        for pattern in BADWORD_PATTERNS:
            match = pattern.search(text)
            if match:
                return True, match.group()
        return False, ""
    
    def is_useless_message(self, text: str) -> bool:
        """تشخیص پیام بی‌محتوا"""
        if not text:
            return False
        t = text.strip().lower()
        patterns = [
            r'^سلام\.?$', r'^کسی هست[\?؟]?$', r'^هلو$',
            r'^hello$', r'^hi\.?$', r'^anyone here[\?]?$',
            r'^[.؟?!！]+$', r'^[\U0001F600-\U0001F64F]+$'
        ]
        for p in patterns:
            if re.match(p, t):
                return True
        return len(t) < 2
    
    async def check_night_violation(self, update: Update) -> Tuple[bool, str]:
        """بررسی تخلفات حالت شب"""
        if not self.is_night_time():
            return False, ""
        
        msg = update.message
        
        # لینک ممنوع
        if msg.text and re.search(r'https?://\S+', msg.text):
            return True, "🔒 شب‌ها ارسال لینک ممنوع است"
        
        # رسانه ممنوع
        if msg.photo or msg.video or msg.animation or msg.sticker:
            return True, "🔒 شب‌ها ارسال رسانه ممنوع است"
        
        return False, ""
    
    # ====================== سیستم اخطار ======================
    
    async def warn_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE, 
                       user_id: int, chat_id: int, reason: str = ""):
        """اخطار به کاربر با سیستم سه مرحله‌ای"""
        warnings = await self.db.add_warning(user_id, chat_id)
        user = await context.bot.get_chat(user_id)
        name = user.full_name
        
        if warnings == 1:
            # اخطار اول: هشدار
            text = f"⚠️ اخطار 1/3 برای {name}\n{reason}\n📌 پیام شما حذف شد."
            await update.message.reply_text(text)
            await self.delete_message(update, context)
        
        elif warnings == 2:
            # اخطار دوم: میوت 30 دقیقه
            await self.db.mute_user(user_id, chat_id, Config.MUTE_DURATION_MINUTES)
            await self.restrict_user(chat_id, user_id, context)
            text = f"🔇 اخطار 2/3 برای {name}\nسکوت اجباری: 30 دقیقه\n{reason}"
            await update.message.reply_text(text)
            await self.delete_message(update, context)
            await self.send_log(context, f"🔇 {name} میوت 30 دقیقه | دلیل: {reason}")
        
        elif warnings >= 3:
            # اخطار سوم: بن دائم
            await self.db.ban_user(user_id, chat_id, update.effective_user.id)
            try:
                await context.bot.ban_chat_member(chat_id, user_id)
            except:
                pass
            text = f"🚫 اخطار 3/3 برای {name}\n⛔️ بن دائم از گروه\n{reason}"
            await update.message.reply_text(text)
            await self.delete_message(update, context)
            await self.send_log(context, f"🚫 {name} بن دائم | دلیل: {reason}")
        
        return warnings
    
    # ====================== کپچا ======================
    
    def generate_captcha(self) -> Tuple[str, int]:
        """تولید کپچای ریاضی"""
        a, b = random.randint(1, 50), random.randint(1, 50)
        if random.choice([True, False]):
            return f"{a} + {b} = ?", a + b
        else:
            if a < b:
                a, b = b, a
            return f"{a} - {b} = ?", a - b
    
    async def send_captcha(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                          user_id: int, chat_id: int):
        """ارسال کپچا برای کاربر جدید"""
        question, answer = self.generate_captcha()
        
        # ساخت کیبورد با جواب‌های تصادفی
        options = {answer}
        while len(options) < 4:
            options.add(random.randint(max(1, answer-20), answer+20))
        options = list(options)
        random.shuffle(options)
        
        keyboard = []
        for i in range(0, 4, 2):
            row = [InlineKeyboardButton(str(options[i]), callback_data=f"captcha_{options[i]}")]
            if i+1 < 4:
                row.append(InlineKeyboardButton(str(options[i+1]), callback_data=f"captcha_{options[i+1]}"))
            keyboard.append(row)
        
        msg = await update.message.reply_text(
            f"👋 کاربر جدید!\n\n"
            f"🤖 لطفاً ثابت کنید ربات نیستید:\n\n"
            f"<b>{question}</b>\n\n"
            f"⏰ زمان: 2 دقیقه",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
        
        await self.db.save_captcha(user_id, chat_id, answer, msg.message_id)
        self.pending_captcha.add((user_id, chat_id))
        
        # تایمر 2 دقیقه
        asyncio.create_task(self._captcha_timeout(context, user_id, chat_id, msg.message_id))
    
    async def _captcha_timeout(self, context: ContextTypes.DEFAULT_TYPE,
                              user_id: int, chat_id: int, msg_id: int):
        """تایمر کپچا"""
        await asyncio.sleep(Config.CAPTCHA_TIMEOUT)
        
        if (user_id, chat_id) in self.pending_captcha:
            self.pending_captcha.discard((user_id, chat_id))
            await self.db.remove_captcha(user_id, chat_id)
            
            try:
                await context.bot.ban_chat_member(chat_id, user_id)
                await context.bot.unban_chat_member(chat_id, user_id)
                await context.bot.delete_message(chat_id, msg_id)
                await self.send_log(context, f"👢 کاربر {user_id} به دلیل عدم حل کپچا کیک شد")
            except Exception as e:
                logger.error(f"خطا در کیک کاربر: {e}")
    
    # ====================== هندلرهای پیام ======================
    
    async def handle_new_member(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """مدیریت کاربر جدید"""
        for member in update.message.new_chat_members:
            if member.is_bot:
                return
            
            user_id = member.id
            chat_id = update.effective_chat.id
            
            # حذف پیام join
            await self.delete_message(update, context)
            
            # ارسال کپچا
            await self.send_captcha(update, context, user_id, chat_id)
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """پردازش پیام‌ها"""
        if not update.message or not update.effective_user:
            return
        
        msg = update.message
        user = update.effective_user
        chat_id = update.effective_chat.id
        user_id = user.id
        
        # نادیده گرفتن پیام‌های کانال
        if user.is_bot:
            return
        
        # بررسی کپچا
        if (user_id, chat_id) in self.pending_captcha:
            await self.delete_message(update, context)
            return
        
        # بررسی بن
        if await self.db.is_banned(user_id, chat_id):
            await self.delete_message(update, context)
            return
        
        # بررسی میوت
        if await self.db.is_muted(user_id, chat_id):
            await self.delete_message(update, context)
            return
        
        level = self.get_user_level(user_id, chat_id)
        
        # ادمین‌ها و founder معاف از فیلترها
        if level >= 2:
            await self.db.log_message(user_id, chat_id, msg.text or "", self._get_msg_type(msg))
            return
        
        # بررسی قفل‌ها
        locks = await self.db.get_locks(chat_id)
        
        if locks['lock_all'] or locks['lock_chat']:
            if level < 1:
                await self.delete_message(update, context)
                return
        
        if locks['lock_all'] or locks['lock_links']:
            if msg.text and re.search(r'https?://\S+', msg.text):
                await self.delete_message(update, context)
                return
        
        if locks['lock_all'] or locks['lock_forwards']:
            if msg.forward_from or msg.forward_from_chat:
                await self.delete_message(update, context)
                return
        
        if locks['lock_all'] or locks['lock_media']:
            if msg.photo or msg.video or msg.animation or msg.sticker:
                await self.delete_message(update, context)
                return
        
        # ثبت پیام
        msg_type = self._get_msg_type(msg)
        await self.db.log_message(user_id, chat_id, msg.text or "", msg_type)
        
        # فیلتر حالت شب
        night_violation, night_reason = await self.check_night_violation(update)
        if night_violation:
            await self.warn_user(update, context, user_id, chat_id, night_reason)
            return
        
        # فیلتر اسپم
        is_spam, spam_reason = await self.check_spam(user_id, chat_id, msg.text or "", msg_type)
        if is_spam:
            if self.is_night_time():
                # حالت شب سخت‌گیرانه‌تر
                await self.warn_user(update, context, user_id, chat_id, f"🚨 اسپم شبانه: {spam_reason}")
            else:
                await self.warn_user(update, context, user_id, chat_id, spam_reason)
            return
        
        # فیلتر کلمات رکیک
        if msg.text:
            has_bad, bad_word = self.check_badwords(msg.text)
            if has_bad:
                await self.warn_user(update, context, user_id, chat_id, f"کلمه نامناسب: {bad_word}")
                return
        
        # پیام بی‌محتوا
        if msg.text and self.is_useless_message(msg.text):
            await self.delete_message(update, context)
            return
        
        # محدودیت طول پیام
        if msg.text and len(msg.text) > Config.MAX_MESSAGE_LENGTH:
            await self.delete_message(update, context)
            await update.message.reply_text(
                f"⚠️ حداکثر طول پیام {Config.MAX_MESSAGE_LENGTH} کاراکتر است."
            )
            return
    
    def _get_msg_type(self, msg) -> str:
        """تشخیص نوع پیام"""
        if msg.text:
            return 'text'
        if msg.photo:
            return 'photo'
        if msg.video:
            return 'video'
        if msg.animation:
            return 'animation'
        if msg.sticker:
            return 'sticker'
        if msg.forward_from or msg.forward_from_chat:
            return 'forward'
        if msg.document:
            return 'document'
        return 'other'
    
    # ====================== دستورات ادمین ======================
    
    async def cmd_warn(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """دستور اخطار /warn"""
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        level = self.get_user_level(user_id, chat_id)
        
        if level < 1:
            return
        
        if await self.check_flood(user_id):
            return
        
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ روی پیام کاربر ریپلای کنید.")
            return
        
        target = update.message.reply_to_message.from_user
        target_id = target.id
        
        if self.get_user_level(target_id, chat_id) >= level:
            await update.message.reply_text("❌ نمی‌توانید به ادمین‌های بالاتر اخطار دهید.")
            return
        
        reason = ' '.join(context.args) if context.args else "تخلف"
        warnings = await self.db.add_warning(target_id, chat_id)
        
        await update.message.reply_text(
            f"⚠️ اخطار {warnings}/3 برای {target.full_name}\n"
            f"📝 دلیل: {reason}"
        )
        
        if warnings == 1:
            await self.delete_message(update, context)
        elif warnings == 2:
            await self.db.mute_user(target_id, chat_id, Config.MUTE_DURATION_MINUTES)
            await self.restrict_user(chat_id, target_id, context)
        elif warnings >= 3:
            await self.db.ban_user(target_id, chat_id, user_id)
            try:
                await context.bot.ban_chat_member(chat_id, target_id)
            except:
                pass
        
        await self.send_log(context, f"⚠️ {user.full_name} به {target.full_name} اخطار داد | {reason}")
    
    async def cmd_mute(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """دستور میوت"""
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        level = self.get_user_level(user_id, chat_id)
        
        if level < 1:
            return
        
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ روی پیام کاربر ریپلای کنید.")
            return
        
        target = update.message.reply_to_message.from_user
        target_id = target.id
        
        if self.get_user_level(target_id, chat_id) >= level:
            await update.message.reply_text("❌ نمی‌توانید ادمین‌های بالاتر را میوت کنید.")
            return
        
        minutes = int(context.args[0]) if context.args else Config.MUTE_DURATION_MINUTES
        await self.db.mute_user(target_id, chat_id, minutes)
        await self.restrict_user(chat_id, target_id, context)
        
        await update.message.reply_text(f"🔇 {target.full_name} برای {minutes} دقیقه میوت شد.")
        await self.send_log(context, f"🔇 {target.full_name} میوت {minutes} دقیقه توسط {update.effective_user.full_name}")
    
    async def cmd_unmute(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """رفع میوت"""
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        
        if self.get_user_level(user_id, chat_id) < 1:
            return
        
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ روی پیام کاربر ریپلای کنید.")
            return
        
        target = update.message.reply_to_message.from_user
        await self.db.unmute_user(target.id, chat_id)
        await self.unrestrict_user(chat_id, target.id, context)
        
        await update.message.reply_text(f"✅ {target.full_name} از میوت خارج شد.")
    
    async def cmd_ban(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """دستور بن"""
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        level = self.get_user_level(user_id, chat_id)
        
        if level < 2:
            return
        
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ روی پیام کاربر ریپلای کنید.")
            return
        
        target = update.message.reply_to_message.from_user
        target_id = target.id
        
        if self.get_user_level(target_id, chat_id) >= level:
            await update.message.reply_text("❌ نمی‌توانید ادمین‌های بالاتر را بن کنید.")
            return
        
        await self.db.ban_user(target_id, chat_id, user_id)
        try:
            await context.bot.ban_chat_member(chat_id, target_id)
        except:
            pass
        
        await update.message.reply_text(f"🚫 {target.full_name} بن شد.")
        await self.send_log(context, f"🚫 {target.full_name} بن توسط {update.effective_user.full_name}")
    
    async def cmd_unban(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """آنبن"""
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        
        if self.get_user_level(user_id, chat_id) < 2:
            return
        
        if not context.args:
            await update.message.reply_text("❌ آیدی عددی کاربر را وارد کنید.")
            return
        
        target_id = int(context.args[0])
        await self.db.unban_user(target_id, chat_id)
        
        try:
            await context.bot.unban_chat_member(chat_id, target_id)
        except:
            pass
        
        await update.message.reply_text(f"✅ کاربر {target_id} آنبن شد.")
    
    async def cmd_kick(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """کیک کاربر"""
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        
        if self.get_user_level(user_id, chat_id) < 2:
            return
        
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ روی پیام کاربر ریپلای کنید.")
            return
        
        target = update.message.reply_to_message.from_user
        
        try:
            await context.bot.ban_chat_member(chat_id, target.id)
            await context.bot.unban_chat_member(chat_id, target.id)
            await update.message.reply_text(f"👢 {target.full_name} کیک شد.")
        except:
            await update.message.reply_text("❌ خطا در کیک کاربر.")
    
    async def cmd_purge(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """پاکسازی پیام‌ها"""
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        
        if self.get_user_level(user_id, chat_id) < 1:
            return
        
        count = min(int(context.args[0]) if context.args else 10, 100)
        
        try:
            deleted = 0
            async for msg in context.bot.get_chat_history(chat_id, limit=count):
                try:
                    await msg.delete()
                    deleted += 1
                except:
                    pass
                await asyncio.sleep(0.1)
            
            await update.message.reply_text(f"✅ {deleted} پیام پاکسازی شد.")
            await self.send_log(context, f"🧹 {deleted} پیام توسط {update.effective_user.full_name} پاک شد")
        except Exception as e:
            await update.message.reply_text(f"❌ خطا: {e}")
    
    async def cmd_lock(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """قفل کردن"""
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        
        if self.get_user_level(user_id, chat_id) < 2:
            return
        
        if not context.args:
            await update.message.reply_text(
                "🔒 انواع قفل:\n"
                "/lock chat - قفل چت\n"
                "/lock links - قفل لینک\n"
                "/lock forwards - قفل فوروارد\n"
                "/lock media - قفل رسانه\n"
                "/lock all - قفل همه"
            )
            return
        
        lock_type = context.args[0].lower()
        valid_types = ['chat', 'links', 'forwards', 'media', 'all']
        
        if lock_type not in valid_types:
            await update.message.reply_text("❌ نوع قفل نامعتبر است.")
            return
        
        if lock_type == 'all':
            for t in ['chat', 'links', 'forwards', 'media']:
                await self.db.set_lock(chat_id, t, True)
            await update.message.reply_text("🔒 همه موارد قفل شدند.")
        else:
            await self.db.set_lock(chat_id, lock_type, True)
            await update.message.reply_text(f"🔒 {lock_type} قفل شد.")
    
    async def cmd_unlock(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """باز کردن قفل"""
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        
        if self.get_user_level(user_id, chat_id) < 2:
            return
        
        if not context.args:
            await update.message.reply_text("❌ نوع قفل را مشخص کنید. /unlock all برای همه")
            return
        
        lock_type = context.args[0].lower()
        
        if lock_type == 'all':
            for t in ['chat', 'links', 'forwards', 'media']:
                await self.db.set_lock(chat_id, t, False)
            await update.message.reply_text("🔓 همه قفل‌ها باز شدند.")
        else:
            await self.db.set_lock(chat_id, lock_type, False)
            await update.message.reply_text(f"🔓 {lock_type} باز شد.")
    
    async def cmd_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """گزارش پیام مشکوک"""
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ روی پیام مورد نظر ریپلای کنید.")
            return
        
        reporter = update.effective_user
        reported_msg = update.message.reply_to_message
        reported_user = reported_msg.from_user
        chat_id = update.effective_chat.id
        
        # ساخت کیبورد برای ادمین‌ها
        keyboard = [
            [
                InlineKeyboardButton("⚠️ اخطار", callback_data=f"report_warn_{reported_user.id}"),
                InlineKeyboardButton("🚫 بن", callback_data=f"report_ban_{reported_user.id}")
            ],
            [
                InlineKeyboardButton("❌ نادیده", callback_data=f"report_ignore_{reported_user.id}")
            ]
        ]
        
        # ارسال به ادمین‌ها
        admin_text = (
            f"📢 <b>گزارش جدید</b>\n"
            f"👤 گزارش‌دهنده: {reporter.full_name}\n"
            f"👤 کاربر: {reported_user.full_name}\n"
            f"🆔 آیدی: <code>{reported_user.id}</code>\n"
            f"💬 پیام: {reported_msg.text or 'رسانه'}"
        )
        
        # ارسال به همه ادمین‌ها
        for admin_id in Config.ADMIN_IDS + [Config.OWNER_ID]:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=admin_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.HTML
                )
            except:
                pass
        
        await update.message.reply_text("✅ گزارش شما به ادمین‌ها ارسال شد.")
        await self.send_log(context, f"📢 گزارش از {reporter.full_name} برای {reported_user.full_name}")
    
    async def cmd_setadmin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """تنظیم ادمین (فقط founder)"""
        if update.effective_user.id != Config.OWNER_ID:
            return
        
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ روی پیام کاربر ریپلای کنید.")
            return
        
        target = update.message.reply_to_message.from_user
        chat_id = update.effective_chat.id
        
        await self.db.add_staff(target.id, chat_id, 'admin', update.effective_user.id)
        Config.ADMIN_IDS.append(target.id)
        
        await update.message.reply_text(f"✅ {target.full_name} به عنوان ادمین اضافه شد.")
        await self.send_log(context, f"👑 {target.full_name} ادمین شد")
    
    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """آمار گروه"""
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        
        if self.get_user_level(user_id, chat_id) < 3:
            return
        
        stats_text = (
            "📊 <b>آمار ربات</b>\n\n"
            f"👥 ادمین‌ها: {len(Config.ADMIN_IDS)}\n"
            f"👮 مدیران: {len(Config.MODERATOR_IDS)}\n"
            f"🌙 حالت شب: {'فعال' if self.is_night_time() else 'غیرفعال'}\n"
            f"⏰ ساعت: {datetime.now().strftime('%H:%M:%S')}"
        )
        
        await update.message.reply_text(stats_text, parse_mode=ParseMode.HTML)
    
    async def cmd_broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """پیام همگانی (فقط founder)"""
        if update.effective_user.id != Config.OWNER_ID:
            return
        
        if not context.args:
            await update.message.reply_text("❌ متن پیام را وارد کنید.")
            return
        
        text = ' '.join(context.args)
        await update.message.reply_text(f"📢 پیام همگانی:\n\n{text}")
    
    # ====================== هندلر دکمه‌ها ======================
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """پردازش دکمه‌های اینلاین"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        user_id = query.from_user.id
        
        # کپچا
        if data.startswith('captcha_'):
            answer = int(data.split('_')[1])
            chat_id = query.message.chat_id
            
            correct = await self.db.get_captcha(user_id, chat_id)
            
            if correct is None:
                await query.message.delete()
                return
            
            if answer == correct:
                self.pending_captcha.discard((user_id, chat_id))
                await self.db.remove_captcha(user_id, chat_id)
                await query.message.delete()
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"✅ {query.from_user.full_name} تایید شد. خوش آمدید!"
                )
            else:
                await query.answer("❌ اشتباه! دوباره تلاش کنید.", show_alert=True)
        
        # گزارش‌ها
        elif data.startswith('report_'):
            action, target_id = data.replace('report_', '').split('_')
            target_id = int(target_id)
            chat_id = query.message.chat_id
            admin_name = query.from_user.full_name
            
            if action == 'warn':
                await self.db.add_warning(target_id, chat_id)
                await query.message.edit_text(
                    f"{query.message.text}\n\n✅ اخطار توسط {admin_name}",
                    parse_mode=ParseMode.HTML
                )
            elif action == 'ban':
                try:
                    await context.bot.ban_chat_member(chat_id, target_id)
                    await query.message.edit_text(
                        f"{query.message.text}\n\n🚫 بن توسط {admin_name}",
                        parse_mode=ParseMode.HTML
                    )
                except:
                    await query.answer("❌ خطا در بن کاربر", show_alert=True)
            elif action == 'ignore':
                await query.message.edit_text(
                    f"{query.message.text}\n\n❌ نادیده گرفته شد توسط {admin_name}",
                    parse_mode=ParseMode.HTML
                )
    
    # ====================== راه‌اندازی ======================
    
    def run(self):
        """اجرای ربات"""
        Config.validate()
        
        # ساخت application
        app = Application.builder().token(Config.BOT_TOKEN).build()
        
        # راه‌اندازی دیتابیس
        asyncio.get_event_loop().run_until_complete(self.db.init())
        
        # هندلر کاربر جدید
        app.add_handler(ChatMemberHandler(self.handle_new_member, ChatMemberHandler.CHAT_MEMBER))
        
        # هندلر پیام‌ها
        app.add_handler(MessageHandler(
            filters.TEXT | filters.PHOTO | filters.VIDEO | filters.ANIMATION | 
            filters.STICKER | filters.Document.ALL | filters.FORWARDED,
            self.handle_message
        ))
        
        # هندلر دکمه‌ها
        app.add_handler(CallbackQueryHandler(self.handle_callback))
        
        # دستورات
        commands = {
            'warn': self.cmd_warn,
            'mute': self.cmd_mute,
            'unmute': self.cmd_unmute,
            'ban': self.cmd_ban,
            'unban': self.cmd_unban,
            'kick': self.cmd_kick,
            'purge': self.cmd_purge,
            'lock': self.cmd_lock,
            'unlock': self.cmd_unlock,
            'report': self.cmd_report,
            'setadmin': self.cmd_setadmin,
            'stats': self.cmd_stats,
            'broadcast': self.cmd_broadcast,
        }
        
        for cmd, handler in commands.items():
            app.add_handler(CommandHandler(cmd, handler))
        
        # اجرا
        logger.info("🤖 ربات در حال اجرا...")
        app.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES
        )

# ====================== اجرای اصلی ======================
if __name__ == '__main__':
    bot = GuardianBot()
    bot.run()