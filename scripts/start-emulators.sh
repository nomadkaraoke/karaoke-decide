#!/bin/bash
# Start GCP emulators for local development

set -e

# Check Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "Docker is not running. Please start Docker and try again."
    exit 1
fi

# Clean up any existing containers
docker rm -f firestore-emulator 2>/dev/null || true
docker rm -f gcs-emulator 2>/dev/null || true

echo "Starting Firestore emulator..."
docker run -d \
  --name firestore-emulator \
  -p 8080:8080 \
  gcr.io/google.com/cloudsdktool/google-cloud-cli:emulators \
  gcloud emulators firestore start --host-port=0.0.0.0:8080

echo "Starting GCS emulator..."
docker run -d \
  --name gcs-emulator \
  -p 4443:4443 \
  fsouza/fake-gcs-server -scheme http -port 4443

# Wait for Firestore (up to 30s)
echo "Waiting for emulators..."
for i in $(seq 1 30); do
    if curl -s http://127.0.0.1:8080 > /dev/null 2>&1; then
        echo "Firestore ready (${i}s)"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "Firestore failed to start after 30s"
        docker logs firestore-emulator 2>&1 | tail -20
        exit 1
    fi
    sleep 1
done

# Wait for GCS (up to 10s)
for i in $(seq 1 10); do
    if curl -s http://127.0.0.1:4443/storage/v1/b > /dev/null 2>&1; then
        echo "GCS ready (${i}s)"
        break
    fi
    if [ "$i" -eq 10 ]; then
        echo "GCS failed to start after 10s"
        docker logs gcs-emulator 2>&1 | tail -20
        exit 1
    fi
    sleep 1
done

echo ""
echo "Emulators started!"
echo "  Firestore: localhost:8080"
echo "  GCS: localhost:4443"
echo ""
echo "Set these environment variables:"
echo "  export FIRESTORE_EMULATOR_HOST=127.0.0.1:8080"
echo "  export STORAGE_EMULATOR_HOST=http://127.0.0.1:4443"
