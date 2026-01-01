#!/bin/bash
# Setup script for Spotify Audio Analysis ETL VM
#
# This script creates a GCE VM with attached SSD, installs dependencies,
# and starts the torrent download. Run from your local machine.
#
# Usage:
#   ./scripts/setup_audio_analysis_vm.sh
#
# See docs/plans/2025-01-spotify-audio-analysis-etl.md for full details.

set -euo pipefail

# Configuration
PROJECT="nomadkaraoke"
ZONE="us-central1-a"
VM_NAME="spotify-etl-vm"
MACHINE_TYPE="e2-standard-8"  # 8 vCPU, 32GB RAM
BOOT_DISK_SIZE="50GB"
DATA_DISK_SIZE="5000GB"  # 5TB for 4TB torrent + working space

MAGNET_LINK="magnet:?xt=urn:btih:afc275bcf57137317e22e296a5ee20af8000444f&dn=annas_archive_spotify_2025_07_audio_analysis.torrent&tr=udp://tracker.opentrackr.org:1337/announce"

echo "================================================"
echo "Spotify Audio Analysis ETL - VM Setup"
echo "================================================"
echo ""
echo "This will create:"
echo "  - VM: $VM_NAME ($MACHINE_TYPE)"
echo "  - Data disk: ${DATA_DISK_SIZE} SSD"
echo "  - Region: $ZONE"
echo ""
echo "Estimated cost: ~\$0.27/hr + \$0.17/GB-month storage"
echo ""
read -p "Continue? (y/N) " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 1
fi

# Check if VM already exists
if gcloud compute instances describe "$VM_NAME" --zone="$ZONE" --project="$PROJECT" &>/dev/null; then
    echo "VM $VM_NAME already exists!"
    read -p "Delete and recreate? (y/N) " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Deleting existing VM..."
        gcloud compute instances delete "$VM_NAME" --zone="$ZONE" --project="$PROJECT" --quiet
    else
        echo "Connecting to existing VM..."
        gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT"
        exit 0
    fi
fi

echo ""
echo "Creating VM..."
gcloud compute instances create "$VM_NAME" \
    --project="$PROJECT" \
    --zone="$ZONE" \
    --machine-type="$MACHINE_TYPE" \
    --image-family=debian-12 \
    --image-project=debian-cloud \
    --boot-disk-size="$BOOT_DISK_SIZE" \
    --boot-disk-type=pd-standard \
    --create-disk=name=spotify-data,size="$DATA_DISK_SIZE",type=pd-ssd,auto-delete=yes \
    --scopes=storage-rw,bigquery

echo ""
echo "Waiting for VM to be ready..."
sleep 30

echo ""
echo "Setting up VM..."

# Create setup script to run on VM
SETUP_SCRIPT=$(cat <<'VMSETUP'
#!/bin/bash
set -euo pipefail

echo "=== VM Setup Script ==="

# Format and mount data disk
echo "Mounting data disk..."
sudo mkfs.ext4 -F /dev/sdb
sudo mkdir -p /data
sudo mount /dev/sdb /data
sudo chown $USER:$USER /data
echo '/dev/sdb /data ext4 defaults 0 0' | sudo tee -a /etc/fstab

# Install dependencies
echo "Installing dependencies..."
sudo apt-get update
sudo apt-get install -y \
    python3-pip \
    python3-venv \
    zstd \
    aria2 \
    tmux \
    htop

# Install Python packages
pip3 install --user \
    google-cloud-bigquery \
    google-cloud-storage \
    orjson \
    tqdm

# Create work directories
mkdir -p /data/output
mkdir -p /data/scripts

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Data disk mounted at /data ($(df -h /data | tail -1 | awk '{print $2}') available)"
echo ""
echo "Next steps:"
echo "1. Start torrent download:"
echo "   cd /data"
echo "   tmux new -s torrent"
echo "   aria2c --seed-time=0 --max-concurrent-downloads=16 'MAGNET_LINK'"
echo ""
echo "2. Monitor download progress:"
echo "   watch -n 60 'du -sh /data/annas_archive*'"
echo ""
echo "3. After download, run ETL:"
echo "   python3 /data/scripts/spotify_audio_analysis_etl.py"
VMSETUP
)

# Copy and run setup script on VM
echo "$SETUP_SCRIPT" | gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT" -- 'cat > /tmp/setup.sh && bash /tmp/setup.sh'

# Copy ETL script to VM
echo ""
echo "Copying ETL script to VM..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
gcloud compute scp "$SCRIPT_DIR/spotify_audio_analysis_etl.py" "$VM_NAME:/data/scripts/" --zone="$ZONE" --project="$PROJECT"

echo ""
echo "================================================"
echo "VM Setup Complete!"
echo "================================================"
echo ""
echo "Connect to VM:"
echo "  gcloud compute ssh $VM_NAME --zone=$ZONE --project=$PROJECT"
echo ""
echo "Start torrent download (in tmux for persistence):"
echo "  cd /data"
echo "  tmux new -s torrent"
echo "  aria2c --seed-time=0 --max-concurrent-downloads=16 --split=16 \\"
echo "    '$MAGNET_LINK'"
echo ""
echo "Monitor progress:"
echo "  tmux attach -t torrent"
echo "  # Or: watch -n 60 'du -sh /data/annas_archive*'"
echo ""
echo "After download completes (~24-48 hours), run ETL:"
echo "  python3 /data/scripts/spotify_audio_analysis_etl.py"
echo ""
echo "When done, delete VM:"
echo "  gcloud compute instances delete $VM_NAME --zone=$ZONE --project=$PROJECT"
echo ""
