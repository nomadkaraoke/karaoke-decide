#!/bin/bash
# Setup script for verifying and seeding Spotify torrents
# Run this AFTER the GCS restore is complete
#
# This script will:
# 1. Add both torrents to transmission
# 2. Point them at the existing data from GCS
# 3. Verify data integrity (check all pieces)
# 4. Download any missing pieces
# 5. Start seeding

set -e

# Magnet links
AUDIO_ANALYSIS_MAGNET="magnet:?xt=urn:btih:afc275bcf57137317e22e296a5ee20af8000444f&dn=annas_archive_spotify_2025_07_audio_analysis.torrent&tr=udp://tracker.opentrackr.org:1337/announce"
METADATA_MAGNET="magnet:?xt=urn:btih:4cc9ac59f807dc6bdf95f52ffc86f44272a361a7&dn=annas_archive_spotify_2025_07_metadata.torrent&tr=udp://tracker.opentrackr.org:1337/announce"

# Install transmission if not already installed
sudo apt-get update
sudo apt-get install -y transmission-daemon transmission-cli

# Stop transmission to configure
sudo systemctl stop transmission-daemon 2>/dev/null || true

# Create transmission config
sudo mkdir -p /etc/transmission-daemon
sudo mkdir -p /var/lib/transmission-daemon/downloads
sudo mkdir -p /var/lib/transmission-daemon/incomplete

# Configure transmission for seeding
cat << 'EOF' | sudo tee /etc/transmission-daemon/settings.json
{
    "alt-speed-down": 50,
    "alt-speed-enabled": false,
    "alt-speed-time-begin": 540,
    "alt-speed-time-day": 127,
    "alt-speed-time-enabled": false,
    "alt-speed-time-end": 1020,
    "alt-speed-up": 50,
    "bind-address-ipv4": "0.0.0.0",
    "bind-address-ipv6": "::",
    "blocklist-enabled": false,
    "cache-size-mb": 4,
    "dht-enabled": true,
    "download-dir": "/data/torrents",
    "download-queue-enabled": true,
    "download-queue-size": 5,
    "encryption": 1,
    "idle-seeding-limit": 0,
    "idle-seeding-limit-enabled": false,
    "incomplete-dir": "/var/lib/transmission-daemon/incomplete",
    "incomplete-dir-enabled": false,
    "lpd-enabled": false,
    "max-peers-global": 200,
    "message-level": 1,
    "peer-congestion-algorithm": "",
    "peer-id-ttl-hours": 6,
    "peer-limit-global": 200,
    "peer-limit-per-torrent": 50,
    "peer-port": 51413,
    "peer-port-random-high": 65535,
    "peer-port-random-low": 49152,
    "peer-port-random-on-start": false,
    "peer-socket-tos": "default",
    "pex-enabled": true,
    "port-forwarding-enabled": true,
    "preallocation": 1,
    "prefetch-enabled": true,
    "queue-stalled-enabled": true,
    "queue-stalled-minutes": 30,
    "ratio-limit": 0,
    "ratio-limit-enabled": false,
    "rename-partial-files": true,
    "rpc-authentication-required": false,
    "rpc-bind-address": "0.0.0.0",
    "rpc-enabled": true,
    "rpc-host-whitelist": "",
    "rpc-host-whitelist-enabled": false,
    "rpc-password": "",
    "rpc-port": 9091,
    "rpc-url": "/transmission/",
    "rpc-username": "",
    "rpc-whitelist": "127.0.0.1,::1",
    "rpc-whitelist-enabled": false,
    "scrape-paused-torrents-enabled": true,
    "script-torrent-done-enabled": false,
    "seed-queue-enabled": false,
    "seed-queue-size": 10,
    "speed-limit-down": 100,
    "speed-limit-down-enabled": false,
    "speed-limit-up": 0,
    "speed-limit-up-enabled": false,
    "start-added-torrents": true,
    "trash-original-torrent-files": false,
    "umask": 18,
    "upload-slots-per-torrent": 14,
    "utp-enabled": true
}
EOF

# Set proper ownership
sudo chown -R debian-transmission:debian-transmission /etc/transmission-daemon
sudo chown -R debian-transmission:debian-transmission /var/lib/transmission-daemon

# Allow transmission to access /data
sudo usermod -a -G $(stat -c '%G' /data) debian-transmission
sudo chmod g+rx /data
sudo chmod -R g+rx /data/torrents

# Start transmission
sudo systemctl start transmission-daemon
sleep 5

echo "=== Transmission daemon started ==="
echo ""

# Add both torrents - transmission will verify existing data
echo "Adding audio analysis torrent..."
transmission-remote -a "$AUDIO_ANALYSIS_MAGNET"
sleep 2

echo "Adding metadata torrent..."
transmission-remote -a "$METADATA_MAGNET"
sleep 2

echo ""
echo "=== Torrents added ==="
echo ""
echo "Transmission will now:"
echo "  1. Fetch torrent metadata from peers"
echo "  2. Verify existing data against torrent pieces"
echo "  3. Download any missing pieces"
echo "  4. Start seeding once verified"
echo ""
echo "Monitor verification progress with:"
echo "  watch -n 5 'transmission-remote -l'"
echo ""
echo "Detailed status:"
echo "  transmission-remote -t all -i"
echo ""
echo "Web interface:"
echo "  http://localhost:9091"
echo ""
echo "=== IMPORTANT ==="
echo "Watch the 'Done' percentage. If it shows less than 100%,"
echo "transmission is downloading missing pieces from the swarm."
echo ""
echo "Once both show 100% and status is 'Seeding', the data is verified complete."
echo ""
echo "Keep this VM running for 1 month to seed the torrents."
echo "Estimated cost: ~\$200/month for e2-standard-8 with 5TB SSD"
