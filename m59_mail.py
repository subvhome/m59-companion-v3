import os
import sys
import time
import struct
import json
from datetime import datetime

# -------------------------------------------------------------------------
# Protocol Op-Codes for Meridian 59 Mail System
# -------------------------------------------------------------------------
BP_REQ_GET_MAIL     = 0x51  # 81 - Request unread mail list from server
BP_SEND_MAIL        = 0x53  # 83 - Send mail packet to recipient object IDs
BP_REQ_LOOKUP_NAMES = 0x54  # 84 - Request 32-bit Object ID lookup for character names
BP_LOOKUP_NAMES     = 0xB7  # 183 - Server response with 32-bit Object ID array

# -------------------------------------------------------------------------
# Settings Directory Helper
# -------------------------------------------------------------------------
def get_settings_dir():
    settings_dir = os.path.join(os.path.abspath("."), "settings")
    if not os.path.exists(settings_dir):
        os.makedirs(settings_dir, exist_ok=True)
    return settings_dir

def get_mail_db_path():
    return os.path.join(get_settings_dir(), "m59_mail_database.json")

# -------------------------------------------------------------------------
# Database Storage Functions (settings/m59_mail_database.json)
# -------------------------------------------------------------------------
def load_mail_database():
    path = get_mail_db_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[MAIL DB] Error reading {path}: {e}")
    return seed_mail_database()

def save_mail_database(db):
    path = get_mail_db_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2)
    except Exception as e:
        print(f"[MAIL DB] Error saving {path}: {e}")

def seed_mail_database():
    db = {
        "last_updated": datetime.now().isoformat(),
        "inbox": [
            {
                "id": 1001,
                "sender": "Elrond",
                "sender_obj_id": 54210,
                "date": "2026-08-27 15:30",
                "subject": "Re: Guild Alliance Meeting",
                "body": "Greetings!\n\nI agree with the terms discussed in Barloque. We will stand united against the Orc Captain invasion.",
                "read": False
            },
            {
                "id": 1000,
                "sender": "Cassandra",
                "sender_obj_id": 12894,
                "date": "2026-08-20 11:00",
                "subject": "Spell Reagents Order",
                "body": "Your order of 50 Red Mushrooms and 20 Emeralds is ready at the Jasper Bank.",
                "read": True
            }
        ],
        "sent": [
            {
                "id": 2001,
                "recipients": ["Elrond"],
                "recipient_obj_ids": [54210],
                "date": "2026-08-27 14:00",
                "subject": "Guild Alliance Meeting",
                "body": "Are you available to discuss neutral zone boundaries this evening?",
                "status": "Delivered"
            }
        ]
    }
    save_mail_database(db)
    return db

# -------------------------------------------------------------------------
# Binary Protocol Packet Builders
# -------------------------------------------------------------------------
def build_bp_req_lookup_names(names: list[str]) -> bytes:
    """
    BP_REQ_LOOKUP_NAMES (0x54) - Lookup 32-bit Object IDs for character names.
    Format:
      [0x54] (1 byte)
      [count] (uint16_t LE)
      [Name1\0][Name2\0]...
    """
    pkt = bytearray([BP_REQ_LOOKUP_NAMES])
    pkt += struct.pack("<H", len(names))
    for name in names:
        pkt += name.encode("latin-1", errors="replace") + b"\x00"
    return bytes(pkt)

def build_bp_send_mail(recipient_ids: list[int], to_display_names: str, subject: str, body: str) -> bytes:
    """
    BP_SEND_MAIL (0x53) - Send mail packet.
    Format:
      [0x53] (1 byte)
      [count] (uint16_t LE)
      [obj_id_1] [obj_id_2] ... (uint32_t LE)
      [Composite Payload String\0]
    
    Composite Payload String Format:
      <To_Display_Names>\n<Subject_Line>\n<Body_Text>\0
    """
    pkt = bytearray([BP_SEND_MAIL])
    pkt += struct.pack("<H", len(recipient_ids))
    for obj_id in recipient_ids:
        pkt += struct.pack("<I", obj_id)

    payload_str = f"{to_display_names}\n{subject}\n{body}\x00"
    pkt += payload_str.encode("latin-1", errors="replace")
    return bytes(pkt)

def build_bp_req_get_mail() -> bytes:
    """BP_REQ_GET_MAIL (0x51) - Request unread mail list from server."""
    return bytes([BP_REQ_GET_MAIL])

def format_hex(data_bytes: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data_bytes)

# -------------------------------------------------------------------------
# Mail Helper Functions
# -------------------------------------------------------------------------
def save_sent_mail(to_names: str, recipient_ids: list[int], subject: str, body: str):
    """Saves a sent mail record to settings/m59_mail_database.json."""
    db = load_mail_database()
    sent_list = db.get("sent", [])
    new_id = (sent_list[0]["id"] if sent_list else 2000) + 1
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    mail_entry = {
        "id": new_id,
        "recipients": [n.strip() for n in to_names.split(",")],
        "recipient_obj_ids": recipient_ids,
        "date": now_str,
        "subject": subject,
        "body": body,
        "status": "Delivered"
    }

    sent_list.insert(0, mail_entry)
    db["sent"] = sent_list
    db["last_updated"] = datetime.now().isoformat()
    save_mail_database(db)
    return mail_entry
