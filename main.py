#!/usr/bin/env python3
# bot.py - Telegram Bot with Step-by-Step Contact Adding

import os
import sys
import logging
import re
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

# === CONFIGURATION ===
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# === .env FILE HANDLING ===
def setup_environment():
    """Load environment variables"""
    env_path = find_dotenv()
    
    if not env_path:
        logger.error("❌ No .env file found!")
        sys.exit(1)
    
    load_dotenv(env_path)
    
    config = {
        'token': os.getenv('TELEGRAM_BOT_TOKEN'),
        'supabase_url': os.getenv('SUPABASE_URL'),
        'supabase_key': os.getenv('SUPABASE_KEY'),
        'table_name': os.getenv('TABLE_NAME', 'contacts'),
        'debug': os.getenv('DEBUG', 'false').lower() == 'true'
    }
    
    if not config['token'] or not config['supabase_url'] or not config['supabase_key']:
        logger.error("Missing environment variables!")
        sys.exit(1)
    
    logger.info(f"✅ Configuration loaded")
    return config

# === HELPER FUNCTIONS ===
def is_phone_number(text):
    """Check if text looks like a phone number"""
    cleaned = re.sub(r'[\s\-\(\)\.]', '', text)
    if cleaned.startswith('+'):
        cleaned = cleaned[1:]
    return len(cleaned) >= 6 and cleaned.isdigit()

def clean_phone_number(text):
    """Clean and normalize phone number"""
    return ''.join(filter(lambda x: x.isdigit() or x == '+', text))

def format_contact(contact):
    """Format a contact for display"""
    name = contact.get('name', 'Unknown')
    phone = contact.get('phone_number', 'N/A')
    return f"👤 **Name:** {name}\n📞 **Number:** `{phone}`"

# === MAIN BOT CODE ===
def main():
    """Main function to run the bot"""
    
    config = setup_environment()
    
    # Import libraries
    try:
        from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
        from telegram.ext import (
            Application, CommandHandler, MessageHandler,
            filters, ContextTypes, ConversationHandler
        )
        from supabase import create_client
    except ImportError as e:
        print(f"❌ Missing dependency: {e}")
        print("Run: pip install python-telegram-bot supabase python-dotenv")
        sys.exit(1)
    
    # Initialize Supabase
    supabase = create_client(config['supabase_url'], config['supabase_key'])
    logger.info("✅ Supabase initialized")
    
    # === CONVERSATION STATES ===
    WAITING_NAME, WAITING_PHONE = range(2)
    
    # === MAIN MENU KEYBOARD ===
    def get_main_keyboard():
        """Return main menu keyboard"""
        keyboard = [
            [KeyboardButton("➕ Add Contact"), KeyboardButton("🔍 Search")],
            [KeyboardButton("📋 List All"), KeyboardButton("❓ Help")]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    def get_cancel_keyboard():
        """Return cancel keyboard"""
        keyboard = [[KeyboardButton("❌ Cancel")]]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    # === COMMAND HANDLERS ===
    
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Welcome message"""
        welcome_msg = """
🤖 **Contact Manager Bot**

━━━━━━━━━━━━━━━━━━━━━━━━━━━
**Choose an option below or:**

📌 **Quick Save:**
Send `Name: Number`
Example: `John: +1234567890`

🔍 **Quick Search:**
Send a name or phone number
━━━━━━━━━━━━━━━━━━━━━━━━━━━
        """
        await update.message.reply_text(
            welcome_msg, 
            parse_mode='Markdown',
            reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END
    
    async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Help message"""
        help_msg = """
📖 **HOW TO USE**

━━━━━━━━━━━━━━━━━━━━━━━━━━━
**➕ ADD CONTACT:**
• Tap "➕ Add Contact" button
• Or send: `Name: Number`

**🔍 SEARCH:**
• Tap "🔍 Search" then enter name/number
• Or just send a name or phone number

**📋 LIST:**
• Tap "📋 List All" to see all contacts

**🗑️ DELETE:**
• Send: `/delete +1234567890`
━━━━━━━━━━━━━━━━━━━━━━━━━━━

**COMMANDS:**
/start - Main menu
/add - Add new contact
/list - List contacts
/search - Search contacts
/delete - Delete contact
/help - This help
/cancel - Cancel operation
        """
        await update.message.reply_text(
            help_msg, 
            parse_mode='Markdown',
            reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END
    
    # === ADD CONTACT CONVERSATION ===
    
    async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start adding a contact - ask for name"""
        await update.message.reply_text(
            "➕ **ADD NEW CONTACT**\n\n"
            "📝 Please enter the **contact name:**\n\n"
            "_Example: John Doe_",
            parse_mode='Markdown',
            reply_markup=get_cancel_keyboard()
        )
        return WAITING_NAME
    
    async def received_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Received name, now ask for phone"""
        name = update.message.text.strip()
        
        # Check for cancel
        if name == "❌ Cancel":
            await update.message.reply_text(
                "❌ Cancelled.",
                reply_markup=get_main_keyboard()
            )
            return ConversationHandler.END
        
        # Validate name
        if len(name) < 2:
            await update.message.reply_text(
                "❌ Name is too short. Please enter at least 2 characters:",
                reply_markup=get_cancel_keyboard()
            )
            return WAITING_NAME
        
        if ":" in name:
            await update.message.reply_text(
                "❌ Name cannot contain ':'\nPlease enter a valid name:",
                reply_markup=get_cancel_keyboard()
            )
            return WAITING_NAME
        
        # Store name and ask for phone
        context.user_data['new_contact_name'] = name
        
        # Create keyboard with phone share button
        keyboard = [
            [KeyboardButton("📱 Share Phone", request_contact=True)],
            [KeyboardButton("❌ Cancel")]
        ]
        
        await update.message.reply_text(
            f"✅ Name: **{name}**\n\n"
            f"📞 Now enter the **phone number:**\n\n"
            f"_Example: +1234567890_\n\n"
            f"Or tap 📱 to share a contact from your phone",
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        return WAITING_PHONE
    
    async def received_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Received phone number, save contact"""
        
        # Check if contact was shared
        if update.message.contact:
            phone_number = update.message.contact.phone_number
            # If contact has a name and we don't have one yet, use it
            if not context.user_data.get('new_contact_name'):
                contact = update.message.contact
                name = f"{contact.first_name or ''} {contact.last_name or ''}".strip()
                context.user_data['new_contact_name'] = name or "Unknown"
        else:
            phone_input = update.message.text.strip()
            
            # Check for cancel
            if phone_input == "❌ Cancel":
                context.user_data.pop('new_contact_name', None)
                await update.message.reply_text(
                    "❌ Cancelled.",
                    reply_markup=get_main_keyboard()
                )
                return ConversationHandler.END
            
            phone_number = clean_phone_number(phone_input)
        
        # Validate phone
        if not phone_number or len(phone_number) < 6:
            await update.message.reply_text(
                "❌ Invalid phone number!\n"
                "Please enter at least 6 digits:",
                reply_markup=get_cancel_keyboard()
            )
            return WAITING_PHONE
        
        # Get stored name
        name = context.user_data.get('new_contact_name', 'Unknown')
        user_id = update.effective_user.id
        
        try:
            # Check if number exists
            existing = supabase.table(config['table_name'])\
                .select("*")\
                .eq("phone_number", phone_number)\
                .execute()
            
            if existing.data:
                old_name = existing.data[0].get('name')
                await update.message.reply_text(
                    f"⚠️ **Number Already Exists!**\n\n"
                    f"📞 `{phone_number}`\n"
                    f"👤 Saved as: **{old_name}**\n\n"
                    f"Delete first with:\n`/delete {phone_number}`",
                    parse_mode='Markdown',
                    reply_markup=get_main_keyboard()
                )
                context.user_data.pop('new_contact_name', None)
                return ConversationHandler.END
            
            # Save contact
            data = {
                "name": name,
                "phone_number": phone_number,
                "telegram_user_id": user_id
            }
            
            supabase.table(config['table_name']).insert(data).execute()
            
            await update.message.reply_text(
                f"✅ **CONTACT SAVED!**\n\n"
                f"👤 **Name:** {name}\n"
                f"📞 **Number:** `{phone_number}`\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🔍 Search by name: `{name.split()[0]}`\n"
                f"🔍 Search by phone: `{phone_number}`",
                parse_mode='Markdown',
                reply_markup=get_main_keyboard()
            )
            
            logger.info(f"Saved: {name} - {phone_number}")
            
        except Exception as e:
            logger.error(f"Error saving: {e}")
            await update.message.reply_text(
                "❌ Error saving contact. Please try again.",
                reply_markup=get_main_keyboard()
            )
        
        # Clear stored data
        context.user_data.pop('new_contact_name', None)
        return ConversationHandler.END
    
    async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Cancel operation"""
        context.user_data.pop('new_contact_name', None)
        await update.message.reply_text(
            "❌ Operation cancelled.",
            reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END
    
    # === SEARCH HANDLERS ===
    
    async def search_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start search"""
        await update.message.reply_text(
            "🔍 **SEARCH CONTACT**\n\n"
            "Enter a **name** or **phone number**:\n\n"
            "_Example: John_\n"
            "_Example: +1234567890_",
            parse_mode='Markdown',
            reply_markup=get_cancel_keyboard()
        )
        context.user_data['searching'] = True
        return ConversationHandler.END
    
    async def list_contacts(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """List all contacts"""
        try:
            response = supabase.table(config['table_name'])\
                .select("*")\
                .order("name")\
                .execute()
            
            if not response.data:
                await update.message.reply_text(
                    "📭 **No contacts saved yet.**\n\n"
                    "Tap ➕ Add Contact to add one!",
                    parse_mode='Markdown',
                    reply_markup=get_main_keyboard()
                )
                return
            
            contacts = response.data
            total = len(contacts)
            
            message = f"📇 **ALL CONTACTS ({total}):**\n\n"
            
            for i, contact in enumerate(contacts[:30], 1):
                name = contact.get('name', 'Unknown')
                phone = contact.get('phone_number', 'N/A')
                message += f"{i}. **{name}**\n   📞 `{phone}`\n\n"
            
            if total > 30:
                message += f"_...and {total - 30} more_\n"
                message += "_Use 🔍 Search to find specific contacts_"
            
            await update.message.reply_text(
                message,
                parse_mode='Markdown',
                reply_markup=get_main_keyboard()
            )
            
        except Exception as e:
            logger.error(f"Error listing: {e}")
            await update.message.reply_text(
                "❌ Error loading contacts.",
                reply_markup=get_main_keyboard()
            )
    
    async def delete_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Delete a contact"""
        if not context.args:
            await update.message.reply_text(
                "🗑️ **DELETE CONTACT**\n\n"
                "Usage: `/delete <phone_number>`\n"
                "Example: `/delete +1234567890`",
                parse_mode='Markdown',
                reply_markup=get_main_keyboard()
            )
            return
        
        phone = clean_phone_number(' '.join(context.args))
        
        try:
            response = supabase.table(config['table_name'])\
                .select("*")\
                .ilike("phone_number", f"%{phone}%")\
                .execute()
            
            if not response.data:
                await update.message.reply_text(
                    f"❌ No contact found: `{phone}`",
                    parse_mode='Markdown',
                    reply_markup=get_main_keyboard()
                )
                return
            
            if len(response.data) > 1:
                msg = "⚠️ **Multiple matches found:**\n\n"
                for c in response.data:
                    msg += f"• `{c.get('phone_number')}` - {c.get('name')}\n"
                msg += "\nPlease be more specific."
                await update.message.reply_text(msg, parse_mode='Markdown')
                return
            
            contact = response.data[0]
            supabase.table(config['table_name'])\
                .delete()\
                .eq("id", contact.get('id'))\
                .execute()
            
            await update.message.reply_text(
                f"✅ **DELETED!**\n\n"
                f"👤 {contact.get('name')}\n"
                f"📞 `{contact.get('phone_number')}`",
                parse_mode='Markdown',
                reply_markup=get_main_keyboard()
            )
            
        except Exception as e:
            logger.error(f"Error deleting: {e}")
            await update.message.reply_text("❌ Error deleting contact.")
    
    # === MESSAGE HANDLER ===
    
    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle all text messages"""
        text = update.message.text.strip()
        user_id = update.effective_user.id
        
        logger.info(f"Message from {user_id}: {text[:50]}")
        
        # Handle menu buttons
        if text == "➕ Add Contact":
            return await add_start(update, context)
        
        if text == "🔍 Search":
            return await search_start(update, context)
        
        if text == "📋 List All":
            return await list_contacts(update, context)
        
        if text == "❓ Help":
            return await help_command(update, context)
        
        if text == "❌ Cancel":
            context.user_data.pop('searching', None)
            await update.message.reply_text(
                "✅ Back to main menu",
                reply_markup=get_main_keyboard()
            )
            return
        
        # === QUICK SAVE (Name: Number format) ===
        if ":" in text:
            try:
                parts = text.split(":", 1)
                name = parts[0].strip()
                phone = clean_phone_number(parts[1])
                
                if not name or len(name) < 2:
                    await update.message.reply_text(
                        "❌ Name is too short!",
                        reply_markup=get_main_keyboard()
                    )
                    return
                
                if not phone or len(phone) < 6:
                    await update.message.reply_text(
                        "❌ Invalid phone number!",
                        reply_markup=get_main_keyboard()
                    )
                    return
                
                # Check existing
                existing = supabase.table(config['table_name'])\
                    .select("*")\
                    .eq("phone_number", phone)\
                    .execute()
                
                if existing.data:
                    await update.message.reply_text(
                        f"⚠️ Number `{phone}` already exists as **{existing.data[0].get('name')}**",
                        parse_mode='Markdown',
                        reply_markup=get_main_keyboard()
                    )
                    return
                
                # Save
                supabase.table(config['table_name']).insert({
                    "name": name,
                    "phone_number": phone,
                    "telegram_user_id": user_id
                }).execute()
                
                await update.message.reply_text(
                    f"✅ **SAVED!**\n\n"
                    f"👤 **{name}**\n"
                    f"📞 `{phone}`",
                    parse_mode='Markdown',
                    reply_markup=get_main_keyboard()
                )
                return
                
            except Exception as e:
                logger.error(f"Quick save error: {e}")
                await update.message.reply_text(
                    "❌ Error saving. Use format:\n`Name: +1234567890`",
                    parse_mode='Markdown'
                )
                return
        
        # === SEARCH ===
        try:
            results = []
            
            # Check if it's a phone number
            if is_phone_number(text):
                phone = clean_phone_number(text)
                response = supabase.table(config['table_name'])\
                    .select("*")\
                    .ilike("phone_number", f"%{phone}%")\
                    .execute()
                results = response.data
            else:
                # Search by name
                response = supabase.table(config['table_name'])\
                    .select("*")\
                    .ilike("name", f"%{text}%")\
                    .order("name")\
                    .execute()
                results = response.data
            
            context.user_data.pop('searching', None)
            
            if not results:
                await update.message.reply_text(
                    f"🔍 **No results for:** `{text}`\n\n"
                    f"💡 To save as contact:\n"
                    f"• Tap ➕ Add Contact\n"
                    f"• Or send: `{text}: +PhoneNumber`",
                    parse_mode='Markdown',
                    reply_markup=get_main_keyboard()
                )
                return
            
            if len(results) == 1:
                c = results[0]
                await update.message.reply_text(
                    f"🔍 **CONTACT FOUND!**\n\n"
                    f"👤 **Name:** {c.get('name')}\n"
                    f"📞 **Number:** `{c.get('phone_number')}`",
                    parse_mode='Markdown',
                    reply_markup=get_main_keyboard()
                )
            else:
                msg = f"🔍 **Found {len(results)} contacts:**\n\n"
                for i, c in enumerate(results[:10], 1):
                    msg += f"{i}. **{c.get('name')}**\n   📞 `{c.get('phone_number')}`\n\n"
                
                if len(results) > 10:
                    msg += f"_...and {len(results) - 10} more_"
                
                await update.message.reply_text(
                    msg,
                    parse_mode='Markdown',
                    reply_markup=get_main_keyboard()
                )
                
        except Exception as e:
            logger.error(f"Search error: {e}")
            await update.message.reply_text(
                "❌ Error searching.",
                reply_markup=get_main_keyboard()
            )
    
    async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle shared contacts"""
        contact = update.message.contact
        phone = contact.phone_number
        name = f"{contact.first_name or ''} {contact.last_name or ''}".strip() or "Unknown"
        
        try:
            # Check if exists
            existing = supabase.table(config['table_name'])\
                .select("*")\
                .eq("phone_number", phone)\
                .execute()
            
            if existing.data:
                await update.message.reply_text(
                    f"⚠️ Contact already exists:\n\n"
                    f"👤 **{existing.data[0].get('name')}**\n"
                    f"📞 `{phone}`",
                    parse_mode='Markdown',
                    reply_markup=get_main_keyboard()
                )
                return
            
            # Save
            supabase.table(config['table_name']).insert({
                "name": name,
                "phone_number": phone,
                "telegram_user_id": update.effective_user.id
            }).execute()
            
            await update.message.reply_text(
                f"✅ **CONTACT SAVED!**\n\n"
                f"👤 **{name}**\n"
                f"📞 `{phone}`",
                parse_mode='Markdown',
                reply_markup=get_main_keyboard()
            )
            
        except Exception as e:
            logger.error(f"Contact save error: {e}")
            await update.message.reply_text("❌ Error saving contact.")
    
    async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors"""
        logger.error(f"Error: {context.error}")
    
    # === BUILD AND RUN ===
    try:
        app = Application.builder().token(config['token']).build()
        
        # Conversation handler for adding contacts
        add_conv = ConversationHandler(
            entry_points=[
                CommandHandler("add", add_start),
                MessageHandler(filters.Regex("^➕ Add Contact$"), add_start)
            ],
            states={
                WAITING_NAME: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, received_name)
                ],
                WAITING_PHONE: [
                    MessageHandler(filters.CONTACT, received_phone),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, received_phone)
                ]
            },
            fallbacks=[
                CommandHandler("cancel", cancel),
                MessageHandler(filters.Regex("^❌ Cancel$"), cancel)
            ]
        )
        
        # Add handlers
        app.add_handler(add_conv)
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("help", help_command))
        app.add_handler(CommandHandler("list", list_contacts))
        app.add_handler(CommandHandler("delete", delete_contact))
        app.add_handler(CommandHandler("cancel", cancel))
        app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        app.add_error_handler(error_handler)
        
        print("\n" + "="*50)
        print("🤖 CONTACT MANAGER BOT")
        print("="*50)
        print("✨ Features:")
        print("   • ➕ Add Contact (step by step)")
        print("   • 🔍 Search by name or phone")
        print("   • 📋 List all contacts")
        print("   • 📱 Share contacts from phone")
        print("   • Quick save: Name: Number")
        print("\nPress Ctrl+C to stop")
        print("="*50 + "\n")
        
        app.run_polling(drop_pending_updates=True)
        
    except Exception as e:
        logger.error(f"Bot failed: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
