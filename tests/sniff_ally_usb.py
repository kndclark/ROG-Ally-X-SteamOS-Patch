import sys
import re
import subprocess
import threading
import time

def monitor_dmesg():
    # Read dmesg --follow
    proc = subprocess.Popen(["dmesg", "-w"], stdout=subprocess.PIPE, text=True)
    for line in proc.stdout:
        if "ALLY_DRV_RAW:" in line:
            # Extract hex part
            # e.g. [36061.944804] ALLY_DRV_RAW: 5a d1 ...
            match = re.search(r"ALLY_DRV_RAW:\s*(.*)", line)
            if match:
                print(f"[DMESG ] {match.group(1).strip()}")

def monitor_usbmon():
    # Read usbmon 1u
    with open("/sys/kernel/debug/usb/usbmon/1u", "r") as f:
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.01)
                continue
            
            # format: tag timestamp type bus:dev:ep stat length [payload...]
            # We want to catch submissions (S) or callbacks (C) that have payloads
            # starting with 5a
            parts = line.split('=')
            if len(parts) == 2:
                payload = parts[1].strip()
                # usbmon hex is consecutive e.g. 5ad10404 ...
                # let's format it to space separated
                if payload.startswith("5a") or payload.startswith("5A"):
                    # split into 2-char chunks
                    spaced = " ".join([payload[i:i+2] for i in range(0, len(payload), 2)])
                    print(f"[USBMON] {spaced}")

if __name__ == "__main__":
    print("Starting USB sniffer...")
    t_dmesg = threading.Thread(target=monitor_dmesg, daemon=True)
    t_usbmon = threading.Thread(target=monitor_usbmon, daemon=True)
    
    t_dmesg.start()
    t_usbmon.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping...")
        sys.exit(0)
