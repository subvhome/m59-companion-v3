import os
import json
import re
from datetime import datetime
from m59_utils import resource_path, get_safe_name, RE_KILL, RE_HIT, RE_MISS

class CombatMonitor:
    def __init__(self, char_name="Unknown"):
        self.char_name = char_name
        self.safe_name = get_safe_name(char_name)
        self.mob_set = self._load_moblist()
        self.kill_book = self._load_kill_book()
        
        # Entities that are NOT monsters but NOT players
        self.whitelist = {"town guard", "corpse", "reflection", "the town guard"}

    def _load_moblist(self):
        """Loads mob names from the second column of moblist.csv."""
        mobs = set()
        csv_path = resource_path("settings/moblist.csv")
        
        if os.path.exists(csv_path):
            try:
                with open(csv_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if "," in line:
                            name = line.split(",")[1].strip().lower().rstrip('"')
                            cleaned_name = ''.join(c for c in name if c.isalnum() or c.isspace() or c == "'" or c == "-")
                            if cleaned_name:
                                mobs.add(cleaned_name)
                                if cleaned_name.startswith("the "): mobs.add(cleaned_name[4:])
                                if cleaned_name.startswith("a "): mobs.add(cleaned_name[2:])
            except Exception as e:
                print(f"COMBAT ERROR: Could not read moblist: {e}")
        
        return mobs

    def _load_kill_book(self):
        """Loads persistent kill counts from JSON with fallback file support."""
        candidates = []
        if self.safe_name and self.safe_name not in ["--", "Unknown"]:
            candidates.append(f"settings/{self.safe_name}_kills.json")
            if self.char_name and self.char_name not in ["--", "Unknown"]:
                candidates.append(f"settings/{self.char_name}_kills.json")
        candidates.extend([
            "settings/Unknown_kills.json",
            "settings/kills.json"
        ])
        if os.path.exists("settings"):
            import glob
            existing = glob.glob("settings/*_kills.json")
            for f in existing:
                clean_f = f.replace("\\", "/")
                if clean_f not in candidates:
                    candidates.append(clean_f)

        for file_path in candidates:
            if os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            if "monsters" in data or "players" in data:
                                return {
                                    "monsters": data.get("monsters", {}),
                                    "players": data.get("players", {}),
                                    "player_kills_history": data.get("player_kills_history", [])
                                }
                            else:
                                return {"monsters": data, "players": {}, "player_kills_history": []}
                except Exception as e:
                    print(f"COMBAT ERROR: Failed reading {file_path}: {e}")
        return {"monsters": {}, "players": {}, "player_kills_history": []}

    def _save_kill_book(self):
        """Saves kill counts to JSON."""
        if not os.path.exists("settings"):
            os.makedirs("settings")
        file_path = f"settings/{self.safe_name}_kills.json"
        try:
            with open(file_path, "w") as f:
                json.dump(self.kill_book, f, indent=4)
        except Exception as e:
            print(f"COMBAT ERROR: Could not save kill book: {e}")

    def process_line(self, line, msg_ts=None, room_name=None):
        """
        Analyzes a line for kills or incoming PK attacks.
        Returns event dict or None.
        """
        clean_line = line.strip()

        # 1. Check for Kills
        kill_match = RE_KILL.match(clean_line)
        if kill_match:
            victim = kill_match.group(1).strip()
            if victim.endswith('.'): victim = victim[:-1]
            victim_lookup = victim.lower()
            
            is_monster = victim_lookup in self.mob_set
            if not is_monster:
                test_name = victim_lookup
                if test_name.startswith("the "): test_name = test_name[4:]
                if test_name.startswith("a "): test_name = test_name[2:]
                is_monster = test_name in self.mob_set

            category = "monsters" if is_monster else "players"
            if category not in self.kill_book:
                self.kill_book[category] = {}
            self.kill_book[category][victim] = self.kill_book[category].get(victim, 0) + 1
            
            pk_record = None
            if category == "players":
                if "player_kills_history" not in self.kill_book or not isinstance(self.kill_book["player_kills_history"], list):
                    self.kill_book["player_kills_history"] = []
                
                now = datetime.now()
                if msg_ts and len(str(msg_ts).strip()) >= 5:
                    ts_raw = str(msg_ts).strip()
                    if len(ts_raw) == 8 and ":" in ts_raw:
                        ts_str = f"{now.strftime('%Y-%m-%d')} {ts_raw}"
                        time_str = ts_raw
                        try:
                            hour_val = int(ts_raw.split(":")[0])
                        except Exception:
                            hour_val = now.hour
                    else:
                        ts_str = ts_raw
                        time_str = ts_raw.split(" ")[-1] if " " in ts_raw else ts_raw
                        try:
                            hour_val = int(time_str.split(":")[0])
                        except Exception:
                            hour_val = now.hour
                else:
                    ts_str = now.strftime("%Y-%m-%d %H:%M:%S")
                    time_str = now.strftime("%H:%M:%S")
                    hour_val = now.hour

                pk_record = {
                    "victim": victim,
                    "timestamp": ts_str,
                    "date": ts_str.split(" ")[0] if " " in ts_str else now.strftime("%Y-%m-%d"),
                    "time": time_str,
                    "hour": hour_val,
                    "day_of_week": now.strftime("%A"),
                    "room": room_name or "Unknown Location"
                }
                self.kill_book["player_kills_history"].append(pk_record)

            self._save_kill_book()
            
            return {
                "type": "KILL",
                "category": category,
                "name": victim,
                "total": self.kill_book[category][victim],
                "pk_record": pk_record
            }

        # 2. Check for Incoming Attacks (PK Detection)
        attacker = None
        hit_match = RE_HIT.match(clean_line)
        if hit_match and " you with " in clean_line:
            attacker = hit_match.group(1).strip()
        else:
            miss_match = RE_MISS.match(clean_line)
            if miss_match:
                attacker = miss_match.group(1).strip()

        if attacker:
            lookup_name = attacker.lower()
            if lookup_name.startswith("the "): lookup_name = lookup_name[4:]
            if lookup_name.startswith("a "): lookup_name = lookup_name[2:]
            
            if lookup_name not in self.mob_set and lookup_name not in self.whitelist:
                return {
                    "type": "PK_ALERT",
                    "name": attacker
                }

        return None

if __name__ == "__main__":
    monitor = CombatMonitor("MF DOOM")
    print("--- M59 Combat Monitor: Logic Test ---")
    test_lines = [
        "You killed the giant rat.", 
        "Psychochild wounds you with his scimitar.", 
    ]
    for line in test_lines:
        res = monitor.process_line(line)
        if res: print(f"Detected: {res}")
