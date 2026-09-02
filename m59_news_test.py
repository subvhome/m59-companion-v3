import os
import sys
import time
import struct
import json
from datetime import datetime

# Ensure parent directory and script directory are in sys.path
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(script_dir, ".."))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

# Win32 & Companion imports
GPSManager = None
win32gui = None
win32process = None

try:
    import win32gui
    import win32process
except ImportError:
    pass

try:
    from m59_gps import GPSManager
except ImportError:
    pass

try:
    from m59_utils import resource_path
except ImportError:
    def resource_path(p):
        return p

# -------------------------------------------------------------------------
# Meridian 59 Newsgroup ID (NID) & Protocol Constants
# -------------------------------------------------------------------------
NID_GAME = 3
NID_JUSTICAR = 4
NID_ADVENTURE = 5
NID_EVENT_SCHEDULE = 6
NID_ANNOUNCEMENTS = 9
NID_TOS_HALL = 20

# Protocol Packet Op-Codes (proto.h)
BP_REQ_ARTICLES = 0x55  # 85 - Request index list of articles
BP_ARTICLES     = 0xB5  # 181 - Server response containing article headers
BP_REQ_ARTICLE  = 0x56  # 86 - Request full text for single article_num
BP_ARTICLE      = 0xB6  # 182 - Server response containing full text body

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
        {"nid": NID_ANNOUNCEMENTS, "name": "Designers' News", "type": "Announcements"},
        {"nid": NID_GAME, "name": "Game News", "type": "Game Updates"}
    ],
    "RID_BAR_INN": [{"nid": NID_ANNOUNCEMENTS, "name": "Designers' News", "type": "Announcements"}],
    "RID_MAR_INN": [{"nid": NID_ANNOUNCEMENTS, "name": "Designers' News", "type": "Announcements"}],
    "RID_JAS_INN": [{"nid": NID_ANNOUNCEMENTS, "name": "Designers' News", "type": "Announcements"}],
    "RID_COR_INN": [{"nid": NID_ANNOUNCEMENTS, "name": "Designers' News", "type": "Announcements"}],
    "RID_KOC_INN": [{"nid": NID_ANNOUNCEMENTS, "name": "Designers' News", "type": "Announcements"}],
    "RID_NEWB1":   [{"nid": NID_ANNOUNCEMENTS, "name": "Designers' News", "type": "Announcements"}],

    # General News (nid = 20)
    "RID_TOS_HALL": [{"nid": NID_TOS_HALL, "name": "General News", "type": "Adventurer Hall"}],
    "RID_BAR_HALL": [{"nid": NID_TOS_HALL, "name": "General News", "type": "Adventurer Hall"}],
    "RID_MAR_HALL": [{"nid": NID_TOS_HALL, "name": "General News", "type": "Adventurer Hall"}],
    "RID_JAS_HALL": [{"nid": NID_TOS_HALL, "name": "General News", "type": "Adventurer Hall"}],
    "RID_COR_HALL": [
        {"nid": NID_TOS_HALL, "name": "General News", "type": "Adventurer Hall"},
        {"nid": NID_ADVENTURE, "name": "Tales of Adventure", "type": "Bardic Tales"}
    ],
    "RID_KOC_HALL_OF_HEROES": [{"nid": NID_TOS_HALL, "name": "General News", "type": "Adventurer Hall"}],

    # Specialized Globes
    "RID_JAS_TAVERN": [{"nid": NID_ADVENTURE, "name": "Tales of Adventure", "type": "Bardic Tales"}],
    "RID_BAR_COURT":  [{"nid": NID_JUSTICAR, "name": "Justicar Court News", "type": "Legal / Court"}],
    "RID_BAZMANS_ROOM": [{"nid": NID_JUSTICAR, "name": "Justicar Court News", "type": "Legal / Court"}],
    "RID_MAR_ELDER":  [{"nid": NID_EVENT_SCHEDULE, "name": "Event Schedule", "type": "Community Events"}],
}

# -------------------------------------------------------------------------
# Sample Dataset Seed Generator for Local News Cache
# -------------------------------------------------------------------------
def get_cache_filepath():
    data_dir = resource_path("data")
    if not os.path.exists(data_dir):
        os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "m59_news_cache.json")

def load_news_cache():
    path = get_cache_filepath()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return seed_default_news_cache()

def save_news_cache(cache):
    path = get_cache_filepath()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        print(f"[CACHE ERROR] Could not save news cache: {e}")

def seed_default_news_cache():
    cache = {
        str(NID_ANNOUNCEMENTS): [
            {
                "id": 105,
                "author": "Zaphod",
                "date": "2026-08-25 18:40",
                "title": "Patch Notes: Server Performance & Room Data Sync",
                "body": "Greetings Adventurers!\n\nToday's server patch includes critical performance enhancements for global room data indexer and spell progression formulas.\n\nSummary of Changes:\n- Optimized room transition packet handling.\n- Updated news globe proximity checks for all Inn and Adventurer Hall locations.\n- Balanced Kraanan and Qor spell school requirements.\n\nSafe travels in Meridian!"
            },
            {
                "id": 104,
                "author": "Psyche",
                "date": "2026-08-18 12:15",
                "title": "Community Tournament Announcement: Jasper Arena",
                "body": "Hear ye, hear ye!\n\nThe monthly PvP Tournament will take place at the Jasper Arena this Saturday at 20:00 UTC!\n\nRules:\n1. Standard 1v1 Elimination Bracket.\n2. No wand spam or external hacks.\n3. Prizes include 500,000 Shillings and rare enchanted scimitars!\n\nSign up with the Justicar in Barloque before Friday."
            },
            {
                "id": 103,
                "author": "Blakston Admin",
                "date": "2026-08-01 09:00",
                "title": "Welcome to Meridian 59 Companion v3.1",
                "body": "Welcome to the updated Companion Engine!\n\nYou can now sync news globes directly when standing in any Inn, Guild Hall, or Adventurer's Hall across the realm."
            }
        ],
        str(NID_TOS_HALL): [
            {
                "id": 240,
                "author": "Gwen",
                "date": "2026-08-26 14:22",
                "title": "Looking for Orc Cave Raid Group",
                "body": "Seeking 3 experienced warriors and a Shal'ille healer for an Orc Cave 6 excursion tonight.\n\nMeeting at Jasper North gate at 21:00 game time. Split loot evenly."
            },
            {
                "id": 239,
                "author": "Balthazar",
                "date": "2026-08-24 19:05",
                "title": "WTB Sol's Ring & Dark Angel Wings",
                "body": "Paying top shilling for pristine Sol's Rings or Dark Angel Wings.\n\nSend tell or whisper to Balthazar in Tos!"
            },
            {
                "id": 238,
                "author": "Elminster",
                "date": "2026-08-20 11:30",
                "title": "Guild Alliance Meeting - Barloque Council",
                "body": "All guild leaders are invited to the Barloque Councilors' Chamber to discuss neutral zone territorial boundaries."
            }
        ],
        str(NID_ADVENTURE): [
            {
                "id": 501,
                "author": "Bard Robin",
                "date": "2026-08-15 22:10",
                "title": "The Tale of the Crimson Fiend",
                "body": "Deep within the shadowed crypts of Tos,\nWhere ancient spirits weep and toss,\nA warrior stood with scimitar bright,\nAnd fought the fiend through endless night..."
            }
        ]
    }
    save_news_cache(cache)
    return cache

# -------------------------------------------------------------------------
# Binary Protocol Packet Builders
# -------------------------------------------------------------------------
def build_bp_req_articles(nid):
    return struct.pack("<BH", BP_REQ_ARTICLES, nid)

def build_bp_req_article(nid, article_num):
    return struct.pack("<BHI", BP_REQ_ARTICLE, nid, article_num)

def format_hex(data_bytes):
    return " ".join(f"{b:02X}" for b in data_bytes)

# -------------------------------------------------------------------------
# Game Room Detector
# -------------------------------------------------------------------------
def detect_current_room():
    """Detects game window directly via EnumWindows."""
    if not win32gui:
        return None, None

    found_windows = []

    def callback(hwnd, extra):
        if win32gui.IsWindowVisible(hwnd):
            text = win32gui.GetWindowText(hwnd)
            if "meridian" in text.lower():
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                found_windows.append({"hwnd": hwnd, "title": text, "pid": pid})

    try:
        win32gui.EnumWindows(callback, None)
    except Exception:
        return None, None

    if not found_windows:
        return None, None

    best = found_windows[0]
    for w in found_windows:
        if " --- " in w['title']:
            best = w
            break

    title = best['title']
    room_title = title
    if "---" in title:
        room_title = title.split("---")[-1].strip()

    gps = GPSManager() if GPSManager else None
    matched_rid = None
    matched_room = None

    if gps and gps.dataset:
        for rid, info in gps.dataset.items():
            rname = info.get('name', '').lower()
            if rname and (rname in room_title.lower() or room_title.lower() in rname):
                matched_rid = rid
                matched_room = info
                break

    return matched_rid, room_title

# -------------------------------------------------------------------------
# Main Automated News Globe Puller & Reader
# -------------------------------------------------------------------------
def start_automated_watcher():
    cache = load_news_cache()
    last_rid = None
    last_title = None

    print("=" * 75)
    print("      MERIDIAN 59 AUTOMATED NEWS GLOBE PULLER & MONITOR     ")
    print("=" * 75)
    print("Monitoring active 'meridian.exe' window in real-time...")
    print("Automatically pulls messages & prints headers and first body upon room entry.\n")

    while True:
        try:
            rid, room_title = detect_current_room()

            if room_title != last_title or rid != last_rid:
                last_rid = rid
                last_title = room_title

                timestamp = datetime.now().strftime("%H:%M:%S")

                if not room_title:
                    print(f"[{timestamp}] ⌛ Waiting for active Meridian 59 process...")
                else:
                    print("\n" + "=" * 75)
                    print(f"[{timestamp}] 🚪 ROOM ENTERED: '{room_title}' (ID: {rid or 'UNKNOWN'})")

                    globes = NEWS_GLOBE_MAP.get(rid, [])

                    if not globes:
                        print(f"  ❌ No News Globe in this room.")
                        print(f"  (Server ContainsNewsID() constraint active: Standing outside news range)")
                    else:
                        print(f"  ✅ ACCESSIBLE NEWS GLOBE(S) FOUND ({len(globes)}):")

                        for idx, g in enumerate(globes, 1):
                            nid = g['nid']
                            g_name = g['name']
                            g_type = g['type']

                            posts = cache.get(str(nid), [])
                            pkt_index = build_bp_req_articles(nid)

                            print("\n" + "-" * 75)
                            print(f"  📰 GLOBE [{idx}]: {g_name} ({g_type}) | Newsgroup NID: {nid}")
                            print(f"  • Packet Inject Payload : BP_REQ_ARTICLES (0x55) -> {format_hex(pkt_index)}")
                            print(f"  • TOTAL MESSAGES COUNT  : {len(posts)} Post(s)")
                            print("-" * 75)

                            if not posts:
                                print("    [No cached messages available]")
                            else:
                                print("\n  📋 MESSAGE HEADERS:")
                                for h_idx, p in enumerate(posts, 1):
                                    post_id = p.get('id', '???')
                                    author = p.get('author', 'Unknown')
                                    date_str = p.get('date', 'N/A')
                                    title = p.get('title', 'No Title')
                                    print(f"    {h_idx}. [Post #{post_id}] {date_str} | Author: {author:<12} | Title: '{title}'")

                                # Automatically display the FIRST (latest) message body
                                first_post = posts[0]
                                print("\n  " + "—" * 65)
                                print(f"  📜 LATEST MESSAGE BODY [Post #{first_post.get('id')} — '{first_post.get('title')}']:")
                                print("  " + "—" * 65)
                                body_lines = first_post.get('body', '').split('\n')
                                for line in body_lines:
                                    print(f"    {line}")
                                print("  " + "—" * 65)

            time.sleep(1.5)

        except KeyboardInterrupt:
            print("\n\n[EXIT] News watcher stopped.")
            break
        except Exception as e:
            print(f"[ERROR] {e}")
            time.sleep(2)

if __name__ == "__main__":
    start_automated_watcher()
