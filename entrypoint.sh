#!/bin/bash
# Inject Vast.ai SSH public key so we can SSH in
mkdir -p /root/.ssh
chmod 700 /root/.ssh
if [ -n "$PUBLIC_KEY" ]; then
    echo "$PUBLIC_KEY" >> /root/.ssh/authorized_keys
fi
chmod 600 /root/.ssh/authorized_keys 2>/dev/null || true

# Pull models from Cloudflare R2 if credentials are set (fast — ~30 sec at 10Gbps)
if [ -n "$R2_ACCOUNT_ID" ] && [ -n "$R2_ACCESS_KEY_ID" ] && [ -n "$R2_SECRET_ACCESS_KEY" ]; then
    echo "[boot] Pulling models from R2..."
    mkdir -p ~/.config/rclone
    cat > ~/.config/rclone/rclone.conf << EOF
[r2]
type = s3
provider = Cloudflare
access_key_id = ${R2_ACCESS_KEY_ID}
secret_access_key = ${R2_SECRET_ACCESS_KEY}
endpoint = https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com
acl = private
EOF
    BUCKET="${R2_BUCKET:-wan-22}"
    rclone copy "r2:${BUCKET}" /workspace/ComfyUI/models --transfers 8 --checksum 2>&1 | tail -3
    echo "[boot] Models ready."
else
    echo "[boot] No R2 credentials — skipping model pull."
fi

exec /usr/bin/supervisord -n -c /etc/supervisor/conf.d/supervisord.conf
