#!/usr/bin/env python3
import firebase_admin
from firebase_admin import credentials, messaging
import sys
import json
from datetime import datetime

# Load configuration
with open('config.json', 'r') as f:
    config = json.load(f)

# Initialize Firebase Admin SDK (only once)
cred = credentials.Certificate(config['service_account_key'])
firebase_admin.initialize_app(cred)

def get_scam_category(score):
    """Determine scam category based on score"""
    if 1 <= score <= 3:
        return {
            "category": "Not a Scam",
            "emoji": "✅",
            "color": "#4CAF50",  # Green
            "priority": "default",
            "vibration": [100, 100]  # Short vibration
        }
    elif 4 <= score <= 7:
        return {
            "category": "Possible Scam",
            "emoji": "⚠",
            "color": "#FFC107",  # Yellow/Orange
            "priority": "high",
            "vibration": [200, 200, 200, 200]  # Medium vibration
        }
    elif 8 <= score <= 10:
        return {
            "category": "Wifey Material",
            "emoji": "🚨",
            "color": "#F44336",  # Red
            "priority": "max",
            "vibration": [500, 200, 500, 200, 500]  # Strong vibration pattern
        }
    else:
        return {
            "category": "Unknown",
            "emoji": "❓",
            "color": "#9E9E9E",  # Gray
            "priority": "default",
            "vibration": [100, 100]
        }

def send_scam_alert(phone_number, scam_score, response_text):
    """
    Send scam alert notification to Android device

    Args:
        phone_number: Phone number that called
        scam_score: Scam score (1-10)
        response_text: Description of the scam
    """

    # Get category info based on score
    category_info = get_scam_category(scam_score)

    # Create title with emoji and category
    title = f"{category_info['emoji']} {category_info['category']}"

    # Create body with score
    body = f"{response_text}\nScore: {scam_score}/10"

    # Create message with both notification and data payload
    message = messaging.Message(
        notification=messaging.Notification(
            title=title,
            body=body
        ),
        data={
            "phone_number": phone_number,
            "love_score": str(scam_score),
            "response": response_text,
            "category": category_info['category'],
            "timestamp": datetime.now().isoformat()
        },
        token=config['fcm_token'],
        android=messaging.AndroidConfig(
            priority='high',
            notification=messaging.AndroidNotification(
                sound='default',
                priority=category_info['priority'],
                channel_id='scam_alerts',
                color=category_info['color'],
                icon='ic_dialog_alert',
                default_sound=True,
                default_vibrate_timings=False,
                vibrate_timings_millis=category_info['vibration'],
                visibility='public',
                notification_count=1
            )
        )
    )

    try:
        response = messaging.send(message)
        print(f"✓ Successfully sent notification")
        print(f"  Category: {category_info['category']}")
        print(f"  Message ID: {response}")
        print(f"  Phone: {phone_number}")
        print(f"  Score: {scam_score}/10")
        print(f"  Color: {category_info['color']}")
        return True
    except Exception as e:
        print(f"✗ Error sending notification: {e}")
        return False

def send_test_notification():
    """Send a test notification"""
    message = messaging.Message(
        notification=messaging.Notification(
            title="🔔 Test Notification",
            body="Scam Detection system is working!"
        ),
        token=config['fcm_token'],
        android=messaging.AndroidConfig(
            priority='high',
            notification=messaging.AndroidNotification(
                sound='default',
                priority='high',
                color='#2196F3',  # Blue
                default_vibrate_timings=True
            )
        )
    )

    try:
        response = messaging.send(message)
        print(f"✓ Test notification sent successfully!")
        print(f"  Message ID: {response}")
        return True
    except Exception as e:
        print(f"✗ Error: {e}")
        return False

def update_fcm_token(new_token):
    """Update FCM token in config file"""
    config['fcm_token'] = new_token
    with open('config.json', 'w') as f:
        json.dump(config, f, indent=4)
    print(f"✓ FCM token updated in config.json")

def show_examples():
    """Show usage examples"""
    print("\n📱 Scam Detection Notification System")
    print("=" * 50)
    print("\nUsage:")
    print("  python3 send_notification.py test")
    print("  python3 send_notification.py <phone> <score> <message>")
    print("  python3 send_notification.py update-token <new_token>")
    print("\nExamples:")
    print("  # Not a Scam (Green, 1-3)")
    print("  python3 send_notification.py '+1234567890' 2 'Verified business'")
    print("\n  # Possible Scam (Yellow, 4-7)")
    print("  python3 send_notification.py '+1234567890' 6 'Suspicious activity detected'")
    print("\n  # Definitely Scam (Red, 8-10)")
    print("  python3 send_notification.py '+1234567890' 9 'Known scammer - IRS fraud'")
    print("\nScore Ranges:")
    print("  1-3:  ✅ Not a Scam (Green)")
    print("  4-7:  ⚠  Possible Scam (Yellow)")
    print("  8-10: 🚨 Definitely Scam (Red)")
    print()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "test":
            # Test mode
            print("Sending test notification...")
            send_test_notification()
        elif sys.argv[1] == "update-token" and len(sys.argv) > 2:
            # Update FCM token
            update_fcm_token(sys.argv[2])
        elif sys.argv[1] in ["help", "-h", "--help"]:
            # Show help
            show_examples()
        elif len(sys.argv) >= 4:
            # Send scam alert with command line arguments
            phone = sys.argv[1]
            score = int(sys.argv[2])
            response = sys.argv[3]

            # Validate score
            if not 1 <= score <= 10:
                print("✗ Error: Score must be between 1 and 10")
                sys.exit(1)

            send_scam_alert(phone, score, response)
        else:
            show_examples()
    else:
        # Show examples with different scores
        print("Sending example notifications for all categories...\n")

        print("1. Not a Scam (Score: 2)")
        send_scam_alert("+1234567890", 2, "Verified business number")
        print()

        print("2. Possible Scam (Score: 6)")
        send_scam_alert("+1234567890", 6, "Suspicious telemarketing call")
        print()

        print("3. Definitely Scam (Score: 9)")
        send_scam_alert("+1234567890", 9, "Known IRS scam - DO NOT ANSWER")
        print()
