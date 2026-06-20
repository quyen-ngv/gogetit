#!/usr/bin/env python3
"""
Telegram bot for scraping Google Maps places and uploading to API.

Usage:
1. Set TELEGRAM_BOT_TOKEN environment variable
2. Run: python telegram_bot.py
3. Send Google Maps URL to bot
4. Bot will scrape and upload automatically
"""

import logging
import os
import re
import sys
import time
from datetime import datetime

try:
    from telegram import Update
    from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
except ImportError:
    print("ERROR: python-telegram-bot not installed")
    print("Install with: pip install python-telegram-bot")
    sys.exit(1)

from run_job import scrape_place, setup_logging
from upload_to_api import format_place_for_api, upload_place as api_upload_place

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# API configuration
API_URL = "https://onestudy.id.vn/goroute/v1/api/places/import"
API_HEADERS = {
    "accept": "*/*",
    "content-type": "application/json",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def is_google_maps_url(text: str) -> bool:
    """Check if text contains a Google Maps URL."""
    patterns = [
        r'https?://(?:www\.)?google\.com/maps',
        r'https?://maps\.google\.com',
        r'https?://goo\.gl/maps',
        r'https?://maps\.app\.goo\.gl',
    ]
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def extract_urls(text: str) -> list[str]:
    """Extract all Google Maps URLs from text."""
    urls = []
    # Find all URLs in text
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    found_urls = re.findall(url_pattern, text)
    
    for url in found_urls:
        if is_google_maps_url(url):
            urls.append(url)
    
    return urls


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message."""
    await update.message.reply_text(
        "👋 Xin chào! Tôi là bot import địa điểm Google Maps.\n\n"
        "📍 Cách dùng:\n"
        "• Gửi URL Google Maps cho tôi\n"
        "• Tôi sẽ lấy thông tin và reviews\n"
        "• Tự động upload lên API\n"
        "• Báo kết quả\n\n"
        "Ví dụ: https://maps.app.goo.gl/abc123\n\n"
        "Lệnh:\n"
        "/start - Hiển thị hướng dẫn\n"
        "/status - Kiểm tra trạng thái bot"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check bot status."""
    await update.message.reply_text(
        "✅ Bot đang hoạt động\n"
        f"🕐 Server time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages with Google Maps URLs."""
    text = update.message.text
    user = update.message.from_user
    
    # Check if user is allowed
    allowed_user_id = int(os.getenv("TELEGRAM_USER_ID", "5636346689"))
    if user.id != allowed_user_id:
        logger.warning(f"Unauthorized user attempted to use bot: {user.id} ({user.username})")
        await update.message.reply_text(
            "❌ Bạn không có quyền sử dụng bot này."
        )
        return
    
    logger.info(f"Received message from {user.username or user.id}: {text[:100]}")
    
    # Extract URLs
    urls = extract_urls(text)
    
    if not urls:
        await update.message.reply_text(
            "❌ Không tìm thấy Google Maps URL\n\n"
            "Vui lòng gửi link dạng:\n"
            "• https://maps.app.goo.gl/...\n"
            "• https://www.google.com/maps/place/..."
        )
        return
    
    # Process each URL
    for idx, url in enumerate(urls, start=1):
        if len(urls) > 1:
            prefix = f"[{idx}/{len(urls)}] "
        else:
            prefix = ""
        
        await update.message.reply_text(f"{prefix}⏳ Đang xử lý URL...\n{url}")
        
        try:
            # Step 1: Scrape place
            status_msg = await update.message.reply_text(
                f"{prefix}📡 Đang lấy thông tin từ Google Maps..."
            )
            
            place_data = scrape_place(
                url,
                headless=True,
                max_reviews=500,  # Get up to 500 reviews (sorted by newest)
                max_scrolls=100  # Increase scrolls to get more reviews
            )
            
            if place_data.get("status") == "failed":
                await status_msg.edit_text(
                    f"{prefix}❌ Lỗi khi scrape:\n{place_data.get('error', 'Unknown error')}"
                )
                continue
            
            place_title = place_data.get("title", "Unknown")
            review_count = place_data.get("reviews_count_output", 0)
            
            await status_msg.edit_text(
                f"{prefix}✅ Đã lấy thông tin:\n"
                f"📍 {place_title}\n"
                f"⭐ {place_data.get('reviewRating', 0)}/5\n"
                f"💬 {review_count} reviews"
            )
            
            # Step 2: Upload to API
            upload_msg = await update.message.reply_text(
                f"{prefix}☁️ Đang upload lên API..."
            )
            
            try:
                api_data = format_place_for_api(place_data)
                success = api_upload_place(API_URL, api_data, API_HEADERS)
                
                if success:
                    await upload_msg.edit_text(
                        f"{prefix}✅ Upload thành công!\n\n"
                        f"📍 {place_title}\n"
                        f"🆔 {place_data.get('placeId', 'N/A')}\n"
                        f"⭐ {place_data.get('reviewRating', 0)}/5 ({review_count} reviews)"
                    )
                else:
                    await upload_msg.edit_text(
                        f"{prefix}❌ Upload thất bại\n"
                        f"Place: {place_title}"
                    )
            except Exception as e:
                await upload_msg.edit_text(
                    f"{prefix}❌ Lỗi upload: {str(e)[:100]}"
                )
            
            # Delay between URLs
            if idx < len(urls):
                time.sleep(2)
                
        except Exception as e:
            logger.error(f"Error processing {url}: {e}", exc_info=True)
            await update.message.reply_text(
                f"{prefix}❌ Lỗi: {str(e)[:200]}"
            )


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors."""
    logger.error(f"Update {update} caused error {context.error}")
    if update and update.message:
        await update.message.reply_text(
            "❌ Đã xảy ra lỗi. Vui lòng thử lại sau."
        )


def main():
    """Run the bot."""
    # Get token from environment or use default
    token = os.getenv("TELEGRAM_BOT_TOKEN", "8565880065:AAFKHNi5tBhwkY5zYOaOuCWaGOuMMzHxMCE")
    
    # Allowed user ID (for security - only this user can use bot)
    allowed_user_id = int(os.getenv("TELEGRAM_USER_ID", "5636346689"))
    
    logger.info("Starting Telegram bot...")
    logger.info(f"Allowed user ID: {allowed_user_id}")
    
    # Setup run_job logging to file only
    setup_logging("WARNING", "bot_scraper.log")
    
    logger.info("Starting Telegram bot...")
    
    # Create application
    app = Application.builder().token(token).build()
    
    # Register handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)
    
    # Start bot
    logger.info("Bot started. Press Ctrl+C to stop.")
    print("✅ Bot đang chạy...")
    print("📱 Gửi Google Maps URL cho bot để bắt đầu!")
    
    app.run_polling(allowed_updates=Update.ALL_TYPES)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
