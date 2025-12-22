#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AliExpress Price Monitor Bot - Render.com Compatible Version
Fixed Event Loop Issue
"""

import os
import re
import sys
import time
import asyncio
import hashlib
import hmac
import logging
import traceback

# Setup logging FIRST
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

logger.info("="*60)
logger.info("🚀 STARTING ALIEXPRESS PRICE MONITOR BOT")
logger.info(f"Python version: {sys.version}")
logger.info(f"Working directory: {os.getcwd()}")
logger.info("="*60)

# Check imports
try:
    import aiohttp
    import ssl
    import certifi
    from openpyxl import Workbook, load_workbook
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
    from telegram.error import BadRequest, Conflict, NetworkError, TimedOut
    logger.info("✅ All imports successful")
except ImportError as e:
    logger.error(f"❌ Import error: {e}")
    sys.exit(1)

from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

# Reduce noise
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)

# ============================================================================
# CONFIGURATION
# ============================================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8354835888:AAF_F1KR40K6nmI_RwkDPwUa74L__CNuY3s")
ALIEXPRESS_APP_KEY = os.getenv("ALIEXPRESS_APP_KEY", "519492")
ALIEXPRESS_APP_SECRET = os.getenv("ALIEXPRESS_APP_SECRET", "R2Zl1pe2p47dFFjXz30546XTwu4JcFlk")
ALIEXPRESS_TRACKING_ID = os.getenv("ALIEXPRESS_TRACKING_ID", "hadef")

if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_TOKEN_HERE":
    logger.error("❌ TELEGRAM_BOT_TOKEN is not set!")
    sys.exit(1)

logger.info(f"✅ Bot token: {TELEGRAM_BOT_TOKEN[:15]}...")

# File paths
DATA_DIR = "/tmp/bot_data" if os.path.exists("/tmp") else os.path.dirname(os.path.abspath(__file__))
os.makedirs(DATA_DIR, exist_ok=True)
logger.info(f"📁 Data directory: {DATA_DIR}")

USERS_FILE = os.path.join(DATA_DIR, "users.xlsx")
PRODUCTS_FILE = os.path.join(DATA_DIR, "products.xlsx")
PRICE_HISTORY_FILE = os.path.join(DATA_DIR, "price_history.xlsx")

# Settings
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

# States
SELECTING_COUNTRY, ENTERING_LINK = range(2)

# Global
api_instance = None

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_ssl_context():
    try:
        return ssl.create_default_context(cafile=certifi.where())
    except:
        return ssl.create_default_context()


def sync_clear_webhook():
    """Synchronously clear webhook using requests-style approach"""
    import urllib.request
    import json
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook?drop_pending_updates=true"
    
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            result = json.loads(response.read().decode())
            if result.get('ok'):
                logger.info("✅ Webhook cleared successfully")
            return result.get('ok', False)
    except Exception as e:
        logger.warning(f"⚠️ Could not clear webhook: {e}")
        return False


def sync_verify_bot():
    """Synchronously verify bot token"""
    import urllib.request
    import json
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe"
    
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            result = json.loads(response.read().decode())
            if result.get('ok'):
                bot_info = result.get('result', {})
                logger.info(f"✅ Bot verified: @{bot_info.get('username', 'unknown')}")
                return True
            else:
                logger.error(f"❌ Invalid bot token")
                return False
    except Exception as e:
        logger.error(f"❌ Could not verify bot: {e}")
        return False


# ============================================================================
# EXCEL MANAGEMENT
# ============================================================================
class ExcelManager:
    @staticmethod
    def init_excel_files():
        try:
            if not os.path.exists(USERS_FILE):
                wb = Workbook()
                ws = wb.active
                ws.title = "Users"
                ws.append(["User ID", "Username", "Country", "Date Added", "Last Update Reminder", 
                          "Update Deadline", "Needs Update Response"])
                wb.save(USERS_FILE)
                logger.info(f"✅ Created {USERS_FILE}")

            if not os.path.exists(PRODUCTS_FILE):
                wb = Workbook()
                ws = wb.active
                ws.title = "Products"
                ws.append(["User ID", "Product ID", "Product URL", "Title", "Current Price", 
                          "Original Price", "Currency", "Image URL", "Country", "Date Added", "Last Checked"])
                wb.save(PRODUCTS_FILE)
                logger.info(f"✅ Created {PRODUCTS_FILE}")
            
            if not os.path.exists(PRICE_HISTORY_FILE):
                wb = Workbook()
                ws = wb.active
                ws.title = "Price History"
                ws.append(["User ID", "Product ID", "Product Title", "Old Price", "New Price", 
                          "Change Amount", "Change Percent", "Currency", "Date"])
                wb.save(PRICE_HISTORY_FILE)
                logger.info(f"✅ Created {PRICE_HISTORY_FILE}")
            
            logger.info("✅ All Excel files ready")
        except Exception as e:
            logger.error(f"❌ Error creating Excel files: {e}")
            raise

    @staticmethod
    def save_user(user_id: int, username: str, country: str):
        try:
            wb = load_workbook(USERS_FILE)
            ws = wb.active
            
            for row in ws.iter_rows(min_row=2, max_col=7):
                if row[0].value == user_id:
                    row[2].value = country
                    wb.save(USERS_FILE)
                    return
            
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ws.append([user_id, username, country, now, "", "", "No"])
            wb.save(USERS_FILE)
        except Exception as e:
            logger.error(f"Error saving user: {e}")

    @staticmethod
    def get_user_country(user_id: int) -> Optional[str]:
        try:
            if not os.path.exists(USERS_FILE):
                return None
            wb = load_workbook(USERS_FILE)
            ws = wb.active
            for row in ws.iter_rows(min_row=2, max_col=3):
                if row[0].value == user_id:
                    return row[2].value
        except Exception as e:
            logger.error(f"Error getting user country: {e}")
        return None

    @staticmethod
    def update_user_products_country(user_id: int, new_country: str):
        try:
            if not os.path.exists(PRODUCTS_FILE):
                return 0
            wb = load_workbook(PRODUCTS_FILE)
            ws = wb.active
            updated = 0
            for row in ws.iter_rows(min_row=2):
                if row[0].value == user_id:
                    row[8].value = new_country
                    updated += 1
            wb.save(PRODUCTS_FILE)
            return updated
        except Exception as e:
            logger.error(f"Error updating products country: {e}")
            return 0

    @staticmethod
    def save_product(user_id: int, product_id: str, product_url: str, title: str, 
                    price: float, original_price: float, currency: str, image_url: str, country: str):
        try:
            wb = load_workbook(PRODUCTS_FILE)
            ws = wb.active
            
            for row in ws.iter_rows(min_row=2):
                if row[0].value == user_id and row[1].value == product_id:
                    row[2].value = product_url
                    row[4].value = price
                    row[5].value = original_price
                    row[8].value = country
                    row[10].value = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    wb.save(PRODUCTS_FILE)
                    logger.info(f"✅ Product {product_id} updated")
                    return
            
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ws.append([user_id, product_id, product_url, title, price, original_price, 
                      currency, image_url, country, now, now])
            wb.save(PRODUCTS_FILE)
            logger.info(f"✅ Product {product_id} saved")
        except Exception as e:
            logger.error(f"Error saving product: {e}")

    @staticmethod
    def get_all_products() -> List[Dict]:
        products = []
        try:
            if not os.path.exists(PRODUCTS_FILE):
                return products
            wb = load_workbook(PRODUCTS_FILE)
            ws = wb.active
            for row in ws.iter_rows(min_row=2, values_only=True):
                if row[0]:
                    products.append({
                        'user_id': row[0],
                        'product_id': row[1],
                        'product_url': row[2],
                        'title': row[3],
                        'current_price': row[4] or 0,
                        'original_price': row[5] or 0,
                        'currency': row[6] or 'USD',
                        'image_url': row[7],
                        'country': row[8] or 'US',
                        'date_added': row[9],
                        'last_checked': row[10]
                    })
        except Exception as e:
            logger.error(f"Error getting products: {e}")
        return products

    @staticmethod
    def get_products_to_check(limit: int) -> List[Dict]:
        all_products = ExcelManager.get_all_products()
        if not all_products:
            return []
        
        def get_last_checked(p):
            lc = p.get('last_checked')
            if not lc:
                return datetime.min
            try:
                return datetime.strptime(str(lc), "%Y-%m-%d %H:%M:%S")
            except:
                return datetime.min
        
        return sorted(all_products, key=get_last_checked)[:limit]

    @staticmethod
    def update_product_price(user_id: int, product_id: str, new_price: float, country: str = None, product_url: str = None):
        try:
            wb = load_workbook(PRODUCTS_FILE)
            ws = wb.active
            for row in ws.iter_rows(min_row=2):
                if row[0].value == user_id and row[1].value == product_id:
                    if product_url:
                        row[2].value = product_url
                    row[4].value = new_price
                    if country:
                        row[8].value = country
                    row[10].value = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    break
            wb.save(PRODUCTS_FILE)
        except Exception as e:
            logger.error(f"Error updating product price: {e}")

    @staticmethod
    def save_price_change(user_id: int, product_id: str, title: str, old_price: float, 
                         new_price: float, currency: str):
        try:
            if abs(new_price - old_price) < 0.01:
                return
            wb = load_workbook(PRICE_HISTORY_FILE)
            ws = wb.active
            change = new_price - old_price
            change_percent = ((new_price - old_price) / old_price * 100) if old_price > 0 else 0
            ws.append([user_id, product_id, title, old_price, new_price,
                      round(change, 2), round(change_percent, 2), currency,
                      datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
            wb.save(PRICE_HISTORY_FILE)
            logger.info(f"💰 Price change: ${old_price:.2f} → ${new_price:.2f}")
        except Exception as e:
            logger.error(f"Error saving price change: {e}")

    @staticmethod
    def get_user_products(user_id: int) -> List[Dict]:
        return [p for p in ExcelManager.get_all_products() if p['user_id'] == user_id]

    @staticmethod
    def delete_product(user_id: int, product_id: str) -> bool:
        try:
            wb = load_workbook(PRODUCTS_FILE)
            ws = wb.active
            rows_to_delete = []
            for idx, row in enumerate(ws.iter_rows(min_row=2), start=2):
                if row[0].value == user_id and row[1].value == product_id:
                    rows_to_delete.append(idx)
            for row_idx in reversed(rows_to_delete):
                ws.delete_rows(row_idx)
            wb.save(PRODUCTS_FILE)
            return True
        except Exception as e:
            logger.error(f"Error deleting product: {e}")
            return False

    @staticmethod
    def get_price_history(user_id: int, product_id: str, months: int = None) -> List[Dict]:
        history = []
        try:
            if not os.path.exists(PRICE_HISTORY_FILE):
                return history
            wb = load_workbook(PRICE_HISTORY_FILE)
            ws = wb.active
            cutoff = datetime.now() - timedelta(days=months * 30) if months else None
            for row in ws.iter_rows(min_row=2, values_only=True):
                if row[0] == user_id and row[1] == product_id:
                    try:
                        date = datetime.strptime(str(row[8]), "%Y-%m-%d %H:%M:%S")
                        if cutoff and date < cutoff:
                            continue
                        history.append({
                            'title': row[2], 'old_price': row[3], 'new_price': row[4],
                            'change_amount': row[5], 'change_percent': row[6],
                            'currency': row[7], 'date': row[8]
                        })
                    except:
                        pass
        except Exception as e:
            logger.error(f"Error getting price history: {e}")
        return sorted(history, key=lambda x: x['date'], reverse=True)

    @staticmethod
    def get_all_user_price_history(user_id: int, months: int = None) -> Dict:
        products = ExcelManager.get_user_products(user_id)
        result = {}
        for p in products:
            h = ExcelManager.get_price_history(user_id, p['product_id'], months)
            if h:
                result[p['product_id']] = {'product': p, 'history': h}
        return result


# ============================================================================
# ALIEXPRESS API
# ============================================================================
class AliExpressAPI:
    def __init__(self, app_key: str, app_secret: str, tracking_id: str):
        self.app_key = app_key
        self.app_secret = app_secret
        self.tracking_id = tracking_id
        self.api_url = "https://api-sg.aliexpress.com/sync"
        self.session = None
        self._lock = asyncio.Lock()

    async def get_session(self):
        async with self._lock:
            if self.session is None or self.session.closed:
                connector = aiohttp.TCPConnector(limit=50, ssl=get_ssl_context())
                self.session = aiohttp.ClientSession(
                    connector=connector,
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                    headers={'User-Agent': 'Mozilla/5.0'}
                )
            return self.session

    async def close_session(self):
        async with self._lock:
            if self.session and not self.session.closed:
                await self.session.close()
                self.session = None

    def generate_signature(self, params: Dict) -> str:
        params_to_sign = {k: str(v) for k, v in params.items() if k != "sign" and v}
        sorted_items = sorted(params_to_sign.items())
        canonical = "".join(f"{k}{v}" for k, v in sorted_items)
        return hmac.new(self.app_secret.encode(), canonical.encode(), hashlib.md5).hexdigest().upper()

    @staticmethod
    def extract_product_id(url: str) -> Optional[str]:
        for pattern in [r"/item/(\d+)\.html", r"/i/(\d+)\.html", r"/(\d+)\.html", r"item/(\d+)"]:
            m = re.search(pattern, url)
            if m:
                return m.group(1)
        return None

    @staticmethod
    def is_shortened_url(url: str) -> bool:
        return any(p in url.lower() for p in ["s.click.aliexpress.com", "a.aliexpress.com", "/e/_"])

    async def resolve_shortened_url(self, url: str) -> str:
        session = await self.get_session()
        try:
            async with session.head(url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                final = str(resp.url)
                if self.extract_product_id(final):
                    return final
        except:
            pass
        return url

    async def get_product_details(self, product_id: str, country: str = "US") -> Dict:
        params = {
            "app_key": self.app_key, "format": "json",
            "method": "aliexpress.affiliate.productdetail.get",
            "sign_method": "hmac", "timestamp": str(int(time.time() * 1000)),
            "v": "2.0", "tracking_id": self.tracking_id,
            "product_ids": product_id, "target_currency": "USD",
            "target_language": "EN", "country": country,
        }
        params["sign"] = self.generate_signature(params)
        
        session = await self.get_session()
        try:
            async with session.get(self.api_url, params=params) as resp:
                data = await resp.json()
                
                if "error_response" in data:
                    return {"success": False, "error": data["error_response"].get("msg", "API Error")}
                
                resp_key = next((k for k in data.keys() if k.endswith("_response")), None)
                if not resp_key:
                    return {"success": False, "error": "Invalid response"}
                
                products = data[resp_key].get("resp_result", {}).get("result", {}).get("products", {}).get("product", [])
                if not products:
                    return {"success": False, "error": "Product not found"}
                
                product = products[0] if isinstance(products, list) else products
                
                def to_float(val):
                    try:
                        return float(str(val).replace("USD", "").replace("$", "").replace(",", "").strip())
                    except:
                        return None
                
                price = to_float(product.get("target_sale_price") or product.get("sale_price"))
                if price is None:
                    return {"success": False, "error": "No price"}
                
                return {
                    "success": True,
                    "product_id": str(product.get("product_id", product_id)),
                    "title": product.get("product_title", "N/A"),
                    "price": price,
                    "original_price": to_float(product.get("target_original_price")) or price,
                    "currency": "USD",
                    "image_url": product.get("product_main_image_url", ""),
                    "product_url": f"https://www.aliexpress.com/item/{product_id}.html"
                }
        except asyncio.TimeoutError:
            return {"success": False, "error": "Timeout"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def generate_affiliate_link(self, product_url: str) -> str:
        params = {
            "app_key": self.app_key, "format": "json",
            "method": "aliexpress.affiliate.link.generate",
            "sign_method": "hmac", "timestamp": str(int(time.time() * 1000)),
            "v": "2.0", "tracking_id": self.tracking_id,
            "promotion_link_type": "0", "source_values": product_url,
        }
        params["sign"] = self.generate_signature(params)
        
        session = await self.get_session()
        try:
            async with session.post(self.api_url, data=params) as resp:
                data = await resp.json()
                links = data.get("aliexpress_affiliate_link_generate_response", {}).get(
                    "resp_result", {}).get("result", {}).get("promotion_links", {}).get("promotion_link", [])
                if links:
                    return links[0].get("promotion_link", product_url)
        except:
            pass
        return product_url


async def get_api():
    global api_instance
    if api_instance is None:
        api_instance = AliExpressAPI(ALIEXPRESS_APP_KEY, ALIEXPRESS_APP_SECRET, ALIEXPRESS_TRACKING_ID)
    return api_instance


# ============================================================================
# TELEGRAM HANDLERS
# ============================================================================

async def safe_edit(query, text, markup=None):
    try:
        await query.edit_message_text(text, reply_markup=markup, parse_mode='HTML')
    except:
        try:
            await query.message.reply_text(text, reply_markup=markup, parse_mode='HTML')
        except:
            pass


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info(f"User {user.id} started bot")
    
    keyboard = [
        [InlineKeyboardButton("🇫🇷 France", callback_data="country_FR"),
         InlineKeyboardButton("🇮🇹 Italy", callback_data="country_IT")],
        [InlineKeyboardButton("🇺🇸 United States", callback_data="country_US")]
    ]
    
    await update.message.reply_text(
        f"👋 <b>Welcome {user.first_name}!</b>\n\n"
        "🛍️ <b>AliExpress Price Monitor</b>\n\n"
        "I'll track product prices and notify you of changes!\n\n"
        "📍 <b>Select your country:</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )
    return SELECTING_COUNTRY


async def country_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    country = query.data.split("_")[1]
    user = update.effective_user
    
    ExcelManager.save_user(user.id, user.username or user.first_name, country)
    context.user_data['country'] = country
    
    flags = {"FR": "🇫🇷", "IT": "🇮🇹", "US": "🇺🇸"}
    
    keyboard = [
        [InlineKeyboardButton("➕ Add Product", callback_data="add_product")],
        [InlineKeyboardButton("📋 My Products", callback_data="view_products")],
        [InlineKeyboardButton("📊 Price History", callback_data="view_history")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="show_help")]
    ]
    
    await safe_edit(query,
        f"✅ <b>Country: {flags.get(country, '')} {country}</b>\n\n"
        "📎 Send me an AliExpress product link to monitor!",
        InlineKeyboardMarkup(keyboard))
    return ENTERING_LINK


async def add_product_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("📋 My Products", callback_data="view_products")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_menu")]
    ]
    await safe_edit(query,
        "📎 <b>Send me an AliExpress link:</b>\n\n"
        "<code>https://www.aliexpress.com/item/xxxxx.html</code>",
        InlineKeyboardMarkup(keyboard))
    return ENTERING_LINK


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return ENTERING_LINK
    
    url = update.message.text.strip()
    user_id = update.effective_user.id
    
    if "aliexpress" not in url.lower():
        await update.message.reply_text("❌ Please send a valid AliExpress link.")
        return ENTERING_LINK
    
    msg = await update.message.reply_text("⏳ Processing...")
    
    api = await get_api()
    country = ExcelManager.get_user_country(user_id) or "US"
    
    if api.is_shortened_url(url):
        url = await api.resolve_shortened_url(url)
    
    product_id = api.extract_product_id(url)
    if not product_id:
        await msg.edit_text("❌ Could not extract product ID.")
        return ENTERING_LINK
    
    result = await api.get_product_details(product_id, country)
    
    if not result.get("success"):
        await msg.edit_text(f"❌ Error: {result.get('error')}")
        return ENTERING_LINK
    
    affiliate_url = await api.generate_affiliate_link(result['product_url'])
    
    ExcelManager.save_product(
        user_id, product_id, affiliate_url, result['title'],
        result['price'], result['original_price'], 'USD',
        result['image_url'], country
    )
    
    discount = result['original_price'] - result['price']
    
    text = (
        "✅ <b>Product Added!</b>\n\n"
        f"📦 {result['title'][:60]}...\n\n"
        f"💵 Price: ${result['price']:.2f}\n"
        f"💰 Original: ${result['original_price']:.2f}\n"
    )
    if discount > 0:
        text += f"🏷️ Discount: ${discount:.2f}\n"
    text += f"\n🔗 <code>{affiliate_url}</code>"
    
    keyboard = [
        [InlineKeyboardButton("➕ Add Another", callback_data="add_product")],
        [InlineKeyboardButton("📋 My Products", callback_data="view_products")],
        [InlineKeyboardButton("🔙 Menu", callback_data="back_menu")]
    ]
    
    try:
        if result.get('image_url'):
            await update.message.reply_photo(result['image_url'], caption=text,
                reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
            await msg.delete()
        else:
            await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    except:
        await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    
    return ENTERING_LINK


async def view_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    
    user_id = update.effective_user.id
    products = ExcelManager.get_user_products(user_id)
    
    if not products:
        keyboard = [[InlineKeyboardButton("➕ Add Product", callback_data="add_product")]]
        message = "📭 No products yet!"
    else:
        text = f"📦 <b>Your Products ({len(products)}):</b>\n\n"
        for i, p in enumerate(products[:5], 1):
            text += f"{i}. {str(p['title'])[:35]}...\n   💵 ${p['current_price']:.2f}\n\n"
        message = text
        
        keyboard = [
            [InlineKeyboardButton("➕ Add Product", callback_data="add_product")],
            [InlineKeyboardButton("🗑️ Manage", callback_data="manage_products")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_menu")]
        ]
    
    if query:
        await safe_edit(query, message, InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')


async def manage_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    products = ExcelManager.get_user_products(update.effective_user.id)
    
    keyboard = []
    for p in products[:8]:
        keyboard.append([InlineKeyboardButton(
            f"❌ {str(p['title'])[:25]}... ${p['current_price']:.2f}",
            callback_data=f"del_{p['product_id']}"
        )])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="view_products")])
    
    await safe_edit(query, "🗑️ <b>Select product to delete:</b>", InlineKeyboardMarkup(keyboard))


async def delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    product_id = query.data.split("_", 1)[1]
    ExcelManager.delete_product(update.effective_user.id, product_id)
    
    keyboard = [[InlineKeyboardButton("📋 My Products", callback_data="view_products")]]
    await safe_edit(query, "✅ Product deleted!", InlineKeyboardMarkup(keyboard))


async def view_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("1M", callback_data="hist_1"),
         InlineKeyboardButton("3M", callback_data="hist_3"),
         InlineKeyboardButton("All", callback_data="hist_all")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_menu")]
    ]
    
    if query:
        await safe_edit(query, "📊 <b>Select time period:</b>", InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text("📊 <b>Select time period:</b>", 
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')


async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    period = query.data.split("_")[1]
    months = None if period == "all" else int(period)
    
    history = ExcelManager.get_all_user_price_history(update.effective_user.id, months)
    
    if not history:
        await safe_edit(query, "📊 No price changes recorded.")
        return
    
    text = f"📊 <b>Price History:</b>\n\n"
    for pid, data in list(history.items())[:5]:
        text += f"📦 {str(data['product']['title'])[:30]}...\n"
        for h in data['history'][:2]:
            emoji = "📉" if float(h['change_amount']) < 0 else "📈"
            text += f"  {emoji} ${float(h['old_price']):.2f}→${float(h['new_price']):.2f}\n"
        text += "\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="view_history")]]
    await safe_edit(query, text, InlineKeyboardMarkup(keyboard))


async def back_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    products = ExcelManager.get_user_products(update.effective_user.id)
    country = ExcelManager.get_user_country(update.effective_user.id) or "US"
    
    keyboard = [
        [InlineKeyboardButton("➕ Add Product", callback_data="add_product")],
        [InlineKeyboardButton("📋 My Products", callback_data="view_products")],
        [InlineKeyboardButton("📊 History", callback_data="view_history")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="show_help")]
    ]
    
    await safe_edit(query,
        f"🏠 <b>Main Menu</b>\n\n"
        f"🌍 Country: {country}\n"
        f"📦 Products: {len(products)}\n\n"
        "Send a link to add products!",
        InlineKeyboardMarkup(keyboard))


async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    
    text = (
        "ℹ️ <b>Help</b>\n\n"
        "1️⃣ Select country\n"
        "2️⃣ Send product links\n"
        "3️⃣ Get price notifications!\n\n"
        "<b>Commands:</b>\n"
        "/start - Start bot\n"
        "/help - Show help\n"
        "/myproducts - View products\n"
        "/history - Price history"
    )
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_menu")]]
    
    if query:
        await safe_edit(query, text, InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')


# ============================================================================
# PRICE MONITORING
# ============================================================================

async def check_product(api, product, context):
    try:
        result = await api.get_product_details(product['product_id'], product['country'])
        
        if not result.get('success'):
            return
        
        new_price = result['price']
        old_price = product['current_price'] or 0
        
        ExcelManager.update_product_price(product['user_id'], product['product_id'], 
                                          new_price, product['country'])
        
        if abs(new_price - old_price) > 0.01:
            ExcelManager.save_price_change(product['user_id'], product['product_id'],
                                           product['title'], old_price, new_price, 'USD')
            
            change = new_price - old_price
            emoji = "📉 PRICE DROP!" if change < 0 else "📈 Price Increase"
            
            text = (
                f"{emoji}\n\n"
                f"<b>{product['title'][:60]}...</b>\n\n"
                f"💵 ${old_price:.2f} → ${new_price:.2f}\n"
                f"📊 Change: ${change:+.2f}"
            )
            
            try:
                await context.bot.send_message(product['user_id'], text, parse_mode='HTML')
            except:
                pass
    except Exception as e:
        logger.error(f"Error checking product: {e}")


async def monitor_prices(context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"🔍 Monitoring cycle")
    
    products = ExcelManager.get_products_to_check(PRODUCTS_PER_CYCLE)
    if not products:
        return
    
    api = await get_api()
    
    for i in range(0, len(products), CONCURRENT_REQUESTS):
        batch = products[i:i + CONCURRENT_REQUESTS]
        await asyncio.gather(*[check_product(api, p, context) for p in batch])
        await asyncio.sleep(REQUEST_DELAY)
    
    logger.info(f"✅ Checked {len(products)} products")


# ============================================================================
# ERROR HANDLER
# ============================================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    error = context.error
    
    if isinstance(error, Conflict):
        logger.error("❌ CONFLICT: Another bot instance running!")
        return
    
    if isinstance(error, (NetworkError, TimedOut)):
        logger.warning(f"Network error: {error}")
        return
    
    logger.error(f"Error: {error}")


async def post_init(app):
    logger.info("✅ Bot initialized")


async def post_shutdown(app):
    global api_instance
    if api_instance:
        await api_instance.close_session()
    logger.info("👋 Goodbye!")


# ============================================================================
# MAIN
# ============================================================================

def main():
    logger.info("📦 Initializing...")
    
    # Initialize Excel
    try:
        ExcelManager.init_excel_files()
    except Exception as e:
        logger.error(f"Failed to init Excel: {e}")
        sys.exit(1)

    # Clear webhook SYNCHRONOUSLY (no event loop issues)
    logger.info("🔄 Verifying bot and clearing webhook...")
    if not sync_verify_bot():
        logger.error("❌ Bot token invalid!")
        sys.exit(1)
    
    sync_clear_webhook()
    
    # Wait for Telegram to release connections
    logger.info("⏳ Waiting for connections to clear...")
    time.sleep(2)

    # Build application
    logger.info("🔧 Building application...")
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    
    # Add error handler
    app.add_error_handler(error_handler)
    
    # Conversation handler
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECTING_COUNTRY: [CallbackQueryHandler(country_selected, pattern="^country_")],
            ENTERING_LINK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link),
                CallbackQueryHandler(add_product_prompt, pattern="^add_product$"),
                CallbackQueryHandler(view_products, pattern="^view_products$"),
                CallbackQueryHandler(view_history, pattern="^view_history$"),
                CallbackQueryHandler(back_menu, pattern="^back_menu$"),
                CallbackQueryHandler(show_help, pattern="^show_help$"),
            ],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True,
    )
    
    # Add handlers
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(add_product_prompt, pattern="^add_product$"))
    app.add_handler(CallbackQueryHandler(view_products, pattern="^view_products$"))
    app.add_handler(CallbackQueryHandler(manage_products, pattern="^manage_products$"))
    app.add_handler(CallbackQueryHandler(delete_product, pattern="^del_"))
    app.add_handler(CallbackQueryHandler(view_history, pattern="^view_history$"))
    app.add_handler(CallbackQueryHandler(show_history, pattern="^hist_"))
    app.add_handler(CallbackQueryHandler(back_menu, pattern="^back_menu$"))
    app.add_handler(CallbackQueryHandler(show_help, pattern="^show_help$"))
    app.add_handler(CommandHandler("help", show_help))
    app.add_handler(CommandHandler("myproducts", view_products))
    app.add_handler(CommandHandler("history", view_history))
    
    logger.info("✅ Handlers registered")

    # Setup jobs
    app.job_queue.run_repeating(monitor_prices, interval=MONITORING_INTERVAL, first=60)
    logger.info("✅ Job queue configured")

    logger.info("="*60)
    logger.info("🚀 BOT STARTING...")
    logger.info("="*60)

    # Run the bot
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )


if __name__ == '__main__':
    main()
