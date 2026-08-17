import os
import json
import logging
from m59_utils import get_safe_name, RE_BANK_TOTAL, RE_BANK_WITHDRAW

logger = logging.getLogger("m59.bank")

class BankManager:
    def __init__(self):
        self.balances = {"mainland": 0, "island": 0}
        self.current_char = None

    def load_balances(self, char_name):
        """Loads persistent balances for a specific character."""
        if not char_name or char_name in ["Unknown", "--"]:
            return
            
        self.current_char = char_name
        safe_n = get_safe_name(char_name)
        
        for folder in ["settings", "logs"]:
            p = f"{folder}/{safe_n}_bank.json"
            if os.path.exists(p):
                try:
                    with open(p, "r") as f:
                        self.balances = json.load(f)
                        logger.info(f"Bank: Loaded balances for {char_name}: M:{self.balances.get('mainland', 0)}, I:{self.balances.get('island', 0)}")
                        return
                except Exception as e:
                    logger.error(f"Bank: Failed to load balances from {p}: {e}")
        self.balances = {"mainland": 0, "island": 0}

    def save_balances(self):
        """Saves current balances to disk."""
        if not self.current_char or self.current_char in ["Unknown", "--"]:
            return
            
        safe_n = get_safe_name(self.current_char)
        
        for folder in ["settings", "logs"]:
            try:
                os.makedirs(folder, exist_ok=True)
                p = f"{folder}/{safe_n}_bank.json"
                with open(p, "w") as f:
                    json.dump(self.balances, f)
            except Exception as e:
                logger.error(f"Bank: Failed to save balances to {folder}: {e}")

    def process_line(self, line):
        """Parses a chat line for bank messages. Returns True if a balance changed."""
        changed = False
        
        # 1. Check for Total Balance / Deposit Result
        m = RE_BANK_TOTAL.search(line)
        if m:
            npc, amount = m.groups()
            bank_type = self._get_bank_type(npc)
            if bank_type:
                new_val = int(amount)
                if self.balances[bank_type] != new_val:
                    self.balances[bank_type] = new_val
                    changed = True
                    logger.info(f"Bank: Update detected from {npc} ({bank_type}): {new_val}")
        
        # 2. Check for Withdrawal
        else:
            m = RE_BANK_WITHDRAW.search(line)
            if m:
                npc, amount = m.groups()
                bank_type = self._get_bank_type(npc)
                if bank_type:
                    withdraw_amt = int(amount)
                    self.balances[bank_type] = max(0, self.balances[bank_type] - withdraw_amt)
                    changed = True
                    logger.info(f"Bank: Withdrawal detected from {npc} ({bank_type}): -{withdraw_amt} (New: {self.balances[bank_type]})")
        
        if changed:
            self.save_balances()
            
        return changed

    def _get_bank_type(self, npc_name):
        """Determines if an NPC belongs to Mainland or Island bank."""
        n = npc_name.lower()
        if "skivlat" in n:
            return "mainland"
        if "huital" in n or "nosak" in n:
            return "island"
        return None
