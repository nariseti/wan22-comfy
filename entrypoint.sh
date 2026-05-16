#!/bin/bash
# Inject Vast.ai SSH public key so we can SSH in
mkdir -p /root/.ssh
chmod 700 /root/.ssh
if [ -n "$PUBLIC_KEY" ]; then
    echo "$PUBLIC_KEY" >> /root/.ssh/authorized_keys
fi
chmod 600 /root/.ssh/authorized_keys 2>/dev/null || true

exec /usr/bin/supervisord -n -c /etc/supervisor/conf.d/supervisord.conf
