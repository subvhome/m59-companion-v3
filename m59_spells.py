import os
import csv
import json
import re
import time
from datetime import datetime
from m59_utils import resource_path, get_safe_name

# Standard spellcast patterns in Meridian 59
TRANCE_START_REGEX = re.compile(
    r"^(?:You focus your whole will on casting|You start to cast)\s+([^.]+)\.?",
    re.I
)

TRANCE_BREAK_REGEX = re.compile(
    r"(?:You lose your concentration|Your concentration is broken|Your spell fizzles|"
    r"You fail to cast|You are interrupted|You cannot cast while|Your spell has no effect|"
    r"The spell fizzles|You are unable to cast|unsuccessful in casting|unsuccessful in|"
    r"You fail in your attempt|Your spell was resisted|don't have the reagents|"
    r"don't have enough mana|too tired to cast|can't cast spells while resting|"
    r"hands are too full|not powerful enough|not worthy to cast|not vile enough|"
    r"guardian angel tells you|safety was on|antisocial cast|turned into an outlaw|"
    r"out of range|can't cast .* on|no proper targets|can't cast .* here|"
    r"already in effect here|resists your|catch in your throat|can't quite remember|"
    r"tip of your tongue|Klaatu Veratu|notes of .* song tangle|unseen force rips)",
    re.I
)

CAST_SUCCESS_GENERIC_REGEX = re.compile(
    r"^(?:You complete your spell|Your spell takes effect|You invoke the power of|You channel the power of|"
    r"You cast (?:the spell )?([^.]+?)(?: on [^.]+)?\.|You summon|A magical glow surrounds|You send forth)",
    re.I
)

SPELL_ADVANCE_REGEX = re.compile(
    r"^(?:You have improved in the art of|You advanced in)\s+([^.!]+)",
    re.I
)

CAST_FAILED_CHANCE_REGEX = re.compile(
    r"^(?:You fail in your attempt to cast|You struggle to cast but fail|You were unsuccessful in casting|You was unsuccessful in casting)\s+([^.]+)\.?",
    re.I
)

CAST_RESISTED_REGEX = re.compile(r"^(?:Your spell was resisted by|[^.]+\s+resists your spell)\s*([^.]*)\.?", re.I)

BACKGROUND_NOISE_REGEX = re.compile(
    r"^(?:You open the door|You enter|You exit|You walk|You are unable to go|You cannot go|"
    r"You see|You look|There is|It is|You block|The \w+ claws|The \w+ burns|The \w+ bites|"
    r"The \w+ hits|The \w+ misses|The \w+ attacks|The \w+ slashes|The \w+ strikes|"
    r"You avoid|You hit|You miss|You swing|You slash|You stab|You shoot|You punch|"
    r"\[|say,|says,|tells you,|you tell|shouts,|yells,|group:)",
    re.I
)


class SpellManager:
    """
    Tracks spell casting, trance states, and reagent consumption statistics.
    Loads spell reagent requirements from settings/spells.csv and persists usage to JSON.
    """
    def __init__(self, char_name="Unknown"):
        self.char_name = char_name
        self.safe_name = get_safe_name(char_name)
        self.spells_db = self._load_spells_csv()
        
        # In-memory trance tracking
        # active_trance: dict with 'spell_name', 'canonical_name', 'start_time', 'cast_time', 'completed'
        self.active_trance = None
        self.last_instant_cast = None
        
        # Statistics:
        # {
        #   "spells_cast": { "create food": 10, ... },
        #   "reagents_used": { "Elder Berry": 20, "Herbs": 20, ... },
        #   "spell_reagent_breakdown": { "create food": { "Elder Berry": 20, "Herbs": 20 } },
        #   "history": [ { "ts": "...", "spell": "...", "reagents": { "Elder Berry": 2, ... } } ]
        # }
        self.reagent_stats = self._load_reagent_stats()

    def set_character(self, char_name):
        if char_name and char_name != self.char_name:
            self.char_name = char_name
            self.safe_name = get_safe_name(char_name)
            self.reagent_stats = self._load_reagent_stats()

    def _load_spells_csv(self):
        """Loads spell database and reagent requirements from settings/spells.csv."""
        spells = {}
        csv_path = resource_path("settings/spells.csv")
        if not os.path.exists(csv_path):
            csv_path = os.path.join(os.getcwd(), "settings", "spells.csv")
            
        if os.path.exists(csv_path):
            try:
                with open(csv_path, "r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        raw_name = row.get("Spell Name", "").strip()
                        if not raw_name:
                            continue
                        clean_name = raw_name.lower()
                        school = row.get("School", "").strip()
                        level = row.get("Level", "1").strip()
                        mana = row.get("Mana", "0").strip()
                        cast_time = row.get("Cast Time (s)", "0").strip()
                        trance_req = row.get("Trance?", "No").strip().lower() == "yes"
                        raw_reagents = row.get("Reagents Required", "").strip()
                        desc = row.get("Description", "").strip()
                        
                        reagent_dict = self._parse_reagents_string(raw_reagents)
                        
                        try:
                            c_time = float(cast_time)
                        except:
                            c_time = 0.0
                            
                        spells[clean_name] = {
                            "name": raw_name.title(),
                            "clean_name": clean_name,
                            "school": school,
                            "level": int(level) if level.isdigit() else 1,
                            "mana": int(mana) if mana.isdigit() else 0,
                            "cast_time": c_time,
                            "trance": trance_req,
                            "reagents_raw": raw_reagents,
                            "reagents": reagent_dict,
                            "description": desc
                        }
            except Exception as e:
                print(f"[M59-SPELLS] Error loading spells.csv: {e}", flush=True)
        return spells

    def _parse_reagents_string(self, reagent_str):
        """
        Parses reagent requirements string into { 'Reagent Name': count }.
        Examples:
          '2 Elder Berry, 2 Herbs' -> {'Elder Berry': 2, 'Herbs': 2}
          '1 Solagh, 1 Kriipa Claw' -> {'Solagh': 1, 'Kriipa Claw': 1}
          'None' -> {}
        """
        reagents = {}
        if not reagent_str or reagent_str.strip().lower() in ("none", "no", "--", "0", ""):
            return reagents
            
        parts = [p.strip() for p in reagent_str.split(",") if p.strip()]
        for p in parts:
            # Pattern: (\d+)\s+(.+)
            m = re.match(r"^(\d+)\s+(.+)$", p)
            if m:
                count = int(m.group(1))
                r_name = m.group(2).strip()
                # Standardize common reagent plural/singular names
                r_canonical = self._canonical_reagent_name(r_name)
                reagents[r_canonical] = reagents.get(r_canonical, 0) + count
            else:
                # Single reagent without number (e.g. 'Elderberry' or '1x Elderberry')
                clean_p = re.sub(r"^\d+x?\s*", "", p).strip()
                r_canonical = self._canonical_reagent_name(clean_p)
                if r_canonical:
                    reagents[r_canonical] = reagents.get(r_canonical, 0) + 1
        return reagents

    def _canonical_reagent_name(self, name):
        """Harmonizes reagent names to clean capitalized display names."""
        n = name.strip()
        n_lower = n.lower()
        
        mapping = {
            "elder berry": "Elderberry",
            "elder berry": "Elderberry",
            "elderberry": "Elderberry",
            "elderberries": "Elderberry",
            "herb": "Herbs",
            "herbs": "Herbs",
            "mushroom": "Mushroom",
            "mushrooms": "Mushroom",
            "red mushroom": "Red Mushroom",
            "red mushrooms": "Red Mushroom",
            "purple mushroom": "Purple Mushroom",
            "purple mushrooms": "Purple Mushroom",
            "blue mushroom": "Blue Mushroom",
            "blue mushrooms": "Blue Mushroom",
            "snack": "Snack",
            "sapphire": "Sapphire",
            "sapphires": "Sapphire",
            "orctooth": "Orc Tooth",
            "orc tooth": "Orc Tooth",
            "orc teeth": "Orc Tooth",
            "solagh": "Solagh",
            "kriipa claw": "Kriipa Claw",
            "kriipa claws": "Kriipa Claw",
            "dragonfly eye": "Dragonfly Eye",
            "dragonfly eyes": "Dragonfly Eye",
            "uncut seraphym": "Uncut Seraphym",
            "polished seraphym": "Polished Seraphym",
            "blue dragon scale": "Blue Dragon Scale",
            "blue dragon scales": "Blue Dragon Scale",
            "yrxlsap": "Yrxl Sap",
            "yrxl sap": "Yrxl Sap",
            "fire sand": "Firesand",
            "firesand": "Firesand",
            "web moss": "Web Moss",
            "shaman blood": "Shaman Blood",
            "entroot berry": "Entroot Berry",
            "entroot berries": "Entroot Berry",
            "fairy wing": "Fairy Wing",
            "fairy wings": "Fairy Wing",
            "spider web": "Spider Web",
            "prism": "Prism",
            "dark angel feather": "Dark Angel Feather",
            "dark angel feathers": "Dark Angel Feather",
            "emerald": "Emerald",
            "emeralds": "Emerald",
            "diamond": "Diamond",
            "diamonds": "Diamond",
            "ruby": "Ruby",
            "rubies": "Ruby"
        }
        return mapping.get(n_lower, n.title())

    def _load_reagent_stats(self):
        """Loads persisted reagent statistics from JSON."""
        defaults = {
            "spells_cast": {},
            "reagents_used": {},
            "spell_reagent_breakdown": {},
            "total_casts": 0,
            "total_reagents": 0,
            "session_casts": {},
            "session_reagents": {},
            "history": [],
            "daily_usage": {}
        }
        
        candidates = []
        if self.safe_name and self.safe_name not in ["--", "Unknown"]:
            candidates.append(f"settings/{self.safe_name}_reagents.json")
        else:
            candidates.append("settings/last_reagents.json")
            candidates.append("settings/reagents.json")
        
        for p in candidates:
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            # Merge with default structure
                            defaults["spells_cast"] = data.get("spells_cast", {})
                            defaults["reagents_used"] = data.get("reagents_used", {})
                            defaults["spell_reagent_breakdown"] = data.get("spell_reagent_breakdown", {})
                            defaults["total_casts"] = sum(defaults["spells_cast"].values())
                            defaults["total_reagents"] = sum(defaults["reagents_used"].values())
                            defaults["history"] = data.get("history", [])[-200:]
                            defaults["daily_usage"] = data.get("daily_usage", {})
                            return defaults
                except Exception as e:
                    print(f"[M59-SPELLS] Failed reading reagent stats from {p}: {e}", flush=True)
        return defaults

    def save_reagent_stats(self):
        """Persists current reagent statistics to JSON."""
        os.makedirs("settings", exist_ok=True)
        paths = []
        if self.safe_name and self.safe_name not in ["--", "Unknown"]:
            paths.append(f"settings/{self.safe_name}_reagents.json")
        else:
            paths.append("settings/last_reagents.json")
        
        save_obj = {
            "spells_cast": self.reagent_stats.get("spells_cast", {}),
            "reagents_used": self.reagent_stats.get("reagents_used", {}),
            "spell_reagent_breakdown": self.reagent_stats.get("spell_reagent_breakdown", {}),
            "total_casts": self.reagent_stats.get("total_casts", 0),
            "total_reagents": self.reagent_stats.get("total_reagents", 0),
            "history": self.reagent_stats.get("history", [])[-200:],
            "daily_usage": self.reagent_stats.get("daily_usage", {}),
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        for p in paths:
            try:
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(save_obj, f, indent=2)
            except Exception as e:
                print(f"[M59-SPELLS] Error saving reagent stats to {p}: {e}", flush=True)

    def find_spell_info(self, query):
        """Matches a spell name query to canonical spell entry."""
        if not query:
            return None
        q = query.strip().lower()
        if q in self.spells_db:
            return self.spells_db[q]
            
        # Partial / fuzzy lookup
        for s_name, data in self.spells_db.items():
            if q == s_name or q in s_name or s_name in q:
                return data
        return None

    def record_spell_success(self, spell_name, is_historical=False):
        """
        Records a successful cast of spell_name, updates reagent consumption,
        and saves stats.
        Returns event dict with consumption details or None.
        """
        info = self.find_spell_info(spell_name)
        canonical_name = info["name"] if info else spell_name.title()
        reagents = info["reagents"] if info else {}
        
        # Track last recorded cast time to prevent duplicate triggers from skill advance messages
        self.last_recorded_cast = (canonical_name, time.time())

        # 1. Update All-Time and Session Spells Cast
        spells_cast = self.reagent_stats.setdefault("spells_cast", {})
        spells_cast[canonical_name] = spells_cast.get(canonical_name, 0) + 1
        
        session_casts = self.reagent_stats.setdefault("session_casts", {})
        session_casts[canonical_name] = session_casts.get(canonical_name, 0) + 1
        
        self.reagent_stats["total_casts"] = sum(spells_cast.values())
        
        # 2. Update Reagent Counts
        reagents_used = self.reagent_stats.setdefault("reagents_used", {})
        session_reagents = self.reagent_stats.setdefault("session_reagents", {})
        breakdown = self.reagent_stats.setdefault("spell_reagent_breakdown", {})
        spell_breakdown = breakdown.setdefault(canonical_name, {})
        
        for r_name, count in reagents.items():
            reagents_used[r_name] = reagents_used.get(r_name, 0) + count
            session_reagents[r_name] = session_reagents.get(r_name, 0) + count
            spell_breakdown[r_name] = spell_breakdown.get(r_name, 0) + count
            
        self.reagent_stats["total_reagents"] = sum(reagents_used.values())
        
        # 3. Update Daily Usage Metrics
        today_str = datetime.now().strftime("%Y-%m-%d")
        daily_usage = self.reagent_stats.setdefault("daily_usage", {})
        today_entry = daily_usage.setdefault(today_str, {"spells": {}, "reagents": {}, "casts": 0, "total_reagents": 0})
        today_entry["casts"] = today_entry.get("casts", 0) + 1
        today_entry["total_reagents"] = today_entry.get("total_reagents", 0) + sum(reagents.values())
        
        d_spells = today_entry.setdefault("spells", {})
        d_spells[canonical_name] = d_spells.get(canonical_name, 0) + 1
        
        d_reagents = today_entry.setdefault("reagents", {})
        for r_name, count in reagents.items():
            d_reagents[r_name] = d_reagents.get(r_name, 0) + count
        
        # 4. Add to History
        ts = datetime.now().strftime("%H:%M:%S")
        entry = {
            "date": today_str,
            "ts": ts,
            "spell": canonical_name,
            "school": info.get("school", "Unknown") if info else "Unknown",
            "mana": info.get("mana", 0) if info else 0,
            "reagents": reagents
        }
        hist = self.reagent_stats.setdefault("history", [])
        hist.append(entry)
        if len(hist) > 300:
            self.reagent_stats["history"] = hist[-300:]
            
        if not is_historical:
            self.save_reagent_stats()
            
        return {
            "type": "SPELL_CAST_SUCCESS",
            "spell_name": canonical_name,
            "school": info.get("school", "Unknown") if info else "Unknown",
            "reagents": reagents,
            "session_count": session_casts[canonical_name],
            "total_count": spells_cast[canonical_name],
            "timestamp": ts
        }

    def process_line(self, line, is_historical=False):
        """
        Parses a log line for spell trance start, trance interruption/fizzle,
        instant cast, success (including spell flavor text), or skill improvement.
        Returns event dict or None.
        """
        clean = line.strip()
        if not clean:
            return None
            
        # 1. Trance Start Check
        trance_m = TRANCE_START_REGEX.search(clean)
        if trance_m:
            detected_spell = trance_m.group(1).strip()
            info = self.find_spell_info(detected_spell)
            c_name = info["name"] if info else detected_spell.title()
            cast_time = info.get("cast_time", 2.0) if info else 2.0
            
            self.active_trance = {
                "spell_name": detected_spell,
                "canonical_name": c_name,
                "start_time": time.time(),
                "cast_time": cast_time,
                "completed": False
            }
            return {
                "type": "TRANCE_START",
                "spell_name": c_name,
                "cast_time": cast_time
            }
            
        # 2. Trance Interrupted / Failed / Fizzled Check
        if TRANCE_BREAK_REGEX.search(clean) or CAST_FAILED_CHANCE_REGEX.search(clean) or CAST_RESISTED_REGEX.search(clean):
            if self.active_trance:
                interrupted_spell = self.active_trance["canonical_name"]
                self.active_trance = None
                return {
                    "type": "SPELL_FIZZLED",
                    "spell_name": interrupted_spell,
                    "message": clean
                }
            return {
                "type": "SPELL_FAILED",
                "message": clean
            }
            
        # 3. Spell Improvement (Skill Advance line)
        advance_m = SPELL_ADVANCE_REGEX.match(clean)
        if advance_m:
            adv_name = advance_m.group(1).strip()
            info = self.find_spell_info(adv_name)
            c_name = info["name"] if info else adv_name.title()
            
            self.active_trance = None
            
            # Check if this exact spell was recorded within the last 4 seconds to avoid double-counting
            now = time.time()
            last_name, last_time = getattr(self, 'last_recorded_cast', (None, 0))
            if last_name == c_name and (now - last_time) < 4.0:
                return {
                    "type": "SPELL_IMPROVED",
                    "spell_name": c_name,
                    "message": clean
                }
                
            return self.record_spell_success(adv_name, is_historical=is_historical)
            
        # 4. If in active trance, handle the next outcome line (spell completion flavor text)
        if self.active_trance:
            # Ignore combat attacks/misses, movement, door messages, or chat messages
            if BACKGROUND_NOISE_REGEX.search(clean):
                return None
                
            # Any line arriving after trance start (that is not a failure, new trance, or noise)
            # IS the spell success / flavor text! (e.g., "Your hands weave through...", "You are amazed...")
            s_name = self.active_trance["canonical_name"]
            self.active_trance = None
            return self.record_spell_success(s_name, is_historical=is_historical)
            
        # 5. Direct Standalone Success Match (for instant spells without prior trance message)
        success_m = CAST_SUCCESS_GENERIC_REGEX.match(clean)
        if success_m and success_m.group(1):
            s_name = success_m.group(1).strip()
            return self.record_spell_success(s_name, is_historical=is_historical)
            
        return None
