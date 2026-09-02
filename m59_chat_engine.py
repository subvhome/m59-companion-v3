# -*- coding: utf-8 -*-
"""
M59 Chat Engine & Communication Classifier
Parses raw game text into structured channels:
- Direct Messages / Private Tells (In & Out)
- Guild, Group, Local Say, Yell
- Combat, System, & Progression Improves
"""

import re

CHANNEL_COLORS = {
    'private': '#38bdf8',   # Light Blue (Tells)
    'chat': '#f8fafc',      # White / Say
    'guild': '#4ade80',     # Green (Guild)
    'group': '#fbbf24',     # Amber (Group)
    'combat': '#f87171',    # Red (Combat)
    'improves': '#a78bfa',  # Purple (Progression)
    'system': '#94a3b8',    # Gray (System)
}

def categorize_communication_line(msg_text):
        """
        Parses raw game text according to Meridian 59 client communication patterns:
        - Tells: 'Kran tells you, ...', 'You tell Kran, ...'
        - Sends: 'Kran sends to you, ...', 'You send to Kran, ...'
        - Guild: '[Guild] Kran: ...', 'Kran sends to guild, ...'
        - Group: '[Group] Kran: ...', 'Kran sends to group, ...'
        - Chat: 'Kran says, ...', 'You say, ...'
        - Yell: 'Kran yells, ...'
        - Broadcast / System: '[Broadcast] ...', '[System] ...', server messages
        - Combat / Improves: Death, kills, improves
        Returns: (channel, is_dm, dm_direction, dm_player, dm_body, dm_type)
        """
        text = msg_text.strip()
        lower = text.lower()

        # 1. Incoming Tell: "Kran tells you, 'Hello'" or "Kran tells you, Hello"
        m = re.match(r"^([A-Za-z0-9_ -]+?)\s+tells\s+you[,\s:]+[\"']?(.*?)[\"']?$", text, re.IGNORECASE)
        if m:
            sender = m.group(1).strip()
            body = m.group(2).strip()
            return ("private", True, "in", sender, body, "tell")

        # 2. Outgoing Tell: "You tell Kran, 'Hello'"
        m = re.match(r"^You\s+tell\s+([A-Za-z0-9_ -]+?)[,\s:]+[\"']?(.*?)[\"']?$", text, re.IGNORECASE)
        if m:
            target = m.group(1).strip()
            body = m.group(2).strip()
            return ("private", True, "out", target, body, "tell")

        # 3. Incoming Send: "Kran sends to you, 'Hello'" or "Kran sends you, 'Hello'"
        m = re.match(r"^([A-Za-z0-9_ -]+?)\s+sends(?:\s+to)?\s+you[,\s:]+[\"']?(.*?)[\"']?$", text, re.IGNORECASE)
        if m:
            sender = m.group(1).strip()
            body = m.group(2).strip()
            return ("private", True, "in", sender, body, "send")

        # 4. Outgoing Send: "You send to Kran, 'Hello'"
        m = re.match(r"^You\s+send(?:\s+to)?\s+([A-Za-z0-9_ -]+?)[,\s:]+[\"']?(.*?)[\"']?$", text, re.IGNORECASE)
        if m:
            target = m.group(1).strip()
            body = m.group(2).strip()
            return ("private", True, "out", target, body, "send")

        # 5. Guild Communications: "[Guild] Kran: Hello" or "Kran sends to guild, 'Hello'"
        m = re.match(r"^\[Guild\]\s*(?:([A-Za-z0-9_ -]+?):)?\s*(.*)$", text, re.IGNORECASE)
        if m:
            return ("guild", False, None, None, None, None)
        m = re.match(r"^([A-Za-z0-9_ -]+?)\s+sends\s+to\s+guild[,\s:]+[\"']?(.*?)[\"']?$", text, re.IGNORECASE)
        if m:
            return ("guild", False, None, None, None, None)

        # 6. Group Communications: "[Group] Kran: Hello" or "Kran sends to group, 'Hello'"
        m = re.match(r"^\[Group\]\s*(?:([A-Za-z0-9_ -]+?):)?\s*(.*)$", text, re.IGNORECASE)
        if m:
            return ("group", False, None, None, None, None)
        m = re.match(r"^([A-Za-z0-9_ -]+?)\s+sends\s+to\s+group[,\s:]+[\"']?(.*?)[\"']?$", text, re.IGNORECASE)
        if m:
            return ("group", False, None, None, None, None)

        # 7. Local / Public Say & Yell: "Kran says, 'Hello'", "You say, 'Hello'", "Kran yells, ..."
        m = re.match(r"^([A-Za-z0-9_ -]+?)\s+(?:says|yells)[,\s:]+[\"']?(.*?)[\"']?$", text, re.IGNORECASE)
        if m or "[Say]" in text or "says," in lower or "yells," in lower:
            return ("chat", False, None, None, None, None)

        # 8. Combat & Kills
        if any(k in lower for k in ["killed", "fatal blow", "collapses", "slain", "strikes", "casts", "inflicts"]):
            return ("combat", False, None, None, None, None)

        # 9. Progression & Improves
        if "improved" in lower or "tougher" in lower or "more knowledgeable" in lower:
            return ("improves", False, None, None, None, None)

        # 10. Default System Broadcast
        return ("system", False, None, None, None, None)

