#!/bin/bash

# Build and push Docker image to Docker Hub
# Usage: ./build-and-push.sh <version>
# Example: ./build-and-push.sh v1.0.0

set -e

# Configuration
DOCKER_USERNAME="luxofons"
IMAGE_NAME="google-maps-bot"
VERSION="${1:-latest}"

echo "======================================"
echo "Building Docker Image"
echo "======================================"
echo "Image: ${DOCKER_USERNAME}/${IMAGE_NAME}:${VERSION}"
echo ""

# Build image
echo "🔨 Building image..."
docker build -t ${DOCKER_USERNAME}/${IMAGE_NAME}:${VERSION} .

# Also tag as latest if version is specified
if [ "$VERSION" != "latest" ]; then
    echo "🏷️  Tagging as latest..."
    docker tag ${DOCKER_USERNAME}/${IMAGE_NAME}:${VERSION} ${DOCKER_USERNAME}/${IMAGE_NAME}:latest
fi

echo ""
echo "✅ Build completed!"
echo ""

# Ask for confirmation before pushing
read -p "Push to Docker Hub? (y/n) " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "======================================"
    echo "Pushing to Docker Hub"
    echo "======================================"
    
    # Login to Docker Hub (if not already logged in)
    echo "🔐 Logging in to Docker Hub..."
    docker login
    
    # Push versioned tag
    echo "📤 Pushing ${VERSION}..."
    docker push ${DOCKER_USERNAME}/${IMAGE_NAME}:${VERSION}
    
    # Push latest tag
    if [ "$VERSION" != "latest" ]; then
        echo "📤 Pushing latest..."
        docker push ${DOCKER_USERNAME}/${IMAGE_NAME}:latest
    fi
    
    echo ""
    echo "======================================"
    echo "✅ Successfully pushed!"
    echo "======================================"
    echo "Image: ${DOCKER_USERNAME}/${IMAGE_NAME}:${VERSION}"
    echo "Image: ${DOCKER_USERNAME}/${IMAGE_NAME}:latest"
    echo ""
    echo "Pull command:"
    echo "  docker pull ${DOCKER_USERNAME}/${IMAGE_NAME}:${VERSION}"
    echo ""
else
    echo "❌ Push cancelled"
fi
