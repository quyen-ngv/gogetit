# Docker Setup - Telegram Bot

## Quick Start (Standalone)

Nếu chỉ chạy bot riêng:

```bash
cd python_place_reviews_job

# Build and run
docker-compose up -d

# View logs
docker-compose logs -f telegram-bot

# Stop
docker-compose down
```

---

## Setup trong hệ thống hiện tại

### Bước 1: Copy files vào server

```bash
# Trên local machine
scp -r python_place_reviews_job root@quyennv-rqnl:~/app/

# Hoặc dùng git
cd ~/app
git pull  # nếu code trong git
```

### Bước 2: Update docker-compose.yml chính

Thêm service này vào file `~/app/docker-compose.yml`:

```yaml
  # Google Maps Telegram Bot
  google-maps-bot:
    build:
      context: ./python_place_reviews_job
      dockerfile: Dockerfile
    container_name: google-maps-telegram-bot
    restart: unless-stopped
    environment:
      - TELEGRAM_BOT_TOKEN=8565880065:AAFKHNi5tBhwkY5zYOaOuCWaGOuMMzHxMCE
      - TELEGRAM_USER_ID=5636346689
    volumes:
      - ./python_place_reviews_job/logs:/app/logs
      - ./python_place_reviews_job/output:/app/output
    mem_limit: 2g
    cpus: 1.5
    networks:
      - vocaberry-network
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
    healthcheck:
      test: ["CMD", "python", "-c", "import sys; sys.exit(0)"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
```

**Vị trí đặt:** Sau service `goroute-app`, trước `cambridge-dictionary`.

### Bước 3: Build và start bot

```bash
cd ~/app

# Build image
docker-compose build google-maps-bot

# Start bot
docker-compose up -d google-maps-bot

# Check status
docker-compose ps google-maps-bot

# View logs
docker-compose logs -f google-maps-bot
```

---

## Hoặc dùng file docker-compose.full.yml

Nếu muốn thay thế toàn bộ:

```bash
cd ~/app

# Backup file cũ
cp docker-compose.yml docker-compose.yml.backup

# Copy file mới
cp python_place_reviews_job/docker-compose.full.yml docker-compose.yml

# Restart tất cả services
docker-compose down
docker-compose up -d
```

---

## Commands quản lý

### Start/Stop/Restart

```bash
# Start bot
docker-compose up -d google-maps-bot

# Stop bot
docker-compose stop google-maps-bot

# Restart bot
docker-compose restart google-maps-bot

# Remove bot
docker-compose down google-maps-bot
```

### View logs

```bash
# Real-time logs
docker-compose logs -f google-maps-bot

# Last 100 lines
docker-compose logs --tail=100 google-maps-bot

# Logs since 1 hour ago
docker-compose logs --since 1h google-maps-bot
```

### Debug

```bash
# Enter container
docker exec -it google-maps-telegram-bot bash

# Check Chrome
docker exec google-maps-telegram-bot google-chrome --version

# Check Python version
docker exec google-maps-telegram-bot python --version

# Test bot manually
docker exec -it google-maps-telegram-bot python telegram_bot.py
```

### Update code

```bash
# Upload new code to server
scp run_job.py root@quyennv-rqnl:~/app/python_place_reviews_job/
scp upload_to_api.py root@quyennv-rqnl:~/app/python_place_reviews_job/
scp telegram_bot.py root@quyennv-rqnl:~/app/python_place_reviews_job/

# Rebuild and restart
docker-compose build google-maps-bot
docker-compose up -d google-maps-bot
```

---

## Monitoring

### Check container health

```bash
# Status
docker ps | grep google-maps

# Stats (CPU, memory)
docker stats google-maps-telegram-bot

# Inspect
docker inspect google-maps-telegram-bot
```

### Check logs location

```bash
# Container logs
docker-compose logs google-maps-bot

# Bot scraper logs
cat ~/app/python_place_reviews_job/logs/bot_scraper.log

# Output files
ls -lh ~/app/python_place_reviews_job/output/
```

---

## Troubleshooting

### Bot không start

```bash
# Check logs
docker-compose logs google-maps-bot

# Common issues:
# 1. Chrome not found
docker exec google-maps-telegram-bot which google-chrome

# 2. Python dependencies
docker exec google-maps-telegram-bot pip list

# 3. Network issues
docker exec google-maps-telegram-bot ping google.com
```

### Bot không respond

```bash
# Check if bot is running
docker ps | grep google-maps

# Check logs for errors
docker-compose logs --tail=50 google-maps-bot

# Restart bot
docker-compose restart google-maps-bot
```

### Memory issues

```bash
# Check memory usage
docker stats google-maps-telegram-bot

# Increase memory limit in docker-compose.yml:
# mem_limit: 4g  # increase to 4GB
```

---

## Security Notes

1. **Bot Token**: Hardcoded trong docker-compose.yml, có thể override bằng env variable
2. **User ID**: Chỉ user `5636346689` được phép dùng bot
3. **Network**: Bot chạy trong `vocaberry-network`, có thể gọi API goroute-app
4. **Logs**: Giới hạn 10MB x 3 files để tránh đầy disk

---

## Resource Usage

- **CPU**: ~50-80% khi scraping
- **Memory**: ~500MB-1.5GB tùy số reviews
- **Disk**: ~100MB image, logs rotate 30MB
- **Network**: ~10-50MB per place (download images)

---

## Backup & Restore

### Backup

```bash
# Backup logs
tar -czf bot-logs-$(date +%Y%m%d).tar.gz ~/app/python_place_reviews_job/logs/

# Backup output
tar -czf bot-output-$(date +%Y%m%d).tar.gz ~/app/python_place_reviews_job/output/
```

### Restore

```bash
# Restore logs
tar -xzf bot-logs-20240608.tar.gz -C ~/app/python_place_reviews_job/

# Restore output
tar -xzf bot-output-20240608.tar.gz -C ~/app/python_place_reviews_job/
```

---

## Production Checklist

- [ ] Bot token đúng
- [ ] User ID đúng
- [ ] Chrome installed trong container
- [ ] Python dependencies installed
- [ ] Network connectivity OK
- [ ] API endpoint accessible
- [ ] Logs directory có quyền write
- [ ] Memory limit hợp lý (2GB+)
- [ ] Disk space đủ (5GB+)
- [ ] Backup script setup

---

## Test Bot

1. **Start bot:**
   ```bash
   docker-compose up -d google-maps-bot
   ```

2. **Check logs:**
   ```bash
   docker-compose logs -f google-maps-bot
   ```
   
   Bạn sẽ thấy:
   ```
   ✅ Bot đang chạy...
   📱 Gửi Google Maps URL cho bot để bắt đầu!
   ```

3. **Mở Telegram**, tìm bot của bạn

4. **Gửi `/start`**

5. **Gửi URL:**
   ```
   https://maps.app.goo.gl/nVCmXvyKDxtfD76J7
   ```

6. **Đợi kết quả** (~2-5 phút cho 500 reviews)

---

## Uninstall

```bash
# Stop and remove container
docker-compose down google-maps-bot

# Remove image
docker rmi google-maps-telegram-bot

# Remove volumes
rm -rf ~/app/python_place_reviews_job/logs
rm -rf ~/app/python_place_reviews_job/output

# Remove code
rm -rf ~/app/python_place_reviews_job
```
