#!/usr/bin/env python3
"""
auto_render.py — Fully autonomous Wan 2.2 render loop.

Run once from your terminal:
    py auto_render.py
    py auto_render.py --start 1 --end 40 --steps 16

It will:
  1. Find cheapest GPU (interruptible preferred, on-demand fallback)
  2. Rent with nariseti/wan22-comfy + R2 env vars
  3. Wait for boot + R2 model pull (~4-6 min)
  4. Submit renders via ComfyUI API
  5. Download completed clips
  6. Destroy instance
  7. Loop back to step 1 if more scenes remain or outbid

No manual steps after launch. Bad machines are blacklisted automatically.
"""

import argparse, json, subprocess, sys, time, requests
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Config ────────────────────────────────────────────────────────────────────

CONFIG_FILE  = Path(r"C:\Users\chanu\OneDrive\Keys\vastai_config.json")
BAD_IPS_FILE = Path(__file__).parent / "bad_machines.json"

DOCKER_IMAGE = "nariseti/wan22-comfy:latest"
COMFY_PATH   = "/workspace/ComfyUI"
VASTAI       = "https://console.vast.ai/api/v0"

# Minimum specs for a render instance
MIN_VRAM_MB  = 24_000
MIN_DISK_GB  = 60

# Boot timeout: 12 min is plenty; if not up by then, machine is bad
BOOT_TIMEOUT = 720

# Permanently bad IPs (add more as discovered)
STATIC_BAD_IPS = {
    "162.120.84.101",
    "162.120.84.113",
    "130.61.31.221",
    "67.71.100.20",   # Quebec RTX 3090 — never booted
}

# ── Helpers ───────────────────────────────────────────────────────────────────

_t0 = time.time()

def log(msg):
    elapsed = time.time() - _t0
    m, s = divmod(int(elapsed), 60)
    print(f"[{time.strftime('%H:%M:%S')} +{m:02d}:{s:02d}] {msg}", flush=True)


def load_config():
    return json.loads(CONFIG_FILE.read_text())


def load_bad_ips():
    if BAD_IPS_FILE.exists():
        return set(json.loads(BAD_IPS_FILE.read_text()))
    return set()


def save_bad_ip(ip):
    ips = load_bad_ips() | {ip}
    BAD_IPS_FILE.write_text(json.dumps(sorted(ips), indent=2))
    log(f"  Blacklisted {ip} permanently.")


def bad_ips():
    return STATIC_BAD_IPS | load_bad_ips()


def api_get(token, path, params=None):
    r = requests.get(f"{VASTAI}/{path}",
                     headers={"Authorization": f"Bearer {token}"},
                     params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def api_put(token, path, body):
    r = requests.put(f"{VASTAI}/{path}",
                     headers={"Authorization": f"Bearer {token}"},
                     json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def api_delete(token, path):
    r = requests.delete(f"{VASTAI}/{path}",
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=30)
    r.raise_for_status()
    return r.json()


def get_instance(token, instance_id):
    data = api_get(token, "instances/", params={"owner": "me"})
    return next((i for i in data.get("instances", []) if i["id"] == instance_id), None)


# ── Step 1: Find cheapest GPU ─────────────────────────────────────────────────

def find_offer(token):
    base = {
        "gpu_ram":          {"gte": MIN_VRAM_MB},
        "disk_space":       {"gte": MIN_DISK_GB},
        "rentable":         {"eq": True},
        "rented":           {"eq": False},
        "verified":         {"eq": True},          # verified hosts only
        "reliability2":     {"gte": 0.90},         # 90%+ uptime reliability
        "direct_port_count":{"gte": 1},            # must support direct SSH (no proxy)
        "order":            [["dph_total", "asc"]],
        "limit":            50,
    }
    blocked = bad_ips()

    for bid, label in [(True, "interruptible"), (False, "on-demand")]:
        q = {**base, "is_bid": {"eq": bid}}
        log(f"Searching {label} GPUs ({MIN_VRAM_MB//1000}GB+ VRAM, {MIN_DISK_GB}GB+ disk)...")
        data = api_get(token, "bundles/", params={"q": json.dumps(q)})
        for o in data.get("offers", []):
            ip = o.get("public_ipaddr") or o.get("inet_ipaddr", "")
            if ip in blocked:
                continue
            log(f"  ✓ {label}: {o['num_gpus']}x {o['gpu_name']} "
                f"${o['dph_total']:.3f}/hr | {int(o['disk_space'])}GB disk "
                f"| {o.get('geolocation','')} | offer {o['id']}")
            return o

    log("ERROR: No offers found. Check Vast.ai manually.")
    sys.exit(1)


# ── Step 2: Rent instance ─────────────────────────────────────────────────────

def rent(token, offer_id, r2):
    env = {
        "R2_ACCOUNT_ID":        r2["account_id"],
        "R2_ACCESS_KEY_ID":     r2["access_key_id"],
        "R2_SECRET_ACCESS_KEY": r2["secret_access_key"],
        "R2_BUCKET":            r2.get("bucket", "wan-22"),
    }
    body = {
        "client_id": "me",
        "image":     DOCKER_IMAGE,
        "disk":      100.0,
        "label":     "wan22-auto",
        "onstart":   "",
        "runtype":   "jupyter_direc ssh_direc ssh_proxy",
        "env":       env,
    }
    try:
        result = api_put(token, f"asks/{offer_id}/", body)
    except Exception as e:
        log(f"  Rent API error: {e}")
        return None
    iid = result.get("new_contract")
    if not iid:
        log(f"  Rent failed: {result}")
        return None
    log(f"  Rented! Instance {iid}")
    return iid


# ── Step 3: Wait for running ──────────────────────────────────────────────────

def wait_running(token, instance_id, timeout=BOOT_TIMEOUT):
    log(f"Waiting for instance {instance_id} to boot (up to {timeout//60} min)...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        inst = get_instance(token, instance_id)
        if not inst:
            log("  Instance disappeared — may have been outbid.")
            return None
        status = inst.get("actual_status", "")
        if status == "running":
            log(f"  Running! ({inst.get('gpu_name')} in {inst.get('geolocation','')})")
            return inst
        if status in ("error", "exited", "deleted"):
            log(f"  Instance failed with status: {status}")
            return None
        print(".", end="", flush=True)
        time.sleep(15)
    print()
    log(f"  Timed out after {timeout//60} min — bad machine.")
    return None


# ── Step 4: Get direct SSH ────────────────────────────────────────────────────

def get_ssh(token, instance_id, retries=8):
    """Get direct SSH host:port. Tries vastai ssh-url first, falls back to API fields."""
    log("Getting direct SSH details...")
    # Try vastai ssh-url (gives real direct port)
    for _ in range(retries):
        try:
            r = subprocess.run(
                ["vastai", "ssh-url", str(instance_id)],
                capture_output=True, text=True, timeout=15
            )
            url = r.stdout.strip()
            if url.startswith("ssh://"):
                parts = url.replace("ssh://root@", "").split(":")
                host, port = parts[0], int(parts[1])
                log(f"  SSH (direct): {host}:{port}")
                return host, port
        except Exception:
            pass
        time.sleep(10)
    # Fallback: use machine_dir_ssh_port from API
    log("  vastai ssh-url timed out — trying API fallback...")
    inst = get_instance(token, instance_id)
    if inst:
        host = inst.get("public_ipaddr")
        for port_field in ["machine_dir_ssh_port", "ssh_port"]:
            port = inst.get(port_field)
            if host and port:
                log(f"  SSH ({port_field}): {host}:{port}")
                return host, int(port)
    return None, None


# ── Step 5: Wait for SSH ──────────────────────────────────────────────────────

def wait_ssh(ssh_key, host, port, retries=40):
    log(f"Waiting for SSH ({host}:{port})")
    for _ in range(retries):
        try:
            r = subprocess.run(
                ["ssh", "-i", ssh_key, "-p", str(port),
                 "-o", "StrictHostKeyChecking=no",
                 "-o", "ConnectTimeout=10",
                 f"root@{host}", "echo ok"],
                capture_output=True, text=True, timeout=15
            )
            if r.returncode == 0:
                log("  SSH ready.")
                return True
        except Exception:
            pass
        print(".", end="", flush=True)
        time.sleep(10)
    print()
    return False


def ssh_run(ssh_key, host, port, cmd, timeout=120):
    return subprocess.run(
        ["ssh", "-i", ssh_key, "-p", str(port),
         "-o", "StrictHostKeyChecking=no",
         "-o", "ConnectTimeout=15",
         "-o", "ServerAliveInterval=30",
         f"root@{host}", cmd],
        capture_output=True, text=True, timeout=timeout
    )


# ── Step 6: GPU health check ─────────────────────────────────────────────────

def check_gpu(ssh_key, host, port):
    """Confirm GPU is free and not zombie-locked by a previous tenant."""
    log("GPU health check...")
    r = ssh_run(ssh_key, host, port,
        "nvidia-smi --query-gpu=name,memory.free,memory.total,utilization.gpu "
        "--format=csv,noheader,nounits 2>&1",
        timeout=20)
    if r.returncode != 0 or not r.stdout.strip():
        log("  FAIL: nvidia-smi did not respond.")
        return False
    log(f"  GPU: {r.stdout.strip()}")
    # Check free VRAM >= 20GB (model needs ~14GB, leave headroom)
    try:
        parts = r.stdout.strip().split(",")
        free_mb = int(parts[1].strip())
        if free_mb < 20_000:
            log(f"  FAIL: Only {free_mb}MB VRAM free — GPU may be zombie-locked.")
            return False
    except Exception:
        pass  # parse failed, proceed optimistically
    log("  GPU healthy.")
    return True


# ── Step 7: Wait for R2 model pull ───────────────────────────────────────────

EXPECTED_MODELS = [
    f"{COMFY_PATH}/models/diffusion_models/Wan2.2/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
    f"{COMFY_PATH}/models/diffusion_models/Wan2.2/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
    f"{COMFY_PATH}/models/vae/wan_2.1_vae.safetensors",
    f"{COMFY_PATH}/models/text_encoders/umt5-xxl-enc-fp8_e4m3fn.safetensors",
]

def wait_models(ssh_key, host, port, timeout=600):
    log("Waiting for R2 model pull to complete...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        check = " && ".join(f'[ -f "{m}" ]' for m in EXPECTED_MODELS)
        r = ssh_run(ssh_key, host, port, f"{check} && echo MODELS_READY || echo WAITING", timeout=15)
        if "MODELS_READY" in r.stdout:
            log("  All models present!")
            r2 = ssh_run(ssh_key, host, port,
                f"du -sh {COMFY_PATH}/models/diffusion_models/ "
                f"{COMFY_PATH}/models/vae/ {COMFY_PATH}/models/text_encoders/ 2>/dev/null",
                timeout=15)
            print(r2.stdout)
            return True
        elapsed = time.time() - (deadline - timeout)
        print(f"  Still pulling... ({elapsed:.0f}s)", flush=True)
        time.sleep(20)
    log("  TIMEOUT: Models never arrived from R2.")
    return False


# ── Step 7: Wait for ComfyUI + WanVideo nodes ─────────────────────────────────

def wait_comfyui(ssh_key, host, port, num_gpus=1, timeout=300):
    log(f"Waiting for {num_gpus} ComfyUI instance(s) + WanVideo nodes...")
    ports = [18188 + i for i in range(num_gpus)]
    ready = set()
    deadline = time.time() + timeout
    while time.time() < deadline and len(ready) < len(ports):
        for p in ports:
            if p in ready:
                continue
            r = ssh_run(ssh_key, host, port,
                f"curl -s --max-time 3 http://localhost:{p}/object_info/LoadWanVideoT5TextEncoder",
                timeout=10)
            if r.stdout.strip() not in ("", "{}", "null", ""):
                ready.add(p)
                log(f"  GPU{p-18188} ready ✓")
        if len(ready) < len(ports):
            time.sleep(10)
    if len(ready) == len(ports):
        log("  ComfyUI fully ready.")
        return True
    log(f"  WARNING: Only {len(ready)}/{len(ports)} ComfyUI instances ready.")
    return len(ready) > 0


# ── Step 8: Get ComfyUI URLs ──────────────────────────────────────────────────

def get_comfyui_urls(instance, num_gpus=1):
    token   = instance.get("jupyter_token", "")
    ip      = instance.get("public_ipaddr") or instance.get("ssh_host")
    port_map = instance.get("ports", {})

    endpoints = []
    for i in range(num_gpus):
        ext_port = None
        for candidate in [8188 + i, 18188 + i]:
            for pk, pm in port_map.items():
                if pk.startswith(str(candidate) + "/") and isinstance(pm, list) and pm:
                    ext_port = pm[0].get("HostPort")
                    break
            if ext_port:
                break
        if not ext_port:
            ext_port = 18188 + i
        url = f"http://{ip}:{ext_port}"
        endpoints.append((url, token))
        log(f"  ComfyUI GPU{i}: {url}")
    return endpoints


# ── Step 9: Submit + download renders ─────────────────────────────────────────

def run_renders(endpoints, scene_start, scene_end, steps):
    script = str(Path(__file__).parent / "batch_render.py")
    url, token = endpoints[0]
    base = ["py", script, "--comfyui", url, "--start", str(scene_start),
            "--end", str(scene_end), "--steps", str(steps)]
    if token:
        base += ["--token", token]

    log(f"Submitting scenes {scene_start}–{scene_end} to {url}...")
    sub = subprocess.run(base + ["--submit"], timeout=300)
    if sub.returncode != 0:
        log("  WARNING: Submit returned non-zero. Check manually.")

    log("Polling for completed clips (downloads as they finish)...")
    dl = subprocess.run(base + ["--download"], timeout=7200)
    return dl.returncode == 0


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start",  type=int, default=1,  help="First scene")
    parser.add_argument("--end",    type=int, default=40, help="Last scene")
    parser.add_argument("--steps",  type=int, default=16, help="Denoising steps")
    parser.add_argument("--test",   action="store_true",  help="Boot + verify only, no render")
    args = parser.parse_args()

    cfg     = load_config()
    token   = cfg["api_key"]
    ssh_key = cfg["ssh_key_path"]
    r2      = cfg.get("r2", {})

    if not r2.get("account_id"):
        log("ERROR: R2 credentials missing from vastai_config.json")
        sys.exit(1)

    attempt = 0
    while True:
        attempt += 1
        t0 = time.time()
        log(f"\n{'='*60}")
        log(f"Attempt {attempt} — finding GPU...")

        # 1. Find offer
        offer = find_offer(token)
        offer_ip = offer.get("public_ipaddr") or offer.get("inet_ipaddr", "")

        # 2. Rent
        instance_id = rent(token, offer["id"], r2)
        if not instance_id:
            log("Rent failed — retrying in 30s...")
            time.sleep(30)
            continue

        # 3. Wait for running
        inst = wait_running(token, instance_id)
        if not inst:
            log(f"Instance {instance_id} never came up. Blacklisting {offer_ip}.")
            save_bad_ip(offer_ip)
            try: api_delete(token, f"instances/{instance_id}/")
            except: pass
            continue

        # 4. Get direct SSH
        host, port = get_ssh(token, instance_id)
        if not host:
            log("Could not get SSH details. Destroying and retrying.")
            api_delete(token, f"instances/{instance_id}/")
            continue

        # 5. Wait for SSH
        if not wait_ssh(ssh_key, host, port):
            log(f"SSH never available. Blacklisting {offer_ip}.")
            save_bad_ip(offer_ip)
            api_delete(token, f"instances/{instance_id}/")
            continue

        t_ssh = time.time()
        log(f"SSH ready in {t_ssh - t0:.0f}s")

        # 6. GPU health check
        if not check_gpu(ssh_key, host, port):
            log(f"GPU failed health check. Blacklisting {offer_ip}.")
            save_bad_ip(offer_ip)
            api_delete(token, f"instances/{instance_id}/")
            continue

        # 8. Wait for R2 model pull
        if not wait_models(ssh_key, host, port):
            log("Models never arrived. Destroying and retrying.")
            api_delete(token, f"instances/{instance_id}/")
            continue

        t_models = time.time()
        log(f"Models ready in {t_models - t0:.0f}s ({(t_models-t0)/60:.1f} min)")

        # 9. Wait for ComfyUI + WanVideo nodes
        num_gpus = inst.get("num_gpus", 1)
        if not wait_comfyui(ssh_key, host, port, num_gpus):
            log("ComfyUI not ready. Destroying and retrying.")
            api_delete(token, f"instances/{instance_id}/")
            continue

        t_ready = time.time()
        log(f"COLD START COMPLETE: {(t_ready-t0)/60:.1f} min | "
            f"{num_gpus}x {inst.get('gpu_name')} @ ${offer['dph_total']:.3f}/hr")

        if args.test:
            log("Test mode -- skipping renders. Destroying instance.")
            api_delete(token, f"instances/{instance_id}/")
            log(f"Cold-start time: {(t_ready-t0)/60:.1f} min. Done.")
            return

        # 10. Get ComfyUI endpoints — re-fetch for fresh port mapping
        inst = get_instance(token, instance_id)
        endpoints = get_comfyui_urls(inst, num_gpus)

        # 11. Render
        success = run_renders(endpoints, args.start, args.end, args.steps)

        # 10. Destroy
        log("Destroying instance...")
        try:
            api_delete(token, f"instances/{instance_id}/")
            log("Instance destroyed.")
        except Exception as e:
            log(f"WARNING: Destroy failed: {e}")

        if success:
            log(f"\nAll scenes {args.start}–{args.end} complete. Done!")
            return
        else:
            log("Render returned failure — looping to rent new instance...")
            time.sleep(10)


if __name__ == "__main__":
    main()
