import os
import sys
import time
import struct
import json
from datetime import datetime

# -------------------------------------------------------------------------
# Meridian 59 Newsgroup ID (NID) & Protocol Constants
# -------------------------------------------------------------------------
NID_GAME = 3
NID_JUSTICAR = 4
NID_ADVENTURE = 5
NID_EVENT_SCHEDULE = 6
NID_ANNOUNCEMENTS = 9
NID_TOS_HALL = 20

# Protocol Op-Codes (proto.h)
BP_REQ_ARTICLES  = 0x55  # 85  - Request index list of articles
BP_ARTICLES      = 0xB5  # 181 - Server response containing article headers
BP_REQ_ARTICLE   = 0x56  # 86  - Request full text for single article_num
BP_ARTICLE       = 0xB6  # 182 - Server response containing full text body
BP_POST_ARTICLE  = 0x57  # 87  - Post or reply to news globe

# Newsgroup Names Mapping
NEWSGROUP_NAMES = {
    NID_ANNOUNCEMENTS: "Designers' News",
    NID_TOS_HALL: "General News (Adventurer Hall)",
    NID_GAME: "Game Updates & Maintenance",
    NID_ADVENTURE: "Tales of Adventure (Bards)",
    NID_JUSTICAR: "Justicar & Court News",
    NID_EVENT_SCHEDULE: "Event Schedule & Community"
}

# Room ID -> News Globe Mapping
NEWS_GLOBE_MAP = {
    # Designers' / Announcements News (nid = 9)
    "RID_TOS_INN": [
        {"nid": NID_ANNOUNCEMENTS, "name": "Designers' News", "type": "Announcements", "isPublicWrite": False},
        {"nid": NID_GAME, "name": "Game News", "type": "Game Updates", "isPublicWrite": False}
    ],
    "RID_BAR_INN": [{"nid": NID_ANNOUNCEMENTS, "name": "Designers' News", "type": "Announcements", "isPublicWrite": False}],
    "RID_MAR_INN": [{"nid": NID_ANNOUNCEMENTS, "name": "Designers' News", "type": "Announcements", "isPublicWrite": False}],
    "RID_JAS_INN": [{"nid": NID_ANNOUNCEMENTS, "name": "Designers' News", "type": "Announcements", "isPublicWrite": False}],
    "RID_COR_INN": [{"nid": NID_ANNOUNCEMENTS, "name": "Designers' News", "type": "Announcements", "isPublicWrite": False}],
    "RID_KOC_INN": [{"nid": NID_ANNOUNCEMENTS, "name": "Designers' News", "type": "Announcements", "isPublicWrite": False}],
    "RID_NEWB1":   [{"nid": NID_ANNOUNCEMENTS, "name": "Designers' News", "type": "Announcements", "isPublicWrite": False}],

    # General News (nid = 20)
    "RID_TOS_HALL": [{"nid": NID_TOS_HALL, "name": "General News", "type": "Adventurer Hall", "isPublicWrite": True}],
    "RID_BAR_HALL": [{"nid": NID_TOS_HALL, "name": "General News", "type": "Adventurer Hall", "isPublicWrite": True}],
    "RID_MAR_HALL": [{"nid": NID_TOS_HALL, "name": "General News", "type": "Adventurer Hall", "isPublicWrite": True}],
    "RID_JAS_HALL": [{"nid": NID_TOS_HALL, "name": "General News", "type": "Adventurer Hall", "isPublicWrite": True}],
    "RID_COR_HALL": [
        {"nid": NID_TOS_HALL, "name": "General News", "type": "Adventurer Hall", "isPublicWrite": True},
        {"nid": NID_ADVENTURE, "name": "Tales of Adventure", "type": "Bardic Tales", "isPublicWrite": True}
    ],
    "RID_KOC_HALL_OF_HEROES": [{"nid": NID_TOS_HALL, "name": "General News", "type": "Adventurer Hall", "isPublicWrite": True}],

    # Specialized Globes
    "RID_JAS_TAVERN": [{"nid": NID_ADVENTURE, "name": "Tales of Adventure", "type": "Bardic Tales", "isPublicWrite": True}],
    "RID_BAR_COURT":  [{"nid": NID_JUSTICAR, "name": "Justicar Court News", "type": "Legal / Court", "isPublicWrite": True}],
    "RID_BAZMANS_ROOM": [{"nid": NID_JUSTICAR, "name": "Justicar Court News", "type": "Legal / Court", "isPublicWrite": True}],
    "RID_MAR_ELDER":  [{"nid": NID_EVENT_SCHEDULE, "name": "Event Schedule", "type": "Community Events", "isPublicWrite": True}],
}

# -------------------------------------------------------------------------
# Settings Directory Helper
# -------------------------------------------------------------------------
def get_settings_dir():
    settings_dir = os.path.join(os.path.abspath("."), "settings")
    if not os.path.exists(settings_dir):
        os.makedirs(settings_dir, exist_ok=True)
    return settings_dir

def get_news_db_path():
    return os.path.join(get_settings_dir(), "m59_news_database.json")

# -------------------------------------------------------------------------
# Database Storage Functions (settings/m59_news_database.json)
# -------------------------------------------------------------------------
def load_news_database():
    path = get_news_db_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[NEWS DB] Error reading {path}: {e}")
    return seed_news_database()

def save_news_database(db):
    path = get_news_db_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2)
    except Exception as e:
        print(f"[NEWS DB] Error saving {path}: {e}")

def seed_news_database():
    db = {
        "last_synced_room": "RID_JAS_HALL",
        "last_synced_at": datetime.now().isoformat(),
        "globes": {
            str(NID_ANNOUNCEMENTS): {
                "nid": NID_ANNOUNCEMENTS,
                "name": "Designers' News",
                "type": "Announcements",
                "isPublicWrite": False,
                "last_updated": datetime.now().isoformat(),
                "articles": [
                    {
                        "id": 105,
                        "author": "Zaphod",
                        "date": "2026-08-25 18:40",
                        "title": "Patch Notes: Server Performance & Room Data Sync",
                        "body": "Greetings Adventurers!\n\nToday's server patch includes critical performance enhancements for global room data indexer and spell progression formulas.\n\nSummary of Changes:\n- Optimized room transition packet handling.\n- Updated news globe proximity checks for all Inn and Adventurer Hall locations.\n- Balanced Kraanan and Qor spell school requirements.\n\nSafe travels in Meridian!",
                        "read": False
                    },
                    {
                        "id": 104,
                        "author": "Psyche",
                        "date": "2026-08-18 12:15",
                        "title": "Community Tournament Announcement: Jasper Arena",
                        "body": "Hear ye, hear ye!\n\nThe monthly PvP Tournament will take place at the Jasper Arena this Saturday at 20:00 UTC!\n\nRules:\n1. Standard 1v1 Elimination Bracket.\n2. No wand spam or external hacks.\n3. Prizes include 500,000 Shillings and rare enchanted scimitars!\n\nSign up with the Justicar in Barloque before Friday.",
                        "read": True
                    },
                    {
                        "id": 103,
                        "author": "Blakston Admin",
                        "date": "2026-08-01 09:00",
                        "title": "Welcome to Meridian 59 Companion v3.1",
                        "body": "Welcome to the updated Companion Engine!\n\nYou can now sync news globes directly when standing in any Inn, Guild Hall, or Adventurer's Hall across the realm.",
                        "read": True
                    }
                ]
            },
            str(NID_TOS_HALL): {
                "nid": NID_TOS_HALL,
                "name": "General News",
                "type": "Adventurer Hall",
                "isPublicWrite": True,
                "last_updated": datetime.now().isoformat(),
                "articles": [
                    {
                        "id": 240,
                        "author": "Gwen",
                        "date": "2026-08-26 14:22",
                        "title": "Looking for Orc Cave Raid Group",
                        "body": "Seeking 3 experienced warriors and a Shal'ille healer for an Orc Cave 6 excursion tonight.\n\nMeeting at Jasper North gate at 21:00 game time. Split loot evenly.",
                        "read": False
                    },
                    {
                        "id": 239,
                        "author": "Balthazar",
                        "date": "2026-08-24 19:05",
                        "title": "WTB Sol's Ring & Dark Angel Wings",
                        "body": "Paying top shilling for pristine Sol's Rings or Dark Angel Wings.\n\nSend tell or whisper to Balthazar in Tos!",
                        "read": False
                    },
                    {
                        "id": 238,
                        "author": "Elminster",
                        "date": "2026-08-20 11:30",
                        "title": "Guild Alliance Meeting - Barloque Council",
                        "body": "All guild leaders are invited to the Barloque Councilors' Chamber to discuss neutral zone territorial boundaries.",
                        "read": True
                    }
                ]
            },
            str(NID_ADVENTURE): {
                "nid": NID_ADVENTURE,
                "name": "Tales of Adventure",
                "type": "Bardic Tales",
                "isPublicWrite": True,
                "last_updated": datetime.now().isoformat(),
                "articles": [
                    {
                        "id": 501,
                        "author": "Bard Robin",
                        "date": "2026-08-15 22:10",
                        "title": "The Tale of the Crimson Fiend",
                        "body": "Deep within the shadowed crypts of Tos,\nWhere ancient spirits weep and toss,\nA warrior stood with scimitar bright,\nAnd fought the fiend through endless night...",
                        "read": True
                    }
                ]
            }
        }
    }
    save_news_database(db)
    return db

# -------------------------------------------------------------------------
# Packet Builders for Wire Protocol
# -------------------------------------------------------------------------
def build_bp_req_articles(nid: int) -> bytes:
    """BP_REQ_ARTICLES (0x55) - Request index list of articles for newsgroup NID."""
    return struct.pack("<BH", BP_REQ_ARTICLES, nid)

def build_bp_req_article(nid: int, article_num: int) -> bytes:
    """BP_REQ_ARTICLE (0x56) - Request body text for a specific article_num."""
    return struct.pack("<BHI", BP_REQ_ARTICLE, nid, article_num)

def build_bp_post_article(nid: int, subject: str, body: str) -> bytes:
    """
    BP_POST_ARTICLE (0x57) - Post a new message or reply to a news globe.
    Constraints:
      - Subject max 40 chars.
      - Body max ~3000 chars.
    """
    if nid == NID_ANNOUNCEMENTS:
        raise PermissionError("Posting/Replying to Designers' News (NID 9) is disabled for general players.")

    subj_bytes = subject[:40].encode("latin-1", errors="replace") + b"\x00"
    body_bytes = body[:3000].encode("latin-1", errors="replace") + b"\x00"

    pkt = bytearray([BP_POST_ARTICLE])
    pkt += struct.pack("<H", nid)
    pkt += subj_bytes
    pkt += body_bytes
    return bytes(pkt)

def build_reply_article(nid: int, original_subject: str, reply_body: str) -> bytes:
    """
    Builds a reply packet for a news globe.
    In Meridian 59 engine, reply is posting with "Re: <Original Subject>".
    """
    subject = original_subject if original_subject.startswith("Re: ") else f"Re: {original_subject}"
    return build_bp_post_article(nid, subject, reply_body)

def format_hex(data_bytes: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data_bytes)

# -------------------------------------------------------------------------
# Local Article Management Helpers
# -------------------------------------------------------------------------
def add_article_to_cache(nid: int, author: str, title: str, body: str) -> dict:
    """Adds a new article to the local JSON database in settings/m59_news_database.json."""
    db = load_news_database()
    target_nid = str(nid)

    if target_nid not in db["globes"]:
        db["globes"][target_nid] = {
            "nid": nid,
            "name": NEWSGROUP_NAMES.get(nid, f"Globe #{nid}"),
            "type": "General",
            "isPublicWrite": nid != NID_ANNOUNCEMENTS,
            "last_updated": datetime.now().isoformat(),
            "articles": []
        }

    globe = db["globes"][target_nid]
    articles = globe["articles"]
    new_id = (articles[0]["id"] if articles else 100) + 1
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    new_article = {
        "id": new_id,
        "author": author,
        "date": now_str,
        "title": title,
        "body": body,
        "read": False
    }

    articles.insert(0, new_article)
    globe["last_updated"] = datetime.now().isoformat()
    save_news_database(db)
    return new_article
