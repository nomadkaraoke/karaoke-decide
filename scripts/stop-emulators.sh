#!/bin/bash
# Stop GCP emulators

echo "Stopping emulators..."

docker stop firestore-emulator 2>/dev/null || true
docker rm firestore-emulator 2>/dev/null || true

docker stop gcs-emulator 2>/dev/null || true
docker rm gcs-emulator 2>/dev/null || true

echo "Emulators stopped."
