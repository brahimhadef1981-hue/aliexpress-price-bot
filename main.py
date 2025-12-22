import os
import re
import time
import asyncio
import hashlib
import hmac
import aiohttp
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
import threading
from flask import Flask, Response
import asyncpg
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters
)
from telegram.error import BadRequest

# ============================================================================
# CONFIGURATION - USE ENVIRONMENT VARIABLES FOR KOYEB
# ============================================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN")
ALIEXPRESS_APP_KEY = os.getenv("ALIEXPRESS_APP_KEY", "YOUR_APP_KEY")
ALIEXPRESS_APP_SECRET = os.getenv("ALIEXPRESS_APP_SECRET", "YOUR_APP_SECRET")
ALIEXPRESS_TRACKING_ID = os.getenv("ALIEXPRESS_TRACKING_ID", "YOUR_TRACKING_ID")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:pass@host:port/db")
PORT = int(os.getenv("PORT", 8000))

# Monitoring configuration
CONCURRENT_REQUESTS = 10
REQUEST_DELAY = 1
MONITORING_INTERVAL = 300
PRODUCTS_PER_CYCLE = 100
RATE_LIMIT_RETRY_DELAY = 30
MAX_RETRIES = 3
REQUEST_TIMEOUT = 15
MONTHLY_UPDATE_REMINDER_DAYS = 30
UPDATE_RESPONSE_DEADLINE_DAYS = 3
MONTHLY_CHECK_INTERVAL = 86400

# States for conversation
SELECTING_COUNTRY, ENTERING_LINK, CHANGING_COUNTRY, MANAGING_PRODUCTS, VIEWING_HISTORY = range(5)

# Flask app for health checks
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return Response("Bot is running!", status=200)

@flask_app.route('/health')
def health():
    return Response("OK", status=200)

# ============================================================================
# ASYNC DATABASE MANAGER - USES ASYNCPG
# ============================================================================
class DatabaseManager:
    _pool = None
    
    @classmethod
    async def get_pool(cls):
        if cls._pool is None:
            cls._pool = await asyncpg.create_pool(
                DATABASE_URL,
                min_size=1,
                max_size=10,
                command_timeout=60
            )
        return cls._pool
    
    @classmethod
    async def close_pool(cls):
        if cls._pool:
            await cls._pool.close()
    
    @staticmethod
    async def init_database():
        """Initialize database tables"""
        pool = await DatabaseManager.get_pool()
        try:
            async with pool.acquire() as conn:
                # Users table
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        user_id BIGINT PRIMARY KEY,
                        username VARCHAR(255),
                        country VARCHAR(10),
                        date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_update_reminder TIMESTAMP,
                        update_deadline TIMESTAMP,
                        needs_update_response BOOLEAN DEFAULT FALSE
                    )
                """)
                
                # Products table
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS products (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT,
                        product_id VARCHAR(50),
                        product_url TEXT,
                        title TEXT,
                        current_price DECIMAL(10,2),
                        original_price DECIMAL(10,2),
                        currency VARCHAR(10),
                        image_url TEXT,
                        country VARCHAR(10),
                        date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_checked TIMESTAMP,
                        UNIQUE(user_id, product_id)
                    )
                """)
                
                # Price history table
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS price_history (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT,
                        product_id VARCHAR(50),
                        product_title TEXT,
                        old_price DECIMAL(10,2),
                        new_price DECIMAL(10,2),
                        change_amount DECIMAL(10,2),
                        change_percent DECIMAL(10,2),
                        currency VARCHAR(10),
                        date_recorded TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Create indexes
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_products_user ON products(user_id)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_products_last_checked ON products(last_checked)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_history_user_product ON price_history(user_id, product_id)")
                
                print("✅ Database tables initialized")
        except Exception as e:
            print(f"❌ Error initializing database: {e}")
            raise

    @staticmethod
    async def save_user(user_id: int, username: str, country: str):
        """Save or update user"""
        pool = await DatabaseManager.get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO users (user_id, username, country)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (user_id) 
                    DO UPDATE SET country = EXCLUDED.country, username = EXCLUDED.username
                """, user_id, username, country)
        except Exception as e:
            print(f"❌ Error saving user: {e}")

    @staticmethod
    async def get_user_country(user_id: int) -> Optional[str]:
        """Get user's current country"""
        pool = await DatabaseManager.get_pool()
        try:
            async with pool.acquire() as conn:
                result = await conn.fetchval("SELECT country FROM users WHERE user_id = $1", user_id)
                return result
        except Exception as e:
            print(f"❌ Error getting user country: {e}")
            return None

    @staticmethod
    async def update_user_products_country(user_id: int, new_country: str) -> int:
        """Update country for all products of a user"""
        pool = await DatabaseManager.get_pool()
        try:
            async with pool.acquire() as conn:
                result = await conn.execute("""
                    UPDATE products SET country = $1 WHERE user_id = $2
                """, new_country, user_id)
                return int(result.split()[-1])
        except Exception as e:
            print(f"❌ Error updating products country: {e}")
            return 0

    @staticmethod
    async def save_product(user_id: int, product_id: str, product_url: str, title: str,
                          price: float, original_price: float, currency: str, image_url: str, country: str):
        """Save product to database"""
        pool = await DatabaseManager.get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO products (user_id, product_id, product_url, title, current_price,
                                         original_price, currency, image_url, country, last_checked)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, CURRENT_TIMESTAMP)
                    ON CONFLICT (user_id, product_id) 
                    DO UPDATE SET 
                        product_url = EXCLUDED.product_url,
                        current_price = EXCLUDED.current_price,
                        original_price = EXCLUDED.original_price,
                        country = EXCLUDED.country,
                        last_checked = CURRENT_TIMESTAMP
                """, user_id, product_id, product_url, title, price, original_price, 
                    currency, image_url, country)
        except Exception as e:
            print(f"❌ Error saving product: {e}")

    @staticmethod
    async def get_all_products() -> List[Dict]:
        """Get all products for monitoring"""
        pool = await DatabaseManager.get_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT user_id, product_id, product_url, title, current_price,
                           original_price, currency, image_url, country, date_added, last_checked
                    FROM products
                """)
                return [dict(row) for row in rows]
        except Exception as e:
            print(f"❌ Error getting products: {e}")
            return []

    @staticmethod
    async def get_products_to_check(limit: int) -> List[Dict]:
        """Get products that need to be checked"""
        pool = await DatabaseManager.get_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT user_id, product_id, product_url, title, current_price,
                           original_price, currency, image_url, country, date_added, last_checked
                    FROM products
                    ORDER BY last_checked ASC NULLS FIRST
                    LIMIT $1
                """, limit)
                return [dict(row) for row in rows]
        except Exception as e:
            print(f"❌ Error getting products to check: {e}")
            return []

    @staticmethod
    async def update_product_price(user_id: int, product_id: str, new_price: float, 
                                   country: str = None, product_url: str = None):
        """Update product price and last checked time"""
        pool = await DatabaseManager.get_pool()
        try:
            async with pool.acquire() as conn:
                if product_url and country:
                    await conn.execute("""
                        UPDATE products 
                        SET current_price = $1, country = $2, product_url = $3, last_checked = CURRENT_TIMESTAMP
                        WHERE user_id = $4 AND product_id = $5
                    """, new_price, country, product_url, user_id, product_id)
                elif country:
                    await conn.execute("""
                        UPDATE products 
                        SET current_price = $1, country = $2, last_checked = CURRENT_TIMESTAMP
                        WHERE user_id = $3 AND product_id = $4
                    """, new_price, country, user_id, product_id)
                else:
                    await conn.execute("""
                        UPDATE products 
                        SET current_price = $1, last_checked = CURRENT_TIMESTAMP
                        WHERE user_id = $2 AND product_id = $3
                    """, new_price, user_id, product_id)
        except Exception as e:
            print(f"❌ Error updating product price: {e}")

    @staticmethod
    async def save_price_change(user_id: int, product_id: str, title: str, old_price: float,
                                new_price: float, currency: str):
        """Save price change to history"""
        if abs(new_price - old_price) < 0.01:
            return
        
        pool = await DatabaseManager.get_pool()
        try:
            change = new_price - old_price
            change_percent = ((new_price - old_price) / old_price * 100) if old_price > 0 else 0
            
            async with pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO price_history (user_id, product_id, product_title, old_price,
                                              new_price, change_amount, change_percent, currency)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """, user_id, product_id, title, old_price, new_price, 
                    round(change, 2), round(change_percent, 2), currency)
                print(f"✅ Price change archived: {title[:30]} - ${change:+.2f} ({change_percent:+.1f}%)")
        except Exception as e:
            print(f"❌ Error saving price change: {e}")

    @staticmethod
    async def get_user_products(user_id: int) -> List[Dict]:
        """Get all products for a specific user"""
        pool = await DatabaseManager.get_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT user_id, product_id, product_url, title, current_price,
                           original_price, currency, image_url, country, date_added, last_checked
                    FROM products WHERE user_id = $1
                """, user_id)
                return [dict(row) for row in rows]
        except Exception as e:
            print(f"❌ Error getting user products: {e}")
            return []

    @staticmethod
    async def delete_product(user_id: int, product_id: str) -> bool:
        """Delete a product from monitoring"""
        pool = await DatabaseManager.get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM products WHERE user_id = $1 AND product_id = $2", 
                                  user_id, product_id)
                return True
        except Exception as e:
            print(f"❌ Error deleting product: {e}")
            return False

    @staticmethod
    async def get_price_history(user_id: int, product_id: str, months: int = None) -> List[Dict]:
        """Get price history for a product"""
        pool = await DatabaseManager.get_pool()
        try:
            async with pool.acquire() as conn:
                if months:
                    rows = await conn.fetch("""
                        SELECT product_title as title, old_price, new_price, change_amount,
                               change_percent, currency, date_recorded as date
                        FROM price_history
                        WHERE user_id = $1 AND product_id = $2
                        AND date_recorded >= CURRENT_TIMESTAMP - INTERVAL '%s months'
                        ORDER BY date_recorded DESC
                    """ % months, user_id, product_id)
                else:
                    rows = await conn.fetch("""
                        SELECT product_title as title, old_price, new_price, change_amount,
                               change_percent, currency, date_recorded as date
                        FROM price_history
                        WHERE user_id = $1 AND product_id = $2
                        ORDER BY date_recorded DESC
                    """, user_id, product_id)
                return [dict(row) for row in rows]
        except Exception as e:
            print(f"❌ Error getting price history: {e}")
            return []

    @staticmethod
    async def get_all_user_price_history(user_id: int, months: int = None) -> Dict[str, Any]:
        """Get price history for all products of a user"""
        products = await DatabaseManager.get_user_products(user_id)
        history_by_product = {}
        
        for product in products:
            history = await DatabaseManager.get_price_history(user_id, product['product_id'], months)
            if history:
                history_by_product[product['product_id']] = {
                    'product': product,
                    'history': history
                }
        
        return history_by_product

    @staticmethod
    async def set_update_reminder(user_id: int):
        """Set monthly update reminder for user"""
        pool = await DatabaseManager.get_pool()
        try:
            deadline = datetime.now() + timedelta(days=UPDATE_RESPONSE_DEADLINE_DAYS)
            async with pool.acquire() as conn:
                await conn.execute("""
                    UPDATE users 
                    SET last_update_reminder = CURRENT_TIMESTAMP,
                        update_deadline = $1,
                        needs_update_response = TRUE
                    WHERE user_id = $2
                """, deadline, user_id)
        except Exception as e:
            print(f"❌ Error setting update reminder: {e}")

    @staticmethod
    async def clear_update_reminder(user_id: int):
        """Clear update reminder after user responds"""
        pool = await DatabaseManager.get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute("""
                    UPDATE users 
                    SET last_update_reminder = CURRENT_TIMESTAMP,
                        update_deadline = NULL,
                        needs_update_response = FALSE
                    WHERE user_id = $1
                """, user_id)
        except Exception as e:
            print(f"❌ Error clearing update reminder: {e}")

    @staticmethod
    async def get_users_needing_reminder() -> List[int]:
        """Get users who need monthly update reminder"""
        pool = await DatabaseManager.get_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT user_id FROM users
                    WHERE last_update_reminder IS NULL
                    OR last_update_reminder < CURRENT_TIMESTAMP - INTERVAL '%s days'
                """ % MONTHLY_UPDATE_REMINDER_DAYS)
                return [row['user_id'] for row in rows]
        except Exception as e:
            print(f"❌ Error getting users needing reminder: {e}")
            return []

    @staticmethod
    async def get_users_past_deadline() -> List[int]:
        """Get users who didn't respond and are past deadline"""
        pool = await DatabaseManager.get_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT user_id FROM users
                    WHERE needs_update_response = TRUE
                    AND update_deadline < CURRENT_TIMESTAMP
                """)
                return [row['user_id'] for row in rows]
        except Exception as e:
            print(f"❌ Error getting users past deadline: {e}")
            return []

    @staticmethod
    async def delete_all_user_data(user_id: int):
        """Delete all products and price history for a user"""
        pool = await DatabaseManager.get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM products WHERE user_id = $1", user_id)
                await conn.execute("DELETE FROM price_history WHERE user_id = $1", user_id)
                print(f"✅ Deleted all data for user {user_id}")
                return True
        except Exception as e:
            print(f"❌ Error deleting user data: {e}")
            return False

# ============================================================================
# ASYNC ALIEXPRESS API CLIENT
# ============================================================================
class AliExpressAPI:
    def __init__(self, app_key: str, app_secret: str, tracking_id: str):
        self.app_key = app_key
        self.app_secret = app_secret
        self.tracking_id = tracking_id
        self.api_url = "https://api-sg.aliexpress.com/sync"
        self.timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        self.session = None

    async def get_session(self):
        if self.session is None or self.session.closed:
            connector = aiohttp.TCPConnector(limit=50, limit_per_host=10, ttl_dns_cache=300)
            self.session = aiohttp.ClientSession(connector=connector, timeout=self.timeout)
        return self.session

    async def close_session(self):
        if self.session and not self.session.closed:
            await self.session.close()

    @staticmethod
    def _now_ms() -> str:
        return str(int(time.time() * 1000))

    def generate_signature(self, params: Dict[str, Any]) -> str:
        params_to_sign = {k: str(v) for k, v in params.items() if k != "sign" and v is not None and v != ""}
        sorted_items = sorted(params_to_sign.items(), key=lambda x: x[0])
        canonical = "".join(f"{k}{v}" for k, v in sorted_items)
        signature = hmac.new(self.app_secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.md5).hexdigest().upper()
        return signature

    @staticmethod
    def extract_product_id(url: str) -> Optional[str]:
        patterns = [r"/item/(\d+)\.html", r"/i/(\d+)\.html", r"/(\d+)\.html", r"item/(\d+)", r"/goods/(\d+)", r"product/(\d+)", r"/dp/(\d+)"]
        for p in patterns:
            m = re.search(p, url)
            if m:
                return m.group(1)
        return None

    @staticmethod
    def build_product_url(product_id: str) -> str:
        return f"https://www.aliexpress.com/item/{product_id}.html"

    async def resolve_shortened_url(self, url: str, max_retries: int = 3) -> str:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        session = await self.get_session()
        
        for attempt in range(max_retries):
            try:
                async with session.head(url, allow_redirects=True, headers=headers) as response:
                    final_url = str(response.url)
                    if self.extract_product_id(final_url):
                        return final_url
            except Exception:
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)
                    continue
                return url
        return url

    @staticmethod
    def is_shortened_url(url: str) -> bool:
        patterns = ["s.click.aliexpress.com", "a.aliexpress.com", "/e/_", "ali.ski"]
        return any(pattern in url.lower() for pattern in patterns)

    @staticmethod
    def is_rate_limited(error_msg: str) -> bool:
        rate_limit_patterns = ["frequency exceeds the limit", "rate limit", "too many requests"]
        return any(pattern in error_msg.lower() for pattern in rate_limit_patterns)

    async def get_product_details(self, product_id: str, country: str = "US", retry_count: int = 0) -> Dict[str, Any]:
        start_time = time.time()
        method = "aliexpress.affiliate.productdetail.get"
        
        params = {
            "app_key": self.app_key, "format": "json", "method": method, "sign_method": "hmac",
            "timestamp": self._now_ms(), "v": "2.0", "tracking_id": self.tracking_id,
            "product_ids": str(product_id), "target_currency": "USD", "target_language": "EN", "country": country
        }
        params["sign"] = self.generate_signature(params)
        session = await self.get_session()
        
        try:
            async with session.get(self.api_url, params=params) as response:
                response.raise_for_status()
                data = await response.json()
                elapsed_time = time.time() - start_time
                
                if "error_response" in data:
                    error_msg = data["error_response"].get("msg", "API Error")
                    if self.is_rate_limited(error_msg) and retry_count < MAX_RETRIES:
                        wait_time = RATE_LIMIT_RETRY_DELAY * (2 ** retry_count)
                        await asyncio.sleep(wait_time)
                        return await self.get_product_details(product_id, country, retry_count + 1)
                    return {"success": False, "error": error_msg, "time_taken": elapsed_time}
                
                resp_key = next((k for k in data.keys() if k.endswith("_response")), None)
                if not resp_key:
                    return {"success": False, "error": "Invalid response", "time_taken": elapsed_time}
                
                result = data[resp_key].get("resp_result", {}).get("result", {})
                products = result.get("products", {}).get("product", [])
                
                if not products:
                    return {"success": False, "error": "Product not found", "time_taken": elapsed_time}
                
                product = products[0] if isinstance(products, list) else products
                sale_price = product.get("target_sale_price") or product.get("sale_price")
                original_price = product.get("target_original_price") or product.get("original_price")
                
                def to_float(val):
                    if val is None: return None
                    try:
                        return float(str(val).replace("USD", "").replace("$", "").replace(",", "").strip())
                    except: return None
                
                current_price = to_float(sale_price)
                if current_price is None:
                    return {"success": False, "error": "No price available", "time_taken": elapsed_time}
                
                return {
                    "success": True, "product_id": str(product.get("product_id", product_id)),
                    "title": product.get("product_title", "N/A"), "price": current_price,
                    "original_price": to_float(original_price) or current_price, "currency": "USD",
                    "image_url": product.get("product_main_image_url", ""),
                    "product_url": self.build_product_url(str(product.get("product_id", product_id))),
                    "time_taken": elapsed_time
                }
        except asyncio.TimeoutError:
            return {"success": False, "error": "Request timeout", "time_taken": time.time() - start_time}
        except Exception as e:
            return {"success": False, "error": str(e), "time_taken": time.time() - start_time}

    async def generate_affiliate_link(self, product_url: str, country: str = "US") -> Optional[str]:
        method = "aliexpress.affiliate.link.generate"
        params = {
            "app_key": self.app_key, "format": "json", "method": method, "sign_method": "hmac",
            "timestamp": self._now_ms(), "v": "2.0", "tracking_id": self.tracking_id,
            "promotion_link_type": "0", "source_values": product_url
        }
        params["sign"] = self.generate_signature(params)
        session = await self.get_session()
        
        try:
            async with session.post(self.api_url, data=params) as response:
                data = await response.json()
                if "error_response" in data:
                    return product_url
                resp_data = data.get("aliexpress_affiliate_link_generate_response", {})
                result = resp_data.get("resp_result", {})
                if result.get("resp_code") == 200:
                    links = result.get("result", {}).get("promotion_links", {}).get("promotion_link", [])
                    if links:
                        return links[0].get("promotion_link")
                return product_url
        except Exception:
            return product_url

# Global API instance
api_instance = None

async def get_api_instance():
    global api_instance
    if api_instance is None:
        api_instance = AliExpressAPI(ALIEXPRESS_APP_KEY, ALIEXPRESS_APP_SECRET, ALIEXPRESS_TRACKING_ID)
    return api_instance

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================
async def safe_edit_message(query, text, reply_markup=None, parse_mode='HTML'):
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except BadRequest:
        try:
            await query.message.delete()
        except: pass
        await query.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        print(f"Error editing message: {e}")
        try:
            await query.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except: pass

# ============================================================================
# TELEGRAM BOT HANDLERS
# ============================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    keyboard = [
        [InlineKeyboardButton("🇫🇷 France", callback_data="country_FR"),
         InlineKeyboardButton("🇮🇹 Italy", callback_data="country_IT")],
        [InlineKeyboardButton("🇺🇸 United States", callback_data="country_US")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    welcome_text = (
        f"👋 <b>Welcome {user.first_name}!</b>\n\n"
        "🛍️ <b>AliExpress Price Monitor Bot</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "I will help you track AliExpress product prices!\n\n"
        "📍 <b>Please select your country:</b>"
    )
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='HTML')
    return SELECTING_COUNTRY

async def country_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    country = query.data.split("_")[1]
    user = update.effective_user
    
    await DatabaseManager.save_user(user.id, user.username or user.first_name, country)
    updated_count = await DatabaseManager.update_user_products_country(user.id, country)
    context.user_data['country'] = country
    
    country_flags = {"FR": "🇫🇷", "IT": "🇮🇹", "US": "🇺🇸"}
    message = f"✅ <b>Country Selected: {country_flags.get(country, '')} {country}</b>\n\n"
    
    if updated_count > 0:
        message += f"🔄 Updated {updated_count} existing products\n\n"
    
    message += (
        "📎 <b>Now send me an AliExpress product link:</b>\n\n"
        "<i>Supported formats:</i>\n"
        "• <code>https://www.aliexpress.com/item/xxxxx.html</code>\n"
        "• <code>https://s.click.aliexpress.com/e/_xxxxx</code>\n\n"
        "💡 Or use the menu below:"
    )
    
    keyboard = [
        [InlineKeyboardButton("➕ Add Product", callback_data="add_product")],
        [InlineKeyboardButton("📋 My Products", callback_data="view_myproducts")],
        [InlineKeyboardButton("📊 Price History", callback_data="view_history")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="show_help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await safe_edit_message(query, message, reply_markup)
    return ENTERING_LINK

async def add_product_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    message = (
        "📎 <b>Send me an AliExpress product link:</b>\n\n"
        "<i>Supported formats:</i>\n"
        "• <code>https://www.aliexpress.com/item/xxxxx.html</code>\n"
        "• <code>https://s.click.aliexpress.com/e/_xxxxx</code>\n\n"
        "💡 Just paste the link and send it to me!"
    )
    
    keyboard = [
        [InlineKeyboardButton("📋 My Products", callback_data="view_myproducts")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await safe_edit_message(query, message, reply_markup)
    return ENTERING_LINK

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return ENTERING_LINK
    
    total_start_time = time.time()
    user_id = update.effective_user.id
    product_url = update.message.text.strip()
    
    if "aliexpress" not in product_url.lower():
        keyboard = [
            [InlineKeyboardButton("➕ Add Product", callback_data="add_product")],
            [InlineKeyboardButton("📋 My Products", callback_data="view_myproducts")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
        ]
        await update.message.reply_text(
            "❌ <b>Invalid Link</b>\n\nPlease send a valid AliExpress product link.",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML'
        )
        return ENTERING_LINK
    
    processing_msg = await update.message.reply_text("⏳ Processing...")
    country = await DatabaseManager.get_user_country(user_id) or "US"
    api = await get_api_instance()
    
    if api.is_shortened_url(product_url):
        await processing_msg.edit_text("🔗 Resolving shortened URL...")
        product_url = await api.resolve_shortened_url(product_url)
    
    product_id = api.extract_product_id(product_url)
    
    if not product_id:
        keyboard = [
            [InlineKeyboardButton("➕ Try Again", callback_data="add_product")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
        ]
        await processing_msg.edit_text(
            "❌ <b>Could not extract product ID</b>\n\nPlease send a valid AliExpress link.",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML'
        )
        return ENTERING_LINK
    
    await processing_msg.edit_text("📊 Fetching product details...")
    result = await api.get_product_details(product_id, country)
    api_time = result.get('time_taken', 0)
    
    if not result.get("success"):
        keyboard = [
            [InlineKeyboardButton("➕ Try Another", callback_data="add_product")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
        ]
        await processing_msg.edit_text(
            f"❌ <b>Cannot monitor this product</b>\n\n<b>Reason:</b> {result.get('error')}\n"
            f"<b>Time taken:</b> {api_time:.2f}s\n\n💡 Try another product.",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML'
        )
        return ENTERING_LINK
    
    affiliate_link = await api.generate_affiliate_link(result['product_url'], country)
    
    await DatabaseManager.save_product(
        user_id=user_id, product_id=product_id, product_url=affiliate_link,
        title=result['title'], price=result['price'], original_price=result['original_price'],
        currency=result['currency'], image_url=result['image_url'], country=country
    )
    
    total_time = time.time() - total_start_time
    discount = result['original_price'] - result['price']
    discount_percent = (discount / result['original_price'] * 100) if result['original_price'] > 0 else 0
    
    message = (
        "✅ <b>Product Added Successfully!</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📦 <b>{result['title'][:80]}...</b>\n\n"
        f"💵 <b>Current:</b> ${result['price']:.2f}\n"
        f"💰 <b>Original:</b> ${result['original_price']:.2f}\n"
    )
    
    if discount > 0:
        message += f"🏷️ <b>Discount:</b> ${discount:.2f} ({discount_percent:.1f}% OFF)\n"
    
    message += (
        f"🌍 <b>Country:</b> {country}\n🆔 <b>ID:</b> {product_id}\n\n"
        f"⏱️ <b>Processing Time:</b> {total_time:.2f}s\n\n"
        f"🔔 <b>Price monitoring active!</b>\n\n🔗 <code>{affiliate_link}</code>"
    )
    
    keyboard = [
        [InlineKeyboardButton("➕ Add Another Product", callback_data="add_product")],
        [InlineKeyboardButton("📋 My Products", callback_data="view_myproducts")],
        [InlineKeyboardButton("📊 Price History", callback_data="view_history")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if result.get('image_url'):
        try:
            await update.message.reply_photo(
                photo=result['image_url'], caption=message,
                reply_markup=reply_markup, parse_mode='HTML'
            )
            await processing_msg.delete()
        except:
            await processing_msg.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')
    else:
        await processing_msg.edit_text(message, reply_markup=reply_markup, parse_mode='HTML')
    
    return ENTERING_LINK

async def view_my_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    
    user_id = update.effective_user.id
    products = await DatabaseManager.get_user_products(user_id)
    
    if not products:
        keyboard = [
            [InlineKeyboardButton("➕ Add Product", callback_data="add_product")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
        ]
        message = "📭 <b>No Products Yet</b>\n\nYou haven't added any products to monitor."
        if query:
            await safe_edit_message(query, message, InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        return
    
    message = f"📦 <b>Your Monitored Products ({len(products)}):</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    for i, product in enumerate(products[:5], 1):
        title = product['title'][:40] + "..." if len(str(product['title'])) > 40 else product['title']
        message += f"{i}. <b>{title}</b>\n   💵 ${float(product['current_price']):.2f}\n   🌍 {product['country']}\n\n"
    
    if len(products) > 5:
        message += f"<i>...and {len(products) - 5} more</i>\n\n"
    
    keyboard = [
        [InlineKeyboardButton("➕ Add Product", callback_data="add_product")],
        [InlineKeyboardButton("🗑️ Manage Products", callback_data="manage_products")],
        [InlineKeyboardButton("📊 Price History", callback_data="view_history")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
    ]
    
    if query:
        await safe_edit_message(query, message, InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def manage_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    products = await DatabaseManager.get_user_products(user_id)
    
    message = f"🗑️ <b>Manage Products ({len(products)}):</b>\n━━━━━━━━━━━━━━━━━━━━━\n\nSelect a product to delete:\n\n"
    
    keyboard = []
    for product in products:
        title = product['title'][:30] + "..." if len(str(product['title'])) > 30 else product['title']
        keyboard.append([InlineKeyboardButton(
            f"❌ {title} - ${float(product['current_price']):.2f}",
            callback_data=f"delete_{product['product_id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("➕ Add Product", callback_data="add_product")])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="view_myproducts")])
    
    await safe_edit_message(query, message, InlineKeyboardMarkup(keyboard))

async def delete_product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    product_id = query.data.split("_", 1)[1]
    user_id = update.effective_user.id
    
    products = await DatabaseManager.get_user_products(user_id)
    product_title = next((p['title'][:50] for p in products if p['product_id'] == product_id), "Product")
    
    success = await DatabaseManager.delete_product(user_id, product_id)
    
    if success:
        keyboard = [
            [InlineKeyboardButton("➕ Add Product", callback_data="add_product")],
            [InlineKeyboardButton("📋 My Products", callback_data="view_myproducts")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
        ]
        await safe_edit_message(query, f"✅ <b>Product Deleted</b>\n\n<b>{product_title}</b>", InlineKeyboardMarkup(keyboard))
    else:
        await safe_edit_message(query, "❌ Error deleting product.")

async def view_price_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    
    message = (
        "📊 <b>Price History</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Select time period to view price changes:"
    )
    
    keyboard = [
        [InlineKeyboardButton("1 Month", callback_data="history_1"),
         InlineKeyboardButton("2 Months", callback_data="history_2"),
         InlineKeyboardButton("3 Months", callback_data="history_3")],
        [InlineKeyboardButton("4 Months", callback_data="history_4"),
         InlineKeyboardButton("5 Months", callback_data="history_5"),
         InlineKeyboardButton("6 Months", callback_data="history_6")],
        [InlineKeyboardButton("📅 All Time", callback_data="history_all")],
        [InlineKeyboardButton("➕ Add Product", callback_data="add_product")],
        [InlineKeyboardButton("🔙 Back", callback_data="view_myproducts")]
    ]
    
    if query:
        await safe_edit_message(query, message, InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def show_price_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    period = query.data.split("_")[1]
    months = None if period == "all" else int(period)
    
    history_data = await DatabaseManager.get_all_user_price_history(user_id, months)
    
    if not history_data:
        keyboard = [
            [InlineKeyboardButton("➕ Add Products", callback_data="add_product")],
            [InlineKeyboardButton("🔙 Back", callback_data="view_history")]
        ]
        period_text = f"last {period} month(s)" if period != "all" else "all time"
        await safe_edit_message(query, f"📊 <b>No Price Changes</b>\n\nNo changes for {period_text}.", InlineKeyboardMarkup(keyboard))
        return
    
    period_text = f"Last {period} Month(s)" if period != "all" else "All Time"
    message = f"📊 <b>Price History - {period_text}</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    total_changes = 0
    for product_id, data in history_data.items():
        product = data['product']
        history = data['history']
        total_changes += len(history)
        
        title = str(product['title'])[:40] + "..." if len(str(product['title'])) > 40 else product['title']
        message += f"📦 <b>{title}</b>\n"
        
        for change in history[:3]:
            date = change['date'].strftime("%m/%d") if hasattr(change['date'], 'strftime') else str(change['date'])[:10]
            emoji = "📉" if float(change['change_amount']) < 0 else "📈"
            message += (
                f"   {emoji} ${float(change['old_price']):.2f} → ${float(change['new_price']):.2f} "
                f"({float(change['change_percent']):+.1f}%) - {date}\n"
            )
        
        if len(history) > 3:
            message += f"   <i>...and {len(history) - 3} more changes</i>\n"
        message += "\n"
    
    message += f"📈 <b>Total Changes:</b> {total_changes}"
    
    keyboard = [
        [InlineKeyboardButton("➕ Add Product", callback_data="add_product")],
        [InlineKeyboardButton("🔄 Change Period", callback_data="view_history")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
    ]
    
    await safe_edit_message(query, message, InlineKeyboardMarkup(keyboard))

async def handle_update_continue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    await DatabaseManager.clear_update_reminder(user_id)
    products = await DatabaseManager.get_user_products(user_id)
    
    keyboard = [
        [InlineKeyboardButton("➕ Add Product", callback_data="add_product")],
        [InlineKeyboardButton("📋 My Products", callback_data="view_myproducts")],
        [InlineKeyboardButton("📊 Price History", callback_data="view_history")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
    ]
    
    await safe_edit_message(
        query,
        f"✅ <b>Monitoring Continued</b>\n\nYour <b>{len(products)}</b> product(s) will continue to be monitored.",
        InlineKeyboardMarkup(keyboard)
    )

async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    country = await DatabaseManager.get_user_country(user_id) or "US"
    products = await DatabaseManager.get_user_products(user_id)
    country_flags = {"FR": "🇫🇷", "IT": "🇮🇹", "US": "🇺🇸"}
    
    message = (
        "🏠 <b>Main Menu</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🌍 <b>Country:</b> {country_flags.get(country, '')} {country}\n"
        f"📦 <b>Monitored Products:</b> {len(products)}\n\n"
        "Send me an AliExpress link to add a product:"
    )
    
    keyboard = [
        [InlineKeyboardButton("➕ Add Product", callback_data="add_product")],
        [InlineKeyboardButton("📋 My Products", callback_data="view_myproducts")],
        [InlineKeyboardButton("📊 Price History", callback_data="view_history")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="show_help")]
    ]
    
    await safe_edit_message(query, message, InlineKeyboardMarkup(keyboard))

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    
    help_text = (
        "ℹ️ <b>Help - AliExpress Price Monitor</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>🔧 How it works:</b>\n"
        "1️⃣ Select your country\n2️⃣ Send product links\n3️⃣ Get price change notifications!\n\n"
        "<b>📋 Commands:</b>\n/start - Start\n/help - Help\n/myproducts - Products\n/history - History\n\n"
        f"<b>⚡ Fast Monitoring:</b>\n• {CONCURRENT_REQUESTS} concurrent requests\n"
        f"• Updates every {MONITORING_INTERVAL//60} minutes"
    )
    
    keyboard = [
        [InlineKeyboardButton("➕ Add Product", callback_data="add_product")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
    ]
    
    if query:
        await safe_edit_message(query, help_text, InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(help_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

# ============================================================================
# PRICE MONITORING
# ============================================================================
async def check_single_product(api: AliExpressAPI, product: Dict, context: ContextTypes.DEFAULT_TYPE) -> Dict:
    start_time = time.time()
    
    try:
        user_country = await DatabaseManager.get_user_country(product['user_id']) or product['country']
        result = await api.get_product_details(product['product_id'], user_country)
        api_time = result.get('time_taken', 0)
        
        if not result.get("success"):
            await DatabaseManager.update_product_price(product['user_id'], product['product_id'], float(product['current_price']), user_country)
            print(f"   ❌ {product['product_id']}: {result.get('error')} (⏱️ {api_time:.2f}s)")
            return {'success': False, 'product_id': product['product_id'], 'error': result.get('error'), 'time_taken': time.time() - start_time}
        
        new_price = result['price']
        old_price = float(product['current_price'])
        
        await DatabaseManager.update_product_price(product['user_id'], product['product_id'], new_price, user_country, result['product_url'])
        
        price_changed = abs(new_price - old_price) > 0.01
        
        if price_changed:
            await DatabaseManager.save_price_change(product['user_id'], product['product_id'], product['title'], old_price, new_price, product['currency'])
            
            change = new_price - old_price
            change_percent = (change / old_price * 100) if old_price > 0 else 0
            print(f"   💰 {product['product_id']}: ${old_price:.2f} → ${new_price:.2f} ({change_percent:+.1f}%)")
            
            emoji = "📉 PRICE DROP!" if change < 0 else "📈 PRICE INCREASE"
            affiliate_link = await api.generate_affiliate_link(result['product_url'], user_country)
            
            notification = (
                f"{emoji}\n\n<b>{product['title'][:80]}...</b>\n\n"
                f"💵 <b>Old:</b> ${old_price:.2f}\n💵 <b>New:</b> ${new_price:.2f}\n"
                f"📊 <b>Change:</b> ${change:+.2f} ({change_percent:+.1f}%)\n"
            )
            if change < 0:
                notification += f"💰 <b>You Save:</b> ${abs(change):.2f}\n"
            notification += f"\n🔗 <code>{affiliate_link}</code>"
            
            keyboard = [
                [InlineKeyboardButton("🛒 Buy Now", url=affiliate_link)],
                [InlineKeyboardButton("📊 View History", callback_data="view_history")]
            ]
            
            try:
                if product.get('image_url'):
                    await context.bot.send_photo(chat_id=product['user_id'], photo=product['image_url'],
                                                 caption=notification, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
                else:
                    await context.bot.send_message(chat_id=product['user_id'], text=notification,
                                                   reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
            except Exception as e:
                print(f"      ❌ Notification failed: {e}")
        else:
            print(f"   ✅ {product['product_id']}: ${new_price:.2f} (no change)")
        
        return {'success': True, 'product_id': product['product_id'], 'old_price': old_price, 'new_price': new_price,
                'changed': price_changed, 'time_taken': time.time() - start_time}
    
    except Exception as e:
        print(f"   ❌ {product['product_id']}: Exception - {str(e)}")
        return {'success': False, 'product_id': product['product_id'], 'error': str(e), 'time_taken': time.time() - start_time}

async def monitor_prices(context: ContextTypes.DEFAULT_TYPE):
    print(f"\n{'='*70}\n🔍 MONITORING CYCLE - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n{'='*70}")
    
    cycle_start = time.time()
    products_to_check = await DatabaseManager.get_products_to_check(PRODUCTS_PER_CYCLE)
    
    if not products_to_check:
        print("⚠️ No products to check")
        return
    
    print(f"📦 Checking {len(products_to_check)} products...")
    api = await get_api_instance()
    
    price_changes, checked, errors = 0, 0, 0
    
    for i in range(0, len(products_to_check), CONCURRENT_REQUESTS):
        batch = products_to_check[i:i + CONCURRENT_REQUESTS]
        print(f"\n📦 Batch {i//CONCURRENT_REQUESTS + 1}: Checking {len(batch)} products...")
        
        tasks = [check_single_product(api, product, context) for product in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, Exception):
                errors += 1
            elif result.get('success'):
                checked += 1
                if result.get('changed'):
                    price_changes += 1
            else:
                errors += 1
        
        if i + CONCURRENT_REQUESTS < len(products_to_check):
            await asyncio.sleep(REQUEST_DELAY)
    
    cycle_time = time.time() - cycle_start
    print(f"\n{'─'*70}\n✅ CYCLE COMPLETE: {checked}/{len(products_to_check)} checked, {price_changes} changes, {errors} errors ({cycle_time:.2f}s)\n{'='*70}\n")

async def check_monthly_updates(context: ContextTypes.DEFAULT_TYPE):
    print(f"\n🔔 Checking monthly updates - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    users_need_reminder = await DatabaseManager.get_users_needing_reminder()
    for user_id in users_need_reminder:
        products = await DatabaseManager.get_user_products(user_id)
        if products:
            await DatabaseManager.set_update_reminder(user_id)
            try:
                keyboard = [
                    [InlineKeyboardButton("✅ Continue Monitoring", callback_data="update_continue")],
                    [InlineKeyboardButton("➕ Add Products", callback_data="add_product")],
                    [InlineKeyboardButton("🗑️ Manage Products", callback_data="manage_products")]
                ]
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🔔 <b>Monthly Product List Update</b>\n\nYou are monitoring <b>{len(products)}</b> product(s).\n\nWould you like to continue?",
                    reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML'
                )
            except Exception as e:
                print(f"   ❌ Reminder failed for {user_id}: {e}")
            await asyncio.sleep(2)
    
    users_past_deadline = await DatabaseManager.get_users_past_deadline()
    for user_id in users_past_deadline:
        try:
            await context.bot.send_message(chat_id=user_id, text="⚠️ <b>Products Removed</b>\n\nUse /start to begin again.", parse_mode='HTML')
        except: pass
        await DatabaseManager.delete_all_user_data(user_id)
        await DatabaseManager.clear_update_reminder(user_id)

# ============================================================================
# MAIN FUNCTION
# ============================================================================
def run_flask():
    """Run Flask server for health checks"""
    flask_app.run(host='0.0.0.0', port=PORT, threaded=True)

async def post_init(application: Application):
    """Initialize database after application starts"""
    await DatabaseManager.init_database()
    print("✅ Database initialized")

def main():
    print(f"\n{'='*70}")
    print("🤖 ALIEXPRESS PRICE MONITOR BOT - KOYEB EDITION")
    print(f"{'='*70}")
    print(f"📅 Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"🔌 Port: {PORT}")
    print(f"⚡ Concurrent requests: {CONCURRENT_REQUESTS}")
    print(f"⏱️  Monitoring interval: {MONITORING_INTERVAL//60} minutes")
    print(f"{'='*70}\n")
    
    # Start Flask in background thread for health checks
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print(f"✅ Health check server started on port {PORT}")
    
    # Build Telegram application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    
    # Conversation handler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECTING_COUNTRY: [CallbackQueryHandler(country_selected, pattern="^country_")],
            ENTERING_LINK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link),
                CallbackQueryHandler(add_product_prompt, pattern="^add_product$"),
                CallbackQueryHandler(view_my_products, pattern="^view_myproducts$"),
                CallbackQueryHandler(view_price_history, pattern="^view_history$"),
                CallbackQueryHandler(back_to_menu, pattern="^back_to_menu$"),
                CallbackQueryHandler(help_command, pattern="^show_help$")
            ]
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(add_product_prompt, pattern="^add_product$"))
    application.add_handler(CallbackQueryHandler(view_my_products, pattern="^view_myproducts$"))
    application.add_handler(CallbackQueryHandler(manage_products, pattern="^manage_products$"))
    application.add_handler(CallbackQueryHandler(delete_product_callback, pattern="^delete_"))
    application.add_handler(CallbackQueryHandler(view_price_history, pattern="^view_history$"))
    application.add_handler(CallbackQueryHandler(show_price_history, pattern="^history_"))
    application.add_handler(CallbackQueryHandler(handle_update_continue, pattern="^update_continue$"))
    application.add_handler(CallbackQueryHandler(back_to_menu, pattern="^back_to_menu$"))
    application.add_handler(CallbackQueryHandler(help_command, pattern="^show_help$"))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("myproducts", view_my_products))
    application.add_handler(CommandHandler("history", view_price_history))
    
    # Job queue for monitoring
    job_queue = application.job_queue
    job_queue.run_repeating(monitor_prices, interval=MONITORING_INTERVAL, first=30)
    job_queue.run_repeating(check_monthly_updates, interval=MONTHLY_CHECK_INTERVAL, first=120)
    
    print("✅ BOT STARTED SUCCESSFULLY!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n⛔ Bot stopped")
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
