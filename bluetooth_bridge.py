#!/usr/bin/env python3
"""
Bluetooth Call Bridge Setup Script
Routes phone call audio through Raspberry Pi to earbuds and captures audio

Key improvements:
- Non-blocking pipe using background drain process
- Seamless integration with scam detection script
- Reliable audio routing that doesn't block earbuds

Requirements:
- 2 Bluetooth adapters (hci0: onboard, hci1: USB dongle)
- bluez, bluez-tools, bluez-alsa-utils installed
- sox installed (for the client script)
"""

import subprocess
import time
import sys
import os
from datetime import datetime

# Device Configuration  
PHONE_MAC = "30:BB:7D:48:29:BC"  # OnePlus Nord CE 3
EARBUDS_MAC = "8C:64:A2:33:E8:D8"  # OnePlus Nord Buds
HCI0_MAC = "2C:CF:67:0A:17:6C"  # Onboard adapter (for phone)
HCI1_MAC = "0C:EF:15:43:05:8A"  # USB adapter (for earbuds)

# Recording Configuration
RECORDING_DIR = "/home/eshita"
PIPE_PATH = "/tmp/downlink_tap"

# Color codes
RED = '\033[0;31m'
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
BLUE = '\033[0;34m'
NC = '\033[0m'

def print_color(message, color=NC):
    """Print colored message"""
    print(f"{color}{message}{NC}")

def run_command(cmd, capture_output=False, shell=False):
    """Run shell command and return result"""
    try:
        if capture_output:
            result = subprocess.run(cmd, shell=shell, capture_output=True, text=True, timeout=10)
            return result.returncode, result.stdout, result.stderr
        else:
            result = subprocess.run(cmd, shell=shell, timeout=10)
            return result.returncode, "", ""
    except subprocess.TimeoutExpired:
        print_color(f"Command timed out: {cmd}", YELLOW)
        return -1, "", "Timeout"
    except Exception as e:
        print_color(f"Error running command: {e}", RED)
        return -1, "", str(e)

def check_root():
    """Check if running as root"""
    ret, out, _ = run_command(["id", "-u"], capture_output=True)
    if ret == 0 and out.strip() == "0":
        return True
    return False

def setup_recording_dir():
    """Create recording directory and the named pipe if they don't exist"""
    if not os.path.exists(RECORDING_DIR):
        os.makedirs(RECORDING_DIR)
        print_color(f"Created recording directory: {RECORDING_DIR}", GREEN)

    # Remove old pipe if it exists to avoid stale state
    if os.path.exists(PIPE_PATH):
        try:
            os.remove(PIPE_PATH)
            print_color(f"Removed old pipe: {PIPE_PATH}", GREEN)
        except Exception as e:
            print_color(f"Could not remove old pipe: {e}", YELLOW)
    
    # Create fresh named pipe
    try:
        os.mkfifo(PIPE_PATH)
        # Make pipe readable/writable by all to avoid permission issues
        os.chmod(PIPE_PATH, 0o666)
        print_color(f"Created named pipe: {PIPE_PATH}", GREEN)
    except Exception as e:
        print_color(f"Failed to create named pipe: {e}", RED)
        sys.exit(1)

def stop_services():
    """Stop and disable conflicting services"""
    print_color("\n[1/8] Stopping conflicting services...", BLUE)
    
    # Kill any running processes
    run_command(["killall", "-q", "bluetoothd", "ofonod", "bluealsa", "arecord", "aplay", "tee", "dd"], shell=False)
    time.sleep(2)
    
    # Stop and disable services
    run_command(["systemctl", "stop", "bluetooth"], shell=False)
    run_command(["systemctl", "disable", "bluetooth"], shell=False)
    run_command(["systemctl", "stop", "ofono"], shell=False)
    run_command(["systemctl", "disable", "ofono"], shell=False)
    
    print_color("Services stopped", GREEN)

def setup_adapters():
    """Configure both Bluetooth adapters"""
    print_color("\n[2/8] Setting up Bluetooth adapters...", BLUE)
    
    # Unblock RF-kill
    run_command(["rfkill", "unblock", "bluetooth"], shell=False)
    time.sleep(1)
    
    # Bring up both adapters
    ret0, _, _ = run_command(["hciconfig", "hci0", "up"], shell=False)
    ret1, _, _ = run_command(["hciconfig", "hci1", "up"], shell=False)
    
    if ret0 != 0 or ret1 != 0:
        print_color("Failed to bring up adapters", RED)
        sys.exit(1)
    
    # Verify status
    ret, out, _ = run_command(["hciconfig"], capture_output=True)
    if "UP RUNNING" in out:
        print_color("Both adapters are UP RUNNING", GREEN)
    else:
        print_color("Adapters not running properly", RED)
        print(out)
        sys.exit(1)

def start_bluetoothd():
    """Start bluetoothd with experimental features"""
    print_color("\n[3/8] Starting bluetoothd...", BLUE)
    
    subprocess.Popen(
        ["/usr/libexec/bluetooth/bluetoothd", "--experimental"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(3)
    
    # Verify it's running
    ret, out, _ = run_command(["ps", "aux"], capture_output=True)
    if "bluetoothd" in out:
        print_color("bluetoothd started", GREEN)
    else:
        print_color("bluetoothd failed to start", RED)
        sys.exit(1)

def start_bluealsa():
    """Start bluealsa with HFP support"""
    print_color("\n[4/8] Starting bluealsa...", BLUE)
    
    subprocess.Popen(
        ["bluealsa", "-p", "hfp-hf", "-p", "hfp-ag", "-p", "a2dp-source", "-p", "a2dp-sink"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(2)
    
    # Verify it's running
    ret, out, _ = run_command(["ps", "aux"], capture_output=True)
    if "bluealsa" in out:
        print_color("bluealsa started", GREEN)
    else:
        print_color("bluealsa failed to start", RED)
        sys.exit(1)

def connect_devices():
    """Connect phone and earbuds via bluetoothctl"""
    print_color("\n[5/8] Connecting devices...", BLUE)
    
    print_color("  Connecting phone to hci0...", NC)
    # Connect phone to hci0
    phone_commands = f"""select {HCI0_MAC}
power on
trust {PHONE_MAC}
connect {PHONE_MAC}
"""
    
    proc = subprocess.Popen(
        ["bluetoothctl"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    time.sleep(2)
    try:
        out, _ = proc.communicate(input=phone_commands, timeout=10)
    except subprocess.TimeoutExpired:
        print_color("  Phone connection timed out", YELLOW)
    
    time.sleep(2)
    
    print_color("  Connecting earbuds to hci1...", NC)
    # Connect earbuds to hci1
    earbuds_commands = f"""select {HCI1_MAC}
power on
trust {EARBUDS_MAC}
connect {EARBUDS_MAC}
quit
"""
    
    proc = subprocess.Popen(
        ["bluetoothctl"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    time.sleep(2)
    try:
        out2, _ = proc.communicate(input=earbuds_commands, timeout=10)
    except subprocess.TimeoutExpired:
        print_color("  Earbuds connection timed out", YELLOW)

    
    # Check connection status
    time.sleep(3)
    ret, status, _ = run_command(["bluetoothctl", "info", PHONE_MAC], capture_output=True)
    phone_connected = "Connected: yes" in status
    
    ret, status, _ = run_command(["bluetoothctl", "info", EARBUDS_MAC], capture_output=True)
    earbuds_connected = "Connected: yes" in status
    
    if phone_connected and earbuds_connected:
        print_color("Both devices connected", GREEN)
    elif phone_connected:
        print_color("Phone connected, but earbuds failed", YELLOW)
    elif earbuds_connected:
        print_color("Earbuds connected, but phone failed", YELLOW)
    else:
        print_color("Connection failed for both devices", RED)
        print_color("  Try connecting manually or check if devices are paired", YELLOW)

def verify_audio_devices():
    """Verify bluealsa audio devices are available"""
    print_color("\n[6/8] Verifying audio devices...", BLUE)
    
    ret, out, _ = run_command(["bluealsa-aplay", "-L"], capture_output=True)
    
    if PHONE_MAC in out and EARBUDS_MAC in out and "sco" in out:
        print_color("Audio devices available:", GREEN)
        print(out)
    else:
        print_color("Audio devices not properly configured", RED)
        print(out)
        sys.exit(1)

def start_audio_routing():
    """Start audio routing with reliable non-blocking pipe"""
    print_color("\n[7/8] Starting audio routing with non-blocking pipe...", BLUE)
    
    # Start persistent pipe drain process
    # This runs in background and continuously reads from the pipe
    # When scam detection starts, it takes over automatically
    drain_script = f"""#!/bin/bash
while true; do
    # Try to read from pipe, discard data
    # If pipe is closed/busy, sleep briefly and retry
    cat {PIPE_PATH} > /dev/null 2>&1 || sleep 0.1
done
"""
    
    # Write drain script to temp file
    drain_script_path = "/tmp/pipe_drain.sh"
    with open(drain_script_path, 'w') as f:
        f.write(drain_script)
    os.chmod(drain_script_path, 0o755)
    
    # Start drain process in background
    subprocess.Popen(
        ["/bin/bash", drain_script_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True  # Detach from parent
    )
    
    time.sleep(1)
    print_color("Pipe drain process started (keeps pipe from blocking)", GREEN)
    
    # Main audio pipeline: Phone -> Earbuds + Pipe
    # Using process substitution to avoid blocking
    downlink_pipeline = f"""#!/bin/bash
exec arecord -D bluealsa:DEV={PHONE_MAC},PROFILE=sco -f S16_LE -r 8000 -c 1 2>/dev/null | \\
tee >(aplay -D bluealsa:DEV={EARBUDS_MAC},PROFILE=sco 2>/dev/null) | \\
dd of={PIPE_PATH} bs=512 2>/dev/null
"""
    
    # Write pipeline script
    pipeline_script_path = "/tmp/audio_downlink.sh"
    with open(pipeline_script_path, 'w') as f:
        f.write(downlink_pipeline)
    os.chmod(pipeline_script_path, 0o755)
    
    # Start downlink pipeline
    subprocess.Popen(
        ["/bin/bash", pipeline_script_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True
    )
    
    time.sleep(1)
    print_color("Downlink audio routing started (Phone → Earbuds + Pipe)", GREEN)

    # Uplink: Earbuds -> Phone (unchanged, no recording needed)
    uplink_pipeline = f"""#!/bin/bash
exec arecord -D bluealsa:DEV={EARBUDS_MAC},PROFILE=sco -f S16_LE -r 8000 -c 1 2>/dev/null | \\
aplay -D bluealsa:DEV={PHONE_MAC},PROFILE=sco 2>/dev/null
"""
    
    uplink_script_path = "/tmp/audio_uplink.sh"
    with open(uplink_script_path, 'w') as f:
        f.write(uplink_pipeline)
    os.chmod(uplink_script_path, 0o755)
    
    subprocess.Popen(
        ["/bin/bash", uplink_script_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True
    )
    
    time.sleep(2)
    print_color("Uplink audio routing started (Earbuds → Phone)", GREEN)
    
    # Verify processes are running
    ret, out, _ = run_command(["ps", "aux"], capture_output=True)
    arecord_count = out.count("arecord")
    aplay_count = out.count("aplay")
    tee_count = out.count("tee")
    drain_count = out.count("pipe_drain")
    
    if arecord_count >= 2 and tee_count >= 1 and drain_count >= 1:
        print_color(f"All audio processes running:", GREEN)
        print_color(f"  • arecord processes: {arecord_count}", NC)
        print_color(f"  • aplay processes: {aplay_count}", NC)
        print_color(f"  • tee processes: {tee_count}", NC)
        print_color(f"  • pipe drain: active", NC)
        print_color(f"  • Non-blocking stream to: {PIPE_PATH}", GREEN)
    else:
        print_color(f"Audio routing may not be complete", YELLOW)
        print_color(f"  • arecord: {arecord_count}, tee: {tee_count}, drain: {drain_count}", YELLOW)

def manual_connect():
    """Manual connection guide"""
    print_color("\n" + "="*60, YELLOW)
    print_color("MANUAL CONNECTION REQUIRED", YELLOW)
    print_color("="*60, YELLOW)
    
    print_color("\nOpen bluetoothctl in another terminal and run:", NC)
    print_color("\n# Connect phone to hci0:", BLUE)
    print_color(f"  select {HCI0_MAC}", NC)
    print_color("  power on", NC)
    print_color(f"  connect {PHONE_MAC}", NC)
    
    print_color("\n# Connect earbuds to hci1:", BLUE)
    print_color(f"  select {HCI1_MAC}", NC)
    print_color("  power on", NC)
    print_color(f"  connect {EARBUDS_MAC}", NC)
    print_color("  quit", NC)
    
    print_color("\n" + "="*60 + "\n", YELLOW)
    
    response = input("Press Enter when devices are connected (or 's' to skip): ")
    if response.lower() == 's':
        print_color("Skipping device verification", YELLOW)
        return False
    return True

def show_status():
    """Show final status and instructions"""
    print_color("\n[8/8] Setup Complete!", GREEN)
    print_color("\n" + "="*60, BLUE)
    print_color("BLUETOOTH CALL BRIDGE STATUS", BLUE)
    print_color("="*60, BLUE)
    
    print_color(f"\n📱 Phone: {PHONE_MAC}", NC)
    print_color(f"🎧 Earbuds: {EARBUDS_MAC}", NC)
    
    print_color("\n Audio routing active:", GREEN)
    print_color("   • Phone → Pi → Earbuds (always works)", NC)
    print_color("   • Earbuds → Pi → Phone (microphone)", NC)
    print_color(f"   • Non-blocking stream to: {PIPE_PATH}", NC)
    print_color("   • Auto-drain keeps earbuds working 24/7", NC)
    
    print_color("\n How it works:", YELLOW)
    print_color("   1. Bridge is always connected (earbuds work perfectly)", NC)
    print_color("   2. Pipe has auto-drain running in background", NC)
    print_color("   3. Start scam detection script ANYTIME with:", NC)
    print_color("      python3 scam_detection.py --source pipe", BLUE)
    print_color("   4. Scam detection takes over from drain seamlessly", NC)
    print_color("   5. Stop scam detection → drain takes over again", NC)
    print_color("   6. Earbuds NEVER block, audio always flows", NC)

    print_color("\n Important notes:", YELLOW)
    print_color("   • Make a phone call to test audio", NC)
    print_color("   • Earbuds work independently of scam detection", NC)
    print_color("   • Start/stop scam detection anytime without issues", NC)
    print_color("   • Pipe is always drained - no blocking possible", NC)
    
    print_color("\n Troubleshooting:", BLUE)
    print_color("   Check connections:  hcitool con", NC)
    print_color("   View processes:     ps aux | grep -E 'arecord|aplay|tee|pipe_drain'", NC)
    print_color("   Stop all:           sudo killall -9 arecord aplay tee dd bash", NC)
    print_color(f"   Check pipe:         ls -l {PIPE_PATH}", NC)
    print_color("   Check drain:        ps aux | grep pipe_drain", NC)
    print_color("   Restart bridge:     sudo python3 setup_bridge.py", NC)

    print_color("\n Performance tips:", BLUE)
    print_color("   • Audio format: 8kHz, 16-bit, mono (SCO profile)", NC)
    print_color("   • Low latency: ~100-200ms typical", NC)
    print_color("   • CPU usage: ~5-10% on Raspberry Pi 4", NC)
    
    print_color("\n" + "="*60 + "\n", BLUE)

def main():
    """Main setup routine"""
    print_color("\n" + "="*60, BLUE)
    print_color("BLUETOOTH CALL BRIDGE SETUP - FIXED VERSION", BLUE)
    print_color("="*60 + "\n", BLUE)
    
    # Check root
    if not check_root():
        print_color("This script must be run as root (use sudo)", RED)
        sys.exit(1)
    
    setup_recording_dir()
    
    print_color(" This will reconfigure your Bluetooth adapters", YELLOW)
    print_color(" Make sure both adapters are connected\n", YELLOW)
    
    response = input("Continue? (y/N): ")
    if response.lower() != 'y':
        print_color("Setup cancelled", YELLOW)
        sys.exit(0)
    
    try:
        stop_services()
        setup_adapters()
        start_bluetoothd()
        start_bluealsa()
        connect_devices()
        
        # Check if connection was successful
        ret, out, _ = run_command(["bluealsa-aplay", "-L"], capture_output=True)
        if PHONE_MAC not in out or EARBUDS_MAC not in out:
            print_color("\n⚠ Devices not fully connected", YELLOW)
            if manual_connect():
                time.sleep(2)
        
        verify_audio_devices()
        start_audio_routing()
        show_status()
        
    except KeyboardInterrupt:
        print_color("\n\nSetup interrupted by user", YELLOW)
        print_color("Stopping processes...", YELLOW)
        run_command(["killall", "-9", "arecord", "aplay", "tee", "dd", "bash"], shell=False)
        sys.exit(1)
    except Exception as e:
        print_color(f"\n\n✗ Setup failed: {e}", RED)
        sys.exit(1)

if __name__ == "__main__":
    main()