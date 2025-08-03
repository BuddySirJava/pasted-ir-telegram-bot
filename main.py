import os
import re
import requests
import logging
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from typing import Optional, Dict

# Configuration
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
PASTEBINIR_API_URL = os.getenv('PASTEBINIR_API_URL', 'http://localhost:8000/api/pastes/')
LANGUAGES_API_URL = os.getenv('LANGUAGES_API_URL', 'http://localhost:8000/api/languages/')
MIN_MESSAGE_LENGTH = int(os.getenv('MIN_MESSAGE_LENGTH', '200'))  # Minimum characters to trigger paste
WEBSITE_URL = os.getenv('WEBSITE_URL', 'http://localhost:8000')
BOT_TOKEN = os.getenv('BOT_TOKEN')  # For nginx whitelist
DEBUG = os.getenv('DEBUG', 'false').lower() == 'true'  # Debug mode for verbose logging
PASTE_EXPIRATION_DAYS = int(os.getenv('PASTE_EXPIRATION_DAYS', '7'))  # Days until paste expires
RATE_LIMIT_WINDOW = int(os.getenv('RATE_LIMIT_WINDOW', '60'))  # Rate limit window in seconds
MAX_PASTES_PER_WINDOW = int(os.getenv('MAX_PASTES_PER_WINDOW', '5'))  # Max pastes per window

# Configure logging
log_level = logging.DEBUG if DEBUG else logging.INFO
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=log_level
)
logger = logging.getLogger(__name__)

# Rate limiting
user_rate_limit: Dict[int, float] = {}

def check_rate_limit(user_id: int) -> bool:
    """Check if user is rate limited"""
    current_time = time.time()
    
    # Clean old entries
    user_rate_limit.clear()
    for uid, timestamp in list(user_rate_limit.items()):
        if current_time - timestamp > RATE_LIMIT_WINDOW:
            del user_rate_limit[uid]
    
    # Check current user
    if user_id in user_rate_limit:
        return False
    
    # Add user to rate limit
    user_rate_limit[user_id] = current_time
    return True

def detect_language_from_content(content: str) -> Optional[str]:
    """Detect programming language from content using patterns and keywords"""
    if not content or not content.strip():
        return None
    
    content_lower = content.lower()
    lines = content.split('\n')
    first_line = lines[0].lower() if lines else ""
    
    # Check for shebang
    if first_line.startswith('#!'):
        if 'python' in first_line:
            return 'python'
        elif 'bash' in first_line or 'sh' in first_line:
            return 'bash'
        elif 'node' in first_line:
            return 'javascript'
    
    # Language patterns and keywords (ordered by specificity)
    language_patterns = {
        'html': {
            'patterns': [r'<!DOCTYPE html>', r'<html[^>]*>', r'<head[^>]*>', r'<body[^>]*>', r'</html>', r'\.html$'],
            'keywords': ['<!DOCTYPE', '<html', '<head', '<body', '<div', '<span', '<p', '<a', '<script', '<style'],
            'weight': 20
        },
        'css': {
            'patterns': [r'\.\w+\s*\{', r'@media\s*\(', r'@import\s+url', r'\.css$'],
            'keywords': ['{', '}', ':', ';', '@media', '@import', 'background', 'color', 'font', 'margin', 'padding', 'display', 'position'],
            'weight': 15
        },
        'javascript': {
            'patterns': [r'function\s+\w+\s*\(', r'const\s+\w+\s*=', r'let\s+\w+\s*=', r'var\s+\w+\s*=', r'console\.log', r'\.js$'],
            'keywords': ['function', 'const', 'let', 'var', 'console.log', '=>', 'async', 'await', 'export', 'import', 'return', 'if', 'else'],
            'weight': 15
        },
        'python': {
            'patterns': [r'^#!/.*python', r'import\s+\w+', r'from\s+\w+\s+import', r'def\s+\w+\s*\(', r'class\s+\w+', r'\.py$'],
            'keywords': ['def', 'class', 'import', 'from', 'if __name__', 'print', 'return', 'self', 'True', 'False', 'None', 'try', 'except'],
            'weight': 15
        },
        'php': {
            'patterns': [r'<\?php', r'\$\w+\s*=', r'echo\s+', r'function\s+\w+', r'\.php$'],
            'keywords': ['<?php', 'echo', '$', 'function', 'class', 'namespace', 'use', 'return', 'if', 'else'],
            'weight': 12
        },
        'sql': {
            'patterns': [r'SELECT\s+.*FROM', r'INSERT\s+INTO', r'UPDATE\s+\w+\s+SET', r'DELETE\s+FROM', r'CREATE\s+TABLE', r'\.sql$'],
            'keywords': ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'FROM', 'WHERE', 'JOIN', 'GROUP BY', 'ORDER BY'],
            'weight': 12
        },
        'java': {
            'patterns': [r'public\s+class\s+\w+', r'import\s+java\.', r'System\.out\.print', r'public\s+static\s+void\s+main', r'\.java$'],
            'keywords': ['public', 'class', 'import', 'System.out', 'static', 'void', 'String', 'int', 'private', 'protected'],
            'weight': 12
        },
        'cpp': {
            'patterns': [r'#include\s*<[^>]+>', r'using\s+namespace\s+std', r'std::', r'int\s+main\s*\(', r'\.cpp$'],
            'keywords': ['#include', 'using namespace', 'std::', 'cout', 'cin', 'int main', 'vector', 'string', 'class'],
            'weight': 10
        },
        'bash': {
            'patterns': [r'#!/bin/bash', r'#!/bin/sh', r'echo\s+["\']', r'if\s+\[.*\];\s+then', r'for\s+\w+\s+in', r'\.sh$'],
            'keywords': ['#!/bin', 'echo', 'if [', 'for', 'while', 'do', 'done', 'then', 'fi', 'exit'],
            'weight': 10
        },
        'json': {
            'patterns': [r'^\s*\{.*\}$', r'^\s*\[.*\]$', r'"[\w-]+"\s*:', r'\.json$'],
            'keywords': ['{', '}', '[', ']', ':', '"', 'true', 'false', 'null'],
            'weight': 8
        },
        'xml': {
            'patterns': [r'<\?xml[^>]*\?>', r'<[^>]+>.*</[^>]+>', r'<[^>]+/>', r'\.xml$'],
            'keywords': ['<?xml', '<', '>', '</', 'version=', 'encoding='],
            'weight': 8
        },
        'yaml': {
            'patterns': [r'^\s*\w+:\s*[^#\n]*$', r'^\s*-\s+\w+', r'^\s*#.*$', r'\.yml$', r'\.yaml$'],
            'keywords': [':', '-', 'version:', 'name:', 'description:', '#'],
            'weight': 8
        },
        'markdown': {
            'patterns': [r'^#\s+\w+', r'^\*\s+\w+', r'^-\s+\w+', r'\*\*[^*]+\*\*', r'__[^_]+__', r'\.md$'],
            'keywords': ['#', '*', '-', '##', '###', '**', '__', '```'],
            'weight': 6
        },
        'c': {
            'patterns': [r'#include\s*<[^>]+>', r'int\s+main\s*\(', r'printf\s*\(', r'scanf\s*\(', r'\.c$'],
            'keywords': ['#include', 'int main', 'printf', 'scanf', 'malloc', 'free', 'struct', 'return'],
            'weight': 5
        }
    }
    
    # Score each language
    scores = {}
    for lang, config in language_patterns.items():
        score = 0
        weight = config.get('weight', 1)
        
        # Check patterns
        for pattern in config['patterns']:
            if re.search(pattern, content, re.IGNORECASE):
                score += 10 * weight
        
        # Check keywords
        for keyword in config['keywords']:
            # Escape regex special characters
            escaped_keyword = re.escape(keyword)
            matches = len(re.findall(rf'\b{escaped_keyword}\b', content, re.IGNORECASE))
            score += matches * weight
        
        if score > 0:
            scores[lang] = score
    
    # Return the language with the highest score
    if scores:
        return max(scores, key=scores.get)
    
    return None

def get_language_id_by_alias(alias: str) -> Optional[int]:
    """Get language ID by alias from the API"""
    try:
        headers = {}
        if BOT_TOKEN:
            headers['X-Bot-Token'] = BOT_TOKEN
            logger.debug(f"Using bot token for API call: {BOT_TOKEN[:10]}...")
        else:
            logger.warning("No BOT_TOKEN set, API calls may be rate limited")
            
        logger.debug(f"Making request to: {LANGUAGES_API_URL}")
        logger.debug(f"Headers: {headers}")
        
        response = requests.get(LANGUAGES_API_URL, headers=headers)
        logger.debug(f"Response status: {response.status_code}")
        logger.debug(f"Response headers: {dict(response.headers)}")
        
        if response.status_code != 200:
            logger.error(f"Response content: {response.text}")
            
        response.raise_for_status()
        languages = response.json()
        
        for lang in languages:
            if lang.get('alias', '').lower() == alias.lower():
                return lang.get('id')
        
        return None
    except Exception as e:
        logger.error(f"Error fetching languages: {e}")
        return None

def create_paste(content: str, language_alias: Optional[str] = None) -> Optional[str]:
    """Create a paste using the pastebinir API"""
    try:
        # Prepare headers for bot authentication
        headers = {}
        if BOT_TOKEN:
            headers['X-Bot-Token'] = BOT_TOKEN
            logger.debug(f"Using bot token for paste creation: {BOT_TOKEN[:10]}...")
        else:
            logger.warning("No BOT_TOKEN set, paste creation may be rate limited")
            
        # Prepare the data
        data = {
            'content': content,
            'expiration': PASTE_EXPIRATION_DAYS,  # Configurable expiration
            'one_time': False
        }
        
        # If language is detected, get its ID
        if language_alias:
            lang_id = get_language_id_by_alias(language_alias)
            if lang_id:
                data['language'] = lang_id
            else:
                # Fallback to first available language
                try:
                    response = requests.get(LANGUAGES_API_URL, headers=headers)
                    response.raise_for_status()
                    languages = response.json()
                    if languages:
                        data['language'] = languages[0]['id']
                except Exception as e:
                    logger.error(f"Error getting fallback language: {e}")
        else:
            # Get first available language as fallback
            try:
                response = requests.get(LANGUAGES_API_URL, headers=headers)
                response.raise_for_status()
                languages = response.json()
                if languages:
                    data['language'] = languages[0]['id']
            except Exception as e:
                logger.error(f"Error getting fallback language: {e}")
        
        # Create the paste
        logger.debug(f"Making POST request to: {PASTEBINIR_API_URL}")
        logger.debug(f"POST headers: {headers}")
        logger.debug(f"POST data: {data}")
        
        response = requests.post(PASTEBINIR_API_URL, json=data, headers=headers)
        logger.debug(f"POST response status: {response.status_code}")
        logger.debug(f"POST response headers: {dict(response.headers)}")
        
        if response.status_code != 201:
            logger.error(f"POST response content: {response.text}")
            
        response.raise_for_status()
        
        paste_data = response.json()
        paste_id = paste_data.get('id')
        
        if paste_id:
            return f"{WEBSITE_URL}/{paste_id}"
        else:
            logger.error("No paste ID returned from API")
            return None
            
    except Exception as e:
        logger.error(f"Error creating paste: {e}")
        return None

def should_create_paste(text: str) -> bool:
    """Determine if a message should be converted to a paste"""
    # Check if message is long enough
    if len(text) < MIN_MESSAGE_LENGTH:
        return False
    
    # Check if it looks like code (has code-like patterns)
    code_indicators = [
        r'```',  # Code blocks
        r'^\s*[a-zA-Z_][a-zA-Z0-9_]*\s*[=:]\s*',  # Variable assignments
        r'^\s*(def|function|class|import|from|if|for|while|try|catch)\s+',  # Code keywords
        r'^\s*[{}()\[\]]',  # Brackets at start of lines
        r'^\s*[#/]\s*',  # Comments
        r'^\s*[a-zA-Z_][a-zA-Z0-9_]*\s*\(',  # Function calls
    ]
    
    lines = text.split('\n')
    code_line_count = 0
    
    for line in lines:
        for pattern in code_indicators:
            if re.search(pattern, line, re.IGNORECASE):
                code_line_count += 1
                break
    
    # If more than 30% of lines look like code, or message is very long (>1000 chars)
    if len(text) > 1000 or (code_line_count / len(lines)) > 0.3:
        return True
    
    # Check for language detection
    detected_lang = detect_language_from_content(text)
    if detected_lang:
        return True
    
    return False

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming messages"""
    # Only process text messages
    if not update.message or not update.message.text:
        return
    
    # Skip messages from bots
    if update.message.from_user.is_bot:
        return
    
    # Skip messages in private chats (only process group messages)
    if update.message.chat.type == 'private':
        return
    
    text = update.message.text
    user_id = update.message.from_user.id
    
    # Check if message should be converted to paste
    if should_create_paste(text):
        # Check rate limit
        if not check_rate_limit(user_id):
            logger.warning(f"User {user_id} is rate limited")
            return
        
        try:
            # Detect language
            detected_lang = detect_language_from_content(text)
            
            # Create paste
            paste_url = create_paste(text, detected_lang)
            
            if paste_url:
                # Delete the original message
                try:
                    await update.message.delete()
                except Exception as e:
                    logger.error(f"Error deleting message: {e}")
                    # If we can't delete, still send the paste link
                
                # Send the paste link
                user_mention = update.message.from_user.mention_html()
                lang_info = f" ({detected_lang})" if detected_lang else ""
                
                message_text = (
                    f"{user_mention} sent a long message{lang_info}.\n"
                    f"📋 View it here: {paste_url}"
                )
                
                await context.bot.send_message(
                    chat_id=update.message.chat.id,
                    text=message_text,
                    parse_mode='HTML',
                    disable_web_page_preview=True
                )
                
                logger.info(f"Successfully created paste for user {user_id} in chat {update.message.chat.id}")
            else:
                logger.error("Failed to create paste")
                # Send error message to user
                await context.bot.send_message(
                    chat_id=update.message.chat.id,
                    text="❌ Sorry, I couldn't create a paste for your message. Please try again later.",
                    reply_to_message_id=update.message.message_id
                )
                
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            # Send error message to user
            try:
                await context.bot.send_message(
                    chat_id=update.message.chat.id,
                    text="❌ Sorry, an error occurred while processing your message. Please try again later.",
                    reply_to_message_id=update.message.message_id
                )
            except Exception as send_error:
                logger.error(f"Error sending error message: {send_error}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command"""
    welcome_text = (
        "Welcome to official Pasted.IR bot! 📋\n\n"
        "This bot is designed to monitor your group messages.\n"
        "Functionality is simple:\n"
        "Add me to a group and make me admin and thats it!\n"
        "Now if I see a long message I will remove the message, paste it into Pasted.IR and give you the link to the text. Each text will be expired after 7 days by default for now."
    )
    
    # Get bot info for the share URL
    bot_info = await context.bot.get_me()
    bot_username = bot_info.username
    
    # Create inline keyboard with glassy button
    keyboard = [
        [InlineKeyboardButton("➕ Add to Group", url=f"https://t.me/{bot_username}?startgroup=true")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command"""
    help_text = (
        "🤖 **Pasted.IR Bot Help**\n\n"
        "**Commands:**\n"
        "• `/start` - Welcome message and add bot to group\n"
        "• `/help` - Show this help message\n"
        "• `/status` - Show bot status and configuration\n\n"
        "**How it works:**\n"
        "1. Add me to your group\n"
        "2. Make me an admin (need to delete messages)\n"
        "3. I'll automatically detect long messages (>200 chars)\n"
        "4. I'll remove the message and create a paste\n"
        "5. I'll share the paste link in the group\n\n"
        "**Features:**\n"
        "• Automatic language detection\n"
        "• 7-day expiration by default\n"
        "• Works in groups only (not private chats)\n"
        "• Supports all major programming languages\n\n"
        "**Admin permissions needed:**\n"
        "• Delete messages\n"
        "• Send messages"
    )
    
    await update.message.reply_text(
        help_text,
        parse_mode='Markdown'
    )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command - show bot status"""
    # Only allow in private chats for security
    if update.message.chat.type != 'private':
        await update.message.reply_text("❌ This command is only available in private chat for security reasons.")
        return
    
    status_text = (
        "📊 **Bot Status**\n\n"
        f"**Configuration:**\n"
        f"• API URL: `{PASTEBINIR_API_URL}`\n"
        f"• Website: `{WEBSITE_URL}`\n"
        f"• Min message length: `{MIN_MESSAGE_LENGTH}` chars\n"
        f"• Debug mode: `{'Enabled' if DEBUG else 'Disabled'}`\n"
        f"• Bot token configured: `{'Yes' if BOT_TOKEN else 'No'}`\n\n"
        f"**Bot Info:**\n"
        f"• Username: @{context.bot.username}\n"
        f"• Name: {context.bot.first_name}\n"
        f"• ID: {context.bot.id}\n\n"
        "**Status:** ✅ Running"
    )
    
    await update.message.reply_text(
        status_text,
        parse_mode='Markdown'
    )

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle errors"""
    logger.error(f"Exception while handling an update: {context.error}")

def main() -> None:
    """Start the bot"""
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN environment variable is required")
        return
    
    # Log configuration
    logger.info("=== Bot Configuration ===")
    logger.info(f"API URL: {PASTEBINIR_API_URL}")
    logger.info(f"Languages URL: {LANGUAGES_API_URL}")
    logger.info(f"Website URL: {WEBSITE_URL}")
    logger.info(f"Min message length: {MIN_MESSAGE_LENGTH}")
    logger.info(f"Debug mode: {'Enabled' if DEBUG else 'Disabled'}")
    logger.info(f"Bot token configured: {'Yes' if BOT_TOKEN else 'No'}")
    if BOT_TOKEN:
        logger.info(f"Bot token: {BOT_TOKEN[:10]}...")
    logger.info("========================")
    
    # Create the Application
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    
    # Add message handler
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Start the bot
    logger.info("Starting Telegram bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
