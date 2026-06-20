# Deployment Guide - Telegram Bot

## 1. Cú pháp sử dụng Telegram Bot

### Commands:
- `/start` - Hiển thị hướng dẫn
- `/status` - Kiểm tra trạng thái bot

### Gửi URL:
Chỉ cần gửi Google Maps URL trực tiếp:

```
https://maps.app.goo.gl/abc123
```

Hoặc gửi nhiều URL cùng lúc:

```
https://maps.app.goo.gl/abc123
https://www.google.com/maps/place/...
https://maps.app.goo.gl/xyz456
```

Bot sẽ tự động:
1. Scrape place details + 500 reviews (newest first)
2. Upload lên API
3. Báo kết quả

---

## 2. Deploy lên Server

### Option A: Deploy trên Linux Server (Ubuntu/Debian)

#### Step 1: Cài đặt dependencies

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python 3.11+
sudo apt install python3 python3-pip python3-venv -y

# Install Chrome for Selenium
wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt install ./google-chrome-stable_current_amd64.deb -y
rm google-chrome-stable_current_amd64.deb

# Verify Chrome installation
google-chrome --version
```

#### Step 2: Setup project

```bash
# Clone/upload project
cd /opt
sudo mkdir google-maps-bot
sudo chown $USER:$USER google-maps-bot
cd google-maps-bot

# Upload files (scp hoặc git clone)
# Hoặc: scp -r python_place_reviews_job/* user@server:/opt/google-maps-bot/

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

#### Step 3: Create systemd service

```bash
sudo nano /etc/systemd/system/telegram-bot.service
```

Paste nội dung:

```ini
[Unit]
Description=Google Maps Telegram Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/google-maps-bot
Environment="PATH=/opt/google-maps-bot/venv/bin"
Environment="TELEGRAM_BOT_TOKEN=8565880065:AAFKHNi5tBhwkY5zYOaOuCWaGOuMMzHxMCE"
Environment="TELEGRAM_USER_ID=5636346689"
ExecStart=/opt/google-maps-bot/venv/bin/python telegram_bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

#### Step 4: Start service

```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable service (auto-start on boot)
sudo systemctl enable telegram-bot

# Start service
sudo systemctl start telegram-bot

# Check status
sudo systemctl status telegram-bot

# View logs
sudo journalctl -u telegram-bot -f
```

#### Step 5: Commands để quản lý

```bash
# Start bot
sudo systemctl start telegram-bot

# Stop bot
sudo systemctl stop telegram-bot

# Restart bot
sudo systemctl restart telegram-bot

# View logs real-time
sudo journalctl -u telegram-bot -f

# View last 100 lines
sudo journalctl -u telegram-bot -n 100
```

---

### Option B: Deploy bằng Docker

#### Step 1: Create Dockerfile

```dockerfile
FROM python:3.11-slim

# Install Chrome
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    && wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Environment variables (override in docker-compose)
ENV TELEGRAM_BOT_TOKEN=8565880065:AAFKHNi5tBhwkY5zYOaOuCWaGOuMMzHxMCE
ENV TELEGRAM_USER_ID=5636346689

CMD ["python", "telegram_bot.py"]
```

#### Step 2: Create docker-compose.yml

```yaml
version: '3.8'

services:
  telegram-bot:
    build: .
    container_name: google-maps-bot
    restart: unless-stopped
    environment:
      - TELEGRAM_BOT_TOKEN=8565880065:AAFKHNi5tBhwkY5zYOaOuCWaGOuMMzHxMCE
      - TELEGRAM_USER_ID=5636346689
    volumes:
      - ./bot_scraper.log:/app/bot_scraper.log
    mem_limit: 2g
    cpus: 1.0
```

#### Step 3: Run with Docker

```bash
# Build and start
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down

# Restart
docker-compose restart
```

---

### Option C: Deploy trên Windows Server

#### Step 1: Install Python & Chrome

1. Install Python 3.11+ từ python.org
2. Install Google Chrome
3. Install Git (optional)

#### Step 2: Setup

```powershell
# Open PowerShell as Administrator
cd C:\
mkdir google-maps-bot
cd google-maps-bot

# Upload files

# Create virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

#### Step 3: Create Windows Service (using NSSM)

```powershell
# Download NSSM
# https://nssm.cc/download

# Install service
nssm install TelegramBot "C:\google-maps-bot\venv\Scripts\python.exe" "C:\google-maps-bot\telegram_bot.py"

# Set environment variables
nssm set TelegramBot AppEnvironmentExtra TELEGRAM_BOT_TOKEN=8565880065:AAFKHNi5tBhwkY5zYOaOuCWaGOuMMzHxMCE
nssm set TelegramBot AppEnvironmentExtra TELEGRAM_USER_ID=5636346689

# Start service
nssm start TelegramBot

# Check status
nssm status TelegramBot
```

---

## 3. Configuration

### Environment Variables:

- `TELEGRAM_BOT_TOKEN` - Bot token (default: hardcoded)
- `TELEGRAM_USER_ID` - Allowed user ID (default: 5636346689)

### Security:

- Chỉ user với ID `5636346689` mới được sử dụng bot
- Các user khác sẽ nhận thông báo: "❌ Bạn không có quyền sử dụng bot này."

---

## 4. Monitoring & Troubleshooting

### Check bot status:

```bash
# Systemd
sudo systemctl status telegram-bot

# Docker
docker-compose ps
```

### View logs:

```bash
# Systemd
sudo journalctl -u telegram-bot -f

# Docker
docker-compose logs -f

# Log file
tail -f bot_scraper.log
```

### Common issues:

1. **Chrome not found:**
   ```bash
   google-chrome --version
   # If not found, reinstall Chrome
   ```

2. **Bot not responding:**
   - Check if service is running
   - Check logs for errors
   - Verify bot token is correct

3. **Permission denied:**
   - Check user ID matches
   - Verify file permissions

---

## 5. Test Bot

1. Mở Telegram
2. Tìm bot (tên bot tạo từ @BotFather)
3. Gửi `/start`
4. Gửi Google Maps URL:
   ```
   https://maps.app.goo.gl/nVCmXvyKDxtfD76J7
   ```
5. Bot sẽ:
   - ⏳ Đang xử lý URL...
   - 📡 Đang lấy thông tin từ Google Maps...
   - ✅ Đã lấy thông tin: Mì Quảng Bà Dinh
   - ☁️ Đang upload lên API...
   - ✅ Upload thành công!

---

## 6. Update Code

### Systemd:
```bash
cd /opt/google-maps-bot
git pull  # or upload new files
sudo systemctl restart telegram-bot
```

### Docker:
```bash
cd /path/to/project
git pull  # or upload new files
docker-compose down
docker-compose up -d --build
```

---

## 7. Backup

### Important files:
- `telegram_bot.py`
- `run_job.py`
- `upload_to_api.py`
- `requirements.txt`
- `bot_scraper.log` (logs)

### Backup command:
```bash
tar -czf backup-$(date +%Y%m%d).tar.gz \
  telegram_bot.py \
  run_job.py \
  upload_to_api.py \
  requirements.txt \
  bot_scraper.log
```
