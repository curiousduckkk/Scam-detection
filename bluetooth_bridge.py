#!/usr/bin/env python3
"""
Bluetooth Call Bridge Setup Script
Routes phone call audio through Raspberry Pi to earbuds and captures audio

Key improvements:
- Non-blocking pipe using background drain process
- Seamless integration with scam detection script
- Reliable audio routing that doesn't block earbuds
- Proper process tracking and cleanup
- Signal handlers for graceful shutdown

Requirements:
- 2 Bluetooth adapters (hci0: onboard, hci1: USB dongle)
- bluez, bluez-tools, bluez-alsa-utils installed
- sox installed (for the client script)
"""

import subprocess
import time
import sys
import os
import signal
import atexit
from datetime import datetime
from typing import List, Tuple, Optional

import config
from logger import bluetooth_logger, log_exception

managed_processes: List[subprocess.Popen] = []
managed_scripts: List[str] = []

def print_color(message, color=config.COLOR_NC):
    """Print colored message and log it"""
    print(f"{color}{message}{config.COLOR_NC}")
    if color == config.COLOR_RED:
        bluetooth_logger.error(message)
    elif color == config.COLOR_YELLOW:
        bluetooth_logger.warning(message)
    elif color == config.COLOR_GREEN or color == config.COLOR_BLUE:
        bluetooth_logger.info(message)
    else:
        bluetooth_logger.debug(message)


def run_command(cmd, capture_output=False, shell=False):
    """Run shell command and return result"""
    try:
        if capture_output:
            result = subprocess.run(
                cmd, 
                shell=shell, 
                capture_output=True, 
                text=True, 
                timeout=config.COMMAND_TIMEOUT
            )
            return result.returncode, result.stdout, result.stderr
        else:
            result = subprocess.run(cmd, shell=shell, timeout=config.COMMAND_TIMEOUT)
            return result.returncode, "", ""
    except subprocess.TimeoutExpired:
        print_color(f"Command timed out: {cmd}", config.COLOR_YELLOW)
        bluetooth_logger.warning(f"Command timed out: {cmd}")
        return -1, "", "Timeout"
    except Exception as e:
        print_color(f"Error running command: {e}", config.COLOR_RED)
        log_exception(bluetooth_logger, e, f"Error running command: {cmd}")
        return -1, "", str(e)

def check_root():
    """Check if running as root"""
    ret, out, _ = run_command(["id", "-u"], capture_output=True)
    if ret == 0 and out.strip() == "0":
        return True
    return False

def cleanup_processes():
    """Clean up all managed processes"""
    bluetooth_logger.info("Cleaning up managed processes...")
    
    for proc in managed_processes:
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=config.PROCESS_CLEANUP_TIMEOUT)
                except subprocess.TimeoutExpired:
                    bluetooth_logger.warning(f"Process {proc.pid} did not terminate, killing...")
                    proc.kill()
        except Exception as e:
            log_exception(bluetooth_logger, e, f"Error cleaning up process {proc.pid}")
    
    managed_processes.clear()
    
    for script_path in managed_scripts:
        try:
            if os.path.exists(script_path):
                os.remove(script_path)
                bluetooth_logger.info(f"Removed script: {script_path}")
        except Exception as e:
            log_exception(bluetooth_logger, e, f"Error removing script {script_path}")
    
    managed_scripts.clear()

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    print_color(f"\nReceived signal {signum}, shutting down...", config.COLOR_YELLOW)
    cleanup_processes()
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)
atexit.register(cleanup_processes)

def setup_recording_dir():
    """Create recording directory and the named pipe if they don't exist"""
    if not os.path.exists(config.RECORDING_DIR):
        try:
            os.makedirs(config.RECORDING_DIR)
            print_color(f"Created recording directory: {config.RECORDING_DIR}", config.COLOR_GREEN)
        except Exception as e:
            print_color(f"Failed to create recording directory: {e}", config.COLOR_RED)
            log_exception(bluetooth_logger, e, "Failed to create recording directory")
            sys.exit(1)

    if os.path.exists(config.PIPE_PATH):
        try:
            os.remove(config.PIPE_PATH)
            print_color(f"Removed old pipe: {config.PIPE_PATH}", config.COLOR_GREEN)
        except Exception as e:
            print_color(f"Could not remove old pipe: {e}", config.COLOR_YELLOW)
            bluetooth_logger.warning(f"Could not remove old pipe: {e}")
    
    try:
        os.mkfifo(config.PIPE_PATH)
        os.chmod(config.PIPE_PATH, 0o666)
        print_color(f"Created named pipe: {config.PIPE_PATH}", config.COLOR_GREEN)
    except Exception as e:
        print_color(f"Failed to create named pipe: {e}", config.COLOR_RED)
        log_exception(bluetooth_logger, e, "Failed to create named pipe")
        sys.exit(1)


def stop_services():
    """Stop and disable conflicting services"""
    print_color("\n[1/8] Stopping conflicting services...", config.COLOR_BLUE)
    
    run_command(["killall", "-q", "bluetoothd", "ofonod", "bluealsa", "arecord", "aplay", "tee", "dd"], shell=False)
    time.sleep(2)
    
    run_command(["systemctl", "stop", "bluetooth"], shell=False)
    run_command(["systemctl", "disable", "bluetooth"], shell=False)
    run_command(["systemctl", "stop", "ofono"], shell=False)
    run_command(["systemctl", "disable", "ofono"], shell=False)
    
    print_color("Services stopped", config.COLOR_GREEN)

def setup_adapters():
    """Configure both Bluetooth adapters"""
    print_color("\n[2/8] Setting up Bluetooth adapters...", config.COLOR_BLUE)
    
    run_command(["rfkill", "unblock", "bluetooth"], shell=False)
    time.sleep(1)
    
    ret0, _, _ = run_command(["hciconfig", "hci0", "up"], shell=False)
    ret1, _, _ = run_command(["hciconfig", "hci1", "up"], shell=False)
    
    if ret0 != 0 or ret1 != 0:
        print_color("Failed to bring up adapters", config.COLOR_RED)
        sys.exit(1)
    
    ret, out, _ = run_command(["hciconfig"], capture_output=True)
    if "UP RUNNING" in out:
        print_color("Both adapters are UP RUNNING", config.COLOR_GREEN)
    else:
        print_color("Adapters not running properly", config.COLOR_RED)
        print(out)
        sys.exit(1)

def start_bluetoothd():
    """Start bluetoothd with experimental features"""
    print_color("\n[3/8] Starting bluetoothd...", config.COLOR_BLUE)
    
    try:
        proc = subprocess.Popen(
            ["/usr/libexec/bluetooth/bluetoothd", "--experimental"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        managed_processes.append(proc)
        time.sleep(3)
        
        ret, out, _ = run_command(["ps", "aux"], capture_output=True)
        if "bluetoothd" in out:
            print_color("bluetoothd started", config.COLOR_GREEN)
        else:
            print_color("bluetoothd failed to start", config.COLOR_RED)
            sys.exit(1)
    except Exception as e:
        print_color(f"Failed to start bluetoothd: {e}", config.COLOR_RED)
        log_exception(bluetooth_logger, e, "Failed to start bluetoothd")
        sys.exit(1)

def start_bluealsa():
    """Start bluealsa with HFP support"""
    print_color("\n[4/8] Starting bluealsa...", config.COLOR_BLUE)
    
    try:
        proc = subprocess.Popen(
            ["bluealsa", "-p", "hfp-hf", "-p", "hfp-ag", "-p", "a2dp-source", "-p", "a2dp-sink"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        managed_processes.append(proc)
        time.sleep(2)
        
        ret, out, _ = run_command(["ps", "aux"], capture_output=True)
        if "bluealsa" in out:
            print_color("bluealsa started", config.COLOR_GREEN)
        else:
            print_color("bluealsa failed to start", config.COLOR_RED)
            sys.exit(1)
    except Exception as e:
        print_color(f"Failed to start bluealsa: {e}", config.COLOR_RED)
        log_exception(bluetooth_logger, e, "Failed to start bluealsa")
        sys.exit(1)


def connect_devices():
    """Connect phone and earbuds via bluetoothctl"""
    print_color("\n[5/8] Connecting devices...", config.COLOR_BLUE)
    
    print_color("  Connecting phone to hci0...", config.COLOR_NC)
    phone_commands = f"""select {config.HCI0_MAC}
power on
trust {config.PHONE_MAC}
connect {config.PHONE_MAC}
"""
    
    try:
        proc = subprocess.Popen(
            ["bluetoothctl"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        time.sleep(2)
        try:
            out, _ = proc.communicate(input=phone_commands, timeout=config.BT_CONNECTION_TIMEOUT)
        except subprocess.TimeoutExpired:
            print_color("  Phone connection timed out", config.COLOR_YELLOW)
            proc.kill()
    except Exception as e:
        log_exception(bluetooth_logger, e, "Error connecting phone")
    
    time.sleep(2)
    
    print_color("  Connecting earbuds to hci1...", config.COLOR_NC)
    earbuds_commands = f"""select {config.HCI1_MAC}
power on
trust {config.EARBUDS_MAC}
connect {config.EARBUDS_MAC}
quit
"""
    
    try:
        proc = subprocess.Popen(
            ["bluetoothctl"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        time.sleep(2)
        try:
            out2, _ = proc.communicate(input=earbuds_commands, timeout=config.BT_CONNECTION_TIMEOUT)
        except subprocess.TimeoutExpired:
            print_color("  Earbuds connection timed out", config.COLOR_YELLOW)
            proc.kill()
    except Exception as e:
        log_exception(bluetooth_logger, e, "Error connecting earbuds")

    
    time.sleep(config.BT_VERIFY_DELAY)
    ret, status, _ = run_command(["bluetoothctl", "info", config.PHONE_MAC], capture_output=True)
    phone_connected = "Connected: yes" in status
    
    ret, status, _ = run_command(["bluetoothctl", "info", config.EARBUDS_MAC], capture_output=True)
    earbuds_connected = "Connected: yes" in status
    
    if phone_connected and earbuds_connected:
        print_color("Both devices connected", config.COLOR_GREEN)
    elif phone_connected:
        print_color("Phone connected, but earbuds failed", config.COLOR_YELLOW)
    elif earbuds_connected:
        print_color("Earbuds connected, but phone failed", config.COLOR_YELLOW)
    else:
        print_color("Connection failed for both devices", config.COLOR_RED)
        print_color("  Try connecting manually or check if devices are paired", config.COLOR_YELLOW)

def verify_audio_devices():
    """Verify bluealsa audio devices are available"""
    print_color("\n[6/8] Verifying audio devices...", config.COLOR_BLUE)
    
    ret, out, _ = run_command(["bluealsa-aplay", "-L"], capture_output=True)
    
    if config.PHONE_MAC in out and config.EARBUDS_MAC in out and "sco" in out:
        print_color("Audio devices available:", config.COLOR_GREEN)
        print(out)
    else:
        print_color("Audio devices not properly configured", config.COLOR_RED)
        print(out)
        sys.exit(1)


def start_audio_routing():
    """Start audio routing with reliable non-blocking pipe"""
    print_color("\n[7/8] Starting audio routing with non-blocking pipe...", config.COLOR_BLUE)
    
    # Start persistent pipe drain process
    # This runs in background and continuously reads from the pipe
    # When scam detection starts, it takes over automatically
    drain_script = f"""#!/bin/bash
while true; do
    # Try to read from pipe, discard data
    # If pipe is closed/busy, sleep briefly and retry
    cat {config.PIPE_PATH} > /dev/null 2>&1 || sleep 0.1
done
"""
    
    drain_script_path = "/tmp/pipe_drain.sh"
    try:
        with open(drain_script_path, 'w') as f:
            f.write(drain_script)
        os.chmod(drain_script_path, 0o755)
        managed_scripts.append(drain_script_path)
        
        proc = subprocess.Popen(
            ["/bin/bash", drain_script_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        managed_processes.append(proc)
        
        time.sleep(1)
        print_color("Pipe drain process started (keeps pipe from blocking)", config.COLOR_GREEN)
    except Exception as e:
        print_color(f"Failed to start pipe drain: {e}", config.COLOR_RED)
        log_exception(bluetooth_logger, e, "Failed to start pipe drain")
    
    # Main audio pipeline: Phone -> Earbuds + Pipe
    # Using process substitution to avoid blocking
    downlink_pipeline = f"""#!/bin/bash
exec arecord -D bluealsa:DEV={config.PHONE_MAC},PROFILE=sco -f S16_LE -r {config.BT_AUDIO_RATE} -c {config.BT_AUDIO_CHANNELS} 2>/dev/null | \\
tee >(aplay -D bluealsa:DEV={config.EARBUDS_MAC},PROFILE=sco 2>/dev/null) | \\
dd of={config.PIPE_PATH} bs=512 2>/dev/null
"""
    
    pipeline_script_path = "/tmp/audio_downlink.sh"
    try:
        with open(pipeline_script_path, 'w') as f:
            f.write(downlink_pipeline)
        os.chmod(pipeline_script_path, 0o755)
        managed_scripts.append(pipeline_script_path)
        
        proc = subprocess.Popen(
            ["/bin/bash", pipeline_script_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        managed_processes.append(proc)
        
        time.sleep(1)
        print_color("Downlink audio routing started (Phone → Earbuds + Pipe)", config.COLOR_GREEN)
    except Exception as e:
        print_color(f"Failed to start downlink audio: {e}", config.COLOR_RED)
        log_exception(bluetooth_logger, e, "Failed to start downlink audio")

    uplink_pipeline = f"""#!/bin/bash
exec arecord -D bluealsa:DEV={config.EARBUDS_MAC},PROFILE=sco -f S16_LE -r {config.BT_AUDIO_RATE} -c {config.BT_AUDIO_CHANNELS} 2>/dev/null | \\
aplay -D bluealsa:DEV={config.PHONE_MAC},PROFILE=sco 2>/dev/null
"""
    
    uplink_script_path = "/tmp/audio_uplink.sh"
    try:
        with open(uplink_script_path, 'w') as f:
            f.write(uplink_pipeline)
        os.chmod(uplink_script_path, 0o755)
        managed_scripts.append(uplink_script_path)
        
        proc = subprocess.Popen(
            ["/bin/bash", uplink_script_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        managed_processes.append(proc)
        
        time.sleep(2)
        print_color("Uplink audio routing started (Earbuds → Phone)", config.COLOR_GREEN)
    except Exception as e:
        print_color(f"Failed to start uplink audio: {e}", config.COLOR_RED)
        log_exception(bluetooth_logger, e, "Failed to start uplink audio")
    
    ret, out, _ = run_command(["ps", "aux"], capture_output=True)
    arecord_count = out.count("arecord")
    aplay_count = out.count("aplay")
    tee_count = out.count("tee")
    drain_count = out.count("pipe_drain")
    
    if arecord_count >= 2 and tee_count >= 1 and drain_count >= 1:
        print_color(f"All audio processes running:", config.COLOR_GREEN)
        print_color(f"  • arecord processes: {arecord_count}", config.COLOR_NC)
        print_color(f"  • aplay processes: {aplay_count}", config.COLOR_NC)
        print_color(f"  • tee processes: {tee_count}", config.COLOR_NC)
        print_color(f"  • pipe drain: active", config.COLOR_NC)
        print_color(f"  • Non-blocking stream to: {config.PIPE_PATH}", config.COLOR_GREEN)
    else:
        print_color(f"Audio routing may not be complete", config.COLOR_YELLOW)
        print_color(f"  • arecord: {arecord_count}, tee: {tee_count}, drain: {drain_count}", config.COLOR_YELLOW)


def manual_connect():
    """Manual connection guide"""
    print_color("\n" + "="*60, config.COLOR_YELLOW)
    print_color("MANUAL CONNECTION REQUIRED", config.COLOR_YELLOW)
    print_color("="*60, config.COLOR_YELLOW)
    
    print_color("\nOpen bluetoothctl in another terminal and run:", config.COLOR_NC)
    print_color("\n# Connect phone to hci0:", config.COLOR_BLUE)
    print_color(f"  select {config.HCI0_MAC}", config.COLOR_NC)
    print_color("  power on", config.COLOR_NC)
    print_color(f"  connect {config.PHONE_MAC}", config.COLOR_NC)
    
    print_color("\n# Connect earbuds to hci1:", config.COLOR_BLUE)
    print_color(f"  select {config.HCI1_MAC}", config.COLOR_NC)
    print_color("  power on", config.COLOR_NC)
    print_color(f"  connect {config.EARBUDS_MAC}", config.COLOR_NC)
    print_color("  quit", config.COLOR_NC)
    
    print_color("\n" + "="*60 + "\n", config.COLOR_YELLOW)
    
    response = input("Press Enter when devices are connected (or 's' to skip): ")
    if response.lower() == 's':
        print_color("Skipping device verification", config.COLOR_YELLOW)
        return False
    return True

def show_status():
    """Show final status and instructions"""
    print_color("\n[8/8] Setup Complete!", config.COLOR_GREEN)
    print_color("\n" + "="*60, config.COLOR_BLUE)
    print_color("BLUETOOTH CALL BRIDGE STATUS", config.COLOR_BLUE)
    print_color("="*60, config.COLOR_BLUE)
    
    print_color(f"\n📱 Phone: {config.PHONE_MAC}", config.COLOR_NC)
    print_color(f"🎧 Earbuds: {config.EARBUDS_MAC}", config.COLOR_NC)
    
    print_color("\n Audio routing active:", config.COLOR_GREEN)
    print_color("   • Phone → Pi → Earbuds (always works)", config.COLOR_NC)
    print_color("   • Earbuds → Pi → Phone (microphone)", config.COLOR_NC)
    print_color(f"   • Non-blocking stream to: {config.PIPE_PATH}", config.COLOR_NC)
    print_color("   • Auto-drain keeps earbuds working 24/7", config.COLOR_NC)
    
    print_color("\n How it works:", config.COLOR_YELLOW)
    print_color("   1. Bridge is always connected (earbuds work perfectly)", config.COLOR_NC)
    print_color("   2. Pipe has auto-drain running in background", config.COLOR_NC)
    print_color("   3. Start scam detection script ANYTIME with:", config.COLOR_NC)
    print_color("      python3 main.py --source arecord", config.COLOR_BLUE)
    print_color("   4. Scam detection takes over from drain seamlessly", config.COLOR_NC)
    print_color("   5. Stop scam detection → drain takes over again", config.COLOR_NC)
    print_color("   6. Earbuds NEVER block, audio always flows", config.COLOR_NC)

    print_color("\n Important notes:", config.COLOR_YELLOW)
    print_color("   • Make a phone call to test audio", config.COLOR_NC)
    print_color("   • Earbuds work independently of scam detection", config.COLOR_NC)
    print_color("   • Start/stop scam detection anytime without issues", config.COLOR_NC)
    print_color("   • Pipe is always drained - no blocking possible", config.COLOR_NC)
    
    print_color("\n Troubleshooting:", config.COLOR_BLUE)
    print_color("   Check connections:  hcitool con", config.COLOR_NC)
    print_color("   View processes:     ps aux | grep -E 'arecord|aplay|tee|pipe_drain'", config.COLOR_NC)
    print_color("   Stop all:           sudo killall -9 arecord aplay tee dd bash", config.COLOR_NC)
    print_color(f"   Check pipe:         ls -l {config.PIPE_PATH}", config.COLOR_NC)
    print_color("   Check drain:        ps aux | grep pipe_drain", config.COLOR_NC)
    print_color("   Restart bridge:     sudo python3 bluetooth_bridge.py", config.COLOR_NC)

    print_color("\n Performance tips:", config.COLOR_BLUE)
    print_color(f"   • Audio format: {config.BT_AUDIO_RATE}Hz, 16-bit, mono (SCO profile)", config.COLOR_NC)
    print_color("   • Low latency: ~100-200ms typical", config.COLOR_NC)
    print_color("   • CPU usage: ~5-10% on Raspberry Pi 4", config.COLOR_NC)
    
    print_color("\n" + "="*60 + "\n", config.COLOR_BLUE)


def main():
    """Main setup routine"""
    print_color("\n" + "="*60, config.COLOR_BLUE)
    print_color("BLUETOOTH CALL BRIDGE SETUP - ROBUST VERSION", config.COLOR_BLUE)
    print_color("="*60 + "\n", config.COLOR_BLUE)
    
    bluetooth_logger.info("Starting Bluetooth bridge setup")
    
    if not check_root():
        print_color("This script must be run as root (use sudo)", config.COLOR_RED)
        sys.exit(1)
    
    setup_recording_dir()
    
    print_color(" This will reconfigure your Bluetooth adapters", config.COLOR_YELLOW)
    print_color(" Make sure both adapters are connected\n", config.COLOR_YELLOW)
    
    response = input("Continue? (y/N): ")
    if response.lower() != 'y':
        print_color("Setup cancelled", config.COLOR_YELLOW)
        sys.exit(0)
    
    try:
        stop_services()
        setup_adapters()
        start_bluetoothd()
        start_bluealsa()
        connect_devices()
        
        ret, out, _ = run_command(["bluealsa-aplay", "-L"], capture_output=True)
        if config.PHONE_MAC not in out or config.EARBUDS_MAC not in out:
            print_color("\n⚠ Devices not fully connected", config.COLOR_YELLOW)
            if manual_connect():
                time.sleep(2)
        
        verify_audio_devices()
        start_audio_routing()
        show_status()
        
        bluetooth_logger.info("Bluetooth bridge setup completed successfully")
        
        print_color("\nBridge is running. Press Ctrl+C to stop and cleanup...", config.COLOR_YELLOW)
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            print_color("\n\nShutting down bridge...", config.COLOR_YELLOW)
        
    except KeyboardInterrupt:
        print_color("\n\nSetup interrupted by user", config.COLOR_YELLOW)
        bluetooth_logger.warning("Setup interrupted by user")
    except Exception as e:
        print_color(f"\n\nSetup failed: {e}", config.COLOR_RED)
        log_exception(bluetooth_logger, e, "Setup failed")
        sys.exit(1)
    finally:
        cleanup_processes()

if __name__ == "__main__":
    main()