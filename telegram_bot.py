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

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID
from place_pipeline import scrape_and_import
from place_urls import extract_urls
from run_job import setup_logging
from run_job import DEFAULT_MAX_REVIEWS

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def _requested_max_reviews(text: str) -> int:
    match = re.search(r"(?:max[_\s-]?reviews?|reviews?|/r)\s*[:=]?\s*(\d{1,3})", text or "", re.IGNORECASE)
    if not match:
        return DEFAULT_MAX_REVIEWS
    value = int(match.group(1))
    return max(1, min(value, DEFAULT_MAX_REVIEWS))

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
    if user.id != TELEGRAM_USER_ID:
        logger.warning(f"Unauthorized user attempted to use bot: {user.id} ({user.username})")
        await update.message.reply_text(
            "❌ Bạn không có quyền sử dụng bot này."
        )
        return
    
    logger.info(f"Received message from {user.username or user.id}: {text[:100]}")
    max_reviews = _requested_max_reviews(text)
    
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
            status_msg = await update.message.reply_text(
                f"{prefix}📡 Đang lấy thông tin từ Google Maps..."
            )

            result = scrape_and_import(url, headless=True, max_reviews=max_reviews, max_scrolls=100)

            if result.scrape_error:
                await status_msg.edit_text(
                    f"{prefix}❌ Lỗi khi scrape:\n{result.scrape_error}"
                )
                continue

            place = result.place
            place_title = place.title if place else "Unknown"
            review_count = place.reviews_scraped if place else 0
            review_rating = place.review_rating if place else 0
            place_id = place.place_id if place else "N/A"

            await status_msg.edit_text(
                f"{prefix}✅ Đã lấy thông tin:\n"
                f"📍 {place_title}\n"
                f"⭐ {review_rating}/5\n"
                f"💬 {review_count} reviews"
            )

            upload_msg = await update.message.reply_text(
                f"{prefix}☁️ Đang upload lên API..."
            )

            if result.success:
                await upload_msg.edit_text(
                    f"{prefix}✅ Upload thành công!\n\n"
                    f"📍 {place_title}\n"
                    f"🆔 {place_id}\n"
                    f"⭐ {review_rating}/5 ({review_count} reviews)"
                )
            else:
                upload_error = result.upload_error or "Unknown error"
                await upload_msg.edit_text(
                    f"{prefix}❌ Upload thất bại\n"
                    f"Place: {place_title}\n"
                    f"Lỗi: {upload_error[:100]}"
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
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set")
        return 1

    logger.info("Starting Telegram bot...")
    logger.info(f"Allowed user ID: {TELEGRAM_USER_ID}")
    
    # Setup run_job logging to file only
    setup_logging("WARNING", "bot_scraper.log")
    
    logger.info("Starting Telegram bot...")
    
    # Create application
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
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
