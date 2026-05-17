#!/bin/bash
# Pull models from R2 in BACKGROUND, then immediately hand off to vastai/comfy entrypoint.
# SSH comes up right away via init.sh. Models arrive while ComfyUI is initializing.

if [ -n "$R2_ACCOUNT_ID" ] && [ -n "$R2_ACCESS_KEY_ID" ] && [ -n "$R2_SECRET_ACCESS_KEY" ]; then
    echo "[boot] Starting R2 model pull in background..."
    mkdir -p ~/.config/rclone /workspace/ComfyUI/models
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
    (
        rclone copy "r2:${BUCKET}" /workspace/ComfyUI/models \
            --transfers 8 \
            --multi-thread-streams 8 \
            --no-check-dest \
            --log-file /workspace/r2_pull.log \
            2>&1
        echo "[boot] R2 pull complete." >> /workspace/r2_pull.log
    ) &
    echo "[boot] R2 pull started in background (PID $!). SSH available now."
else
    echo "[boot] No R2 credentials — skipping model pull."
fi

# Hand off immediately — SSH starts right away
exec /opt/ai-dock/bin/init.sh "$@"
