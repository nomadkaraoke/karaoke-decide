#!/bin/bash
# Start GCP emulators for local development

set -e

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

echo ""
echo "Emulators started!"
echo "  Firestore: localhost:8080"
echo "  GCS: localhost:4443"
echo ""
echo "Set these environment variables:"
echo "  export FIRESTORE_EMULATOR_HOST=127.0.0.1:8080"
echo "  export STORAGE_EMULATOR_HOST=http://127.0.0.1:4443"
