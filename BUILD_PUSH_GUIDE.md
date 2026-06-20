# Build & Push Docker Image Guide

## 1. Build và Push Image lên Docker Hub

### Linux/Mac:

```bash
cd python_place_reviews_job

# Make script executable
chmod +x build-and-push.sh

# Build and push with version tag
./build-and-push.sh v1.0.0

# Or build and push as latest only
./build-and-push.sh
```

### Windows:

```cmd
cd python_place_reviews_job

# Build and push with version tag
build-and-push.bat v1.0.0

# Or build and push as latest only
build-and-push.bat
```

Script sẽ:
1. Build image: `luxofons/google-maps-bot:v1.0.0`
2. Tag as latest: `luxofons/google-maps-bot:latest`
3. Hỏi confirm
4. Login Docker Hub
5. Push cả 2 tags lên

---

## 2. Manual Build & Push (nếu không dùng script)

### Step 1: Build image

```bash
cd python_place_reviews_job

# Build with version tag
docker build -t luxofons/google-maps-bot:v1.0.0 .

# Tag as latest
docker tag luxofons/google-maps-bot:v1.0.0 luxofons/google-maps-bot:latest
```

### Step 2: Login Docker Hub

```bash
docker login

# Username: luxofons
# Password: <your-password>
```

### Step 3: Push images

```bash
# Push versioned tag
docker push luxofons/google-maps-bot:v1.0.0

# Push latest tag
docker push luxofons/google-maps-bot:latest
```

---

## 3. Verify Image

```bash
# Check local images
docker images | grep google-maps-bot

# Pull from Docker Hub
docker pull luxofons/google-maps-bot:latest

# Test run
docker run --rm luxofons/google-maps-bot:latest python --version
```

---

## 4. Update Server to Use Pre-built Image

### Option A: Update docker-compose.yml trên server

```yaml
# Before (build from source)
google-maps-bot:
  build:
    context: ./python_place_reviews_job
    dockerfile: Dockerfile
  ...

# After (use pre-built image)
google-maps-bot:
  image: luxofons/google-maps-bot:latest
  ...
```

### Option B: Pull và restart trên server

```bash
# SSH vào server
ssh root@quyennv-rqnl

cd ~/app

# Pull latest image
docker pull luxofons/google-maps-bot:latest

# Restart service
docker-compose up -d google-maps-bot

# Check logs
docker-compose logs -f google-maps-bot
```

---

## 5. Multi-Architecture Build (Optional)

Nếu muốn support nhiều platform (amd64, arm64):

```bash
# Create buildx builder
docker buildx create --name multiarch --use

# Build and push multi-arch
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t luxofons/google-maps-bot:v1.0.0 \
  -t luxofons/google-maps-bot:latest \
  --push \
  .
```

---

## 6. Version Management

### Semantic Versioning:
- `v1.0.0` - Major release
- `v1.1.0` - Minor update
- `v1.0.1` - Patch/bugfix

### Tags:
```bash
# Latest stable
luxofons/google-maps-bot:latest

# Specific version
luxofons/google-maps-bot:v1.0.0

# Dev/test version
luxofons/google-maps-bot:dev
```

### Example workflow:

```bash
# Development
./build-and-push.sh dev

# Testing
docker pull luxofons/google-maps-bot:dev
# Test...

# Release
./build-and-push.sh v1.0.0

# Update production
docker pull luxofons/google-maps-bot:latest
docker-compose up -d google-maps-bot
```

---

## 7. CI/CD with GitHub Actions (Optional)

Create `.github/workflows/docker-build.yml`:

```yaml
name: Build and Push Docker Image

on:
  push:
    tags:
      - 'v*'
  workflow_dispatch:

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v2
      
      - name: Login to Docker Hub
        uses: docker/login-action@v2
        with:
          username: ${{ secrets.DOCKER_USERNAME }}
          password: ${{ secrets.DOCKER_PASSWORD }}
      
      - name: Extract version
        id: meta
        run: echo "version=${GITHUB_REF#refs/tags/}" >> $GITHUB_OUTPUT
      
      - name: Build and push
        uses: docker/build-push-action@v4
        with:
          context: ./python_place_reviews_job
          push: true
          tags: |
            luxofons/google-maps-bot:${{ steps.meta.outputs.version }}
            luxofons/google-maps-bot:latest
```

---

## 8. Image Size Optimization

Current image: ~1.5GB (Python + Chrome)

### Tips to reduce size:
1. Use multi-stage build
2. Clean up apt cache
3. Remove unnecessary files
4. Use alpine base (if compatible)

### Multi-stage Dockerfile example:

```dockerfile
# Stage 1: Builder
FROM python:3.11-slim as builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# Stage 2: Runtime
FROM python:3.11-slim
# Install Chrome
RUN apt-get update && apt-get install -y \
    google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /root/.local /root/.local
COPY *.py .

ENV PATH=/root/.local/bin:$PATH
CMD ["python", "telegram_bot.py"]
```

---

## 9. Security Best Practices

1. **Don't hardcode secrets in Dockerfile**
   - Use environment variables
   - Use Docker secrets

2. **Scan for vulnerabilities**
   ```bash
   docker scan luxofons/google-maps-bot:latest
   ```

3. **Use specific base image versions**
   ```dockerfile
   FROM python:3.11.8-slim  # instead of python:3.11-slim
   ```

4. **Run as non-root user**
   ```dockerfile
   RUN useradd -m botuser
   USER botuser
   ```

---

## 10. Troubleshooting

### Build fails:

```bash
# Clear build cache
docker builder prune -a

# Build with no cache
docker build --no-cache -t luxofons/google-maps-bot:latest .
```

### Push fails:

```bash
# Re-login
docker logout
docker login

# Check network
ping hub.docker.com
```

### Image too large:

```bash
# Check image layers
docker history luxofons/google-maps-bot:latest

# Use dive tool to analyze
dive luxofons/google-maps-bot:latest
```

---

## Quick Reference

```bash
# Build
docker build -t luxofons/google-maps-bot:v1.0.0 .

# Tag
docker tag luxofons/google-maps-bot:v1.0.0 luxofons/google-maps-bot:latest

# Login
docker login

# Push
docker push luxofons/google-maps-bot:v1.0.0
docker push luxofons/google-maps-bot:latest

# Pull on server
docker pull luxofons/google-maps-bot:latest

# Update
docker-compose pull google-maps-bot
docker-compose up -d google-maps-bot
```
