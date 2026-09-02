import os
import sys
import time
import struct

# Ensure parent directory is in sys.path so we can import m59_* modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Optional Win32/Process imports
find_meridian_processes = None
GPSManager = None

try:
    from m59_gps import GPSManager
except ImportError:
    pass

try:
    from m59_bridge import find_meridian_processes
except ImportError:
    pass

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

def build_bp_req_articles(nid):
    """
    Constructs BP_REQ_ARTICLES (0x55) packet buffer.
    Payload: BYTE (0x55) + WORD (nid - uint16 LE)
    """
    return struct.pack("<BH", BP_REQ_ARTICLES, nid)

def build_bp_req_article(nid, article_num):
    """
    Constructs BP_REQ_ARTICLE (0x56) packet buffer.
    Payload: BYTE (0x56) + WORD (nid - uint16 LE) + DWORD (article_num - uint32 LE)
    """
    return struct.pack("<BHI", BP_REQ_ARTICLE, nid, article_num)

def format_hex(data_bytes):
    return " ".join(f"{b:02X}" for b in data_bytes)

def print_header():
    print("=" * 65)
    print("      MERIDIAN 59 NEWS GLOBE TESTER & PACKET GENERATOR     ")
    print("=" * 65)

def detect_current_room():
    """Detects game window and matches room name against world dataset."""
    if not find_meridian_processes:
        print("[PROCESS] Windows Process Attachment not available in this environment.")
        return None, None

    try:
        procs = find_meridian_processes()
        if not procs:
            print("[PROCESS] No active 'meridian.exe' window detected.")
            return None, None

        proc = procs[0]
        hwnd = proc['hwnd']
        title = proc['title']
        print(f"[PROCESS] Connected to HWND {hwnd} | Title: '{title}'")

        room_title = title
        if "---" in title:
            room_title = title.split("---")[-1].strip()

        gps = GPSManager() if GPSManager else None
        matched_rid = None
        matched_room = None

        if gps and gps.dataset:
            for rid, info in gps.dataset.items():
                if info.get('name', '').lower() in room_title.lower() or room_title.lower() in info.get('name', '').lower():
                    matched_rid = rid
                    matched_room = info
                    break

        return matched_rid, room_title
    except Exception as e:
        print(f"[PROCESS ERROR] Could not detect room: {e}")
        return None, None

def run_cli_test():
    print_header()
    gps = GPSManager() if GPSManager else None

    rid, room_name = detect_current_room()

    if rid:
        print(f"\n[LOCATION DETECTED] Room ID: {rid} ('{room_name}')")
    else:
        print(f"\n[LOCATION UNKNOWN] Could not auto-detect room from active window.")
        print("Available test rooms with News Globes:")
        test_rids = list(NEWS_GLOBE_MAP.keys())
        for idx, trid in enumerate(test_rids, 1):
            rname = gps.dataset.get(trid, {}).get('name', trid) if (gps and gps.dataset) else trid
            print(f"  {idx}. {rname} ({trid})")

        choice = input("\nSelect a Room Number to test (1-15, or press Enter for #1): ").strip()
        if not choice:
            choice = "1"
            
        if choice.isdigit():
            c_idx = int(choice) - 1
            if 0 <= c_idx < len(test_rids):
                rid = test_rids[c_idx]
                room_name = gps.dataset.get(rid, {}).get('name', rid) if (gps and gps.dataset) else rid
            else:
                print("Invalid selection.")
                return
        else:
            print("Invalid input.")
            return

    # Check for News Globes in current room
    globes = NEWS_GLOBE_MAP.get(rid, [])

    print("\n" + "-" * 65)
    print(f"NEWS GLOBE PROXIMITY CHECK FOR: {room_name} ({rid})")
    print("-" * 65)

    if not globes:
        print("❌ NO NEWS GLOBE IN THIS ROOM.")
        print("The server ContainsNewsID() check will reject requests from this room.")
        print("Move your character to an Inn or Adventurer Hall to read news!")
        return

    print(f"✅ FOUND {len(globes)} ACCESSIBLE NEWS GLOBE(S):\n")

    for i, g in enumerate(globes, 1):
        nid = g['nid']
        g_name = g['name']
        g_type = g['type']

        req_articles_pkt = build_bp_req_articles(nid)
        sample_req_article_pkt = build_bp_req_article(nid, 1001)

        print(f"  [{i}] {g_name} (Type: {g_type})")
        print(f"      • Newsgroup ID (nid) : {nid}")
        print(f"      • Fetch Index Packet : BP_REQ_ARTICLES (0x55)")
        print(f"        Hex Payload        : {format_hex(req_articles_pkt)}")
        print(f"      • Fetch Post Packet  : BP_REQ_ARTICLE (0x56)")
        print(f"        Hex Sample (Post 1001) : {format_hex(sample_req_article_pkt)}")
        print()

    print("=" * 65)
    print("FRIDA SYNC INTEGRATION SPEC:")
    print("  1. Inject BP_REQ_ARTICLES payload when entering room.")
    print("  2. Intercept BP_ARTICLES (181 / 0xB5) from server response.")
    print("  3. Compare article_num headers against local sqlite/cache.")
    print("  4. Inject BP_REQ_ARTICLE (86 / 0x56) for missing post IDs.")
    print("  5. Intercept BP_ARTICLE (182 / 0xB6) -> Save full text.")
    print("=" * 65)

if __name__ == "__main__":
    run_cli_test()
