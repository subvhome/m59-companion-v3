# -*- coding: utf-8 -*-
"""
===============================================================================
 MERIDIAN 59 AUTONOMOUS GLOBE NEWS HARVESTER & ARCHIVER ENGINE
 Integrated Engine: Frida Wire Sniffer + SQLite + JSON Archiver + Room GPS Engine

 FEATURES:
  1. Room Location-Based Auto-Selection: Automatically detects active globe(s) in room.
  2. First-Time Sync Confirmation: Prompts user on first run before syncing full history.
  3. Automatic Incremental Sync: Seamless auto-sync on subsequent visits to globe rooms.
  4. Complete Local Archive: Dual-archiving to SQLite database and formatted JSON files
     (meridian59_news_archive.json, General_News.json, Designers_News.json).
  5. Native Frida Hooking: Hooking EventServerMessage, Winsock, and ToServer RPC exports.
===============================================================================
"""

import os
import sys
import time
import json
import struct
import sqlite3
import threading
import datetime
import shutil
from typing import Dict, List, Optional, Tuple, Set

# Win32 & Companion Imports
try:
    import win32gui
    import win32process
    import win32con
    HAS_WIN32 = True
except ImportError:
    win32gui = None
    win32process = None
    HAS_WIN32 = False

try:
    import frida
    HAS_FRIDA = True
except ImportError:
    HAS_FRIDA = False

try:
    from m59_utils import resource_path
except ImportError:
    def resource_path(p):
        return p

try:
    from m59_gps import GPSManager
except ImportError:
    GPSManager = None

try:
    from m59_logging import get_logger
    news_logger = get_logger("news_globe")
except Exception:
    import logging
    news_logger = logging.getLogger("news_globe")

# Constants
RAW_LOG_PATH = "news_wire_raw.log"
ARCHIVE_JSON_PATH = "meridian59_news_archive.json"
PACKET_TRACE_PATH = "m59_packet_trace.log"
M59_EPOCH_OFFSET = 1534000000

# Newsgroups NIDs
NID_GAME = 3
NID_JUSTICAR = 4
NID_ADVENTURE = 5
NID_EVENT_SCHEDULE = 6
NID_ANNOUNCEMENTS = 9
NID_GUILD_CHARTER = 10
NID_GUILD_NEWS = 15
NID_TOS_HALL = 20

# Protocol Opcodes
BP_MAIL             = 0x50  # 80
BP_REQ_GET_MAIL     = 0x51  # 81
BP_SEND_MAIL        = 0x52  # 82
BP_DELETE_MAIL      = 0x53  # 83
BP_REQ_ARTICLES     = 0x55  # 85
BP_REQ_ARTICLE      = 0x56  # 86
BP_POST_ARTICLE     = 0x57  # 87
BP_REQ_LOOKUP_NAMES = 0x58  # 88
BP_LOOK_NEWSGROUP   = 0xB4  # 180
BP_ARTICLES         = 0xB5  # 181
BP_ARTICLE          = 0xB6  # 182
BP_LOOKUP_NAMES     = 0xBE  # 190

NEWSGROUP_NAMES = {
    NID_ANNOUNCEMENTS: "Designers' News",
    NID_TOS_HALL: "General News (Adventurer Hall)",
    NID_GAME: "Game Updates & Maintenance",
    NID_ADVENTURE: "Tales of Adventure (Bards)",
    NID_JUSTICAR: "Justicar & Court News",
    NID_EVENT_SCHEDULE: "Event Schedule & Community",
    NID_GUILD_CHARTER: "Guild Charter & Rules",
    NID_GUILD_NEWS: "Guild Internal News"
}

NEWSGROUP_DESCRIPTIONS = {
    NID_ANNOUNCEMENTS: "Official administrator announcements, server updates, and patch notes (Read-only for players).",
    NID_TOS_HALL: "Public community newsgroup located in Adventurer Halls across all towns (Open posting).",
    NID_GAME: "Technical notes and server maintenance schedules.",
    NID_ADVENTURE: "Bardic tales, poetry, and chronicles of legendary Meridian adventures.",
    NID_JUSTICAR: "Legal declarations, court trials, and Justicar warrants from Barloque Court.",
    NID_EVENT_SCHEDULE: "Player-organized tournaments, arena matches, and guild events.",
    NID_GUILD_CHARTER: "Guild declarations and charter rules.",
    NID_GUILD_NEWS: "Guild internal communications."
}

# -------------------------------------------------------------------------
# Wire Protocol Encoders & Decoders
# -------------------------------------------------------------------------
def build_bp_req_articles(nid: int) -> bytes:
    """BP_REQ_ARTICLES (Opcode 85 / 0x55)"""
    return struct.pack("<BH", BP_REQ_ARTICLES, nid)

def parse_bp_articles(data: bytes) -> Dict:
    """Parses BP_ARTICLES (Opcode 181 / 0xB5)"""
    result = {"nid": 0, "num_articles": 0, "articles": []}
    if not data or len(data) < 7:
        return result
    try:
        opcode, nid, b1, b2, count = struct.unpack_from("<BHBBH", data, 0)
        result["nid"] = nid
        offset = 7
        articles = []
        for _ in range(count):
            if offset + 8 > len(data):
                break
            art_num, timestamp = struct.unpack_from("<II", data, offset)
            offset += 8
            p_end = data.find(b'\x00', offset)
            if p_end == -1:
                break
            poster = data[offset:p_end].decode('latin-1', 'replace')
            offset = p_end + 1
            t_end = data.find(b'\x00', offset)
            if t_end == -1:
                break
            title = data[offset:t_end].decode('latin-1', 'replace')
            offset = t_end + 1
            articles.append({
                "nid": nid,
                "article_num": art_num,
                "timestamp": timestamp,
                "poster": poster,
                "title": title
            })
        result["articles"] = articles
        result["num_articles"] = len(articles)
    except Exception as e:
        news_logger.error(f"Error in parse_bp_articles: {e}")
    return result

def build_bp_req_article(nid: int, article_num: int) -> bytes:
    """BP_REQ_ARTICLE (Opcode 86 / 0x56)"""
    return struct.pack("<BHI", BP_REQ_ARTICLE, nid, article_num)

def parse_bp_article(data: bytes) -> Optional[Dict]:
    """Parses BP_ARTICLE (Opcode 182 / 0xB6)"""
    if not data or len(data) < 2:
        return None
    try:
        if len(data) >= 7 and data[0] == BP_ARTICLE:
            nid, art_num = struct.unpack_from("<HI", data, 1)
            offset = 7
            body_text = data[offset:].decode('latin-1', 'replace').rstrip('\x00')
            return {"nid": nid, "article_num": art_num, "body": body_text}
        else:
            body_text = data[1:].decode('latin-1', 'replace').rstrip('\x00')
            return {"body": body_text}
    except Exception as e:
        news_logger.error(f"Error in parse_bp_article: {e}")
        return None

def build_bp_post_article(nid: int, subject: str, body: str) -> bytes:
    """BP_POST_ARTICLE (Opcode 87 / 0x57)"""
    subj_b = subject.encode('latin-1', 'replace') + b'\x00'
    body_b = body.encode('latin-1', 'replace') + b'\x00'
    return struct.pack("<BH", BP_POST_ARTICLE, nid) + subj_b + body_b

def parse_bp_look_newsgroup(data: bytes) -> Optional[Dict]:
    if not data or len(data) < 3:
        return None
    nid = struct.unpack_from("<H", data, 1)[0]
    return {"nid": nid}

def build_bp_req_lookup_names(prefix: str) -> bytes:
    """BP_REQ_LOOKUP_NAMES (Opcode 88 / 0x58)"""
    prefix_b = prefix.encode('latin-1', 'replace') + b'\x00'
    return struct.pack("<B", BP_REQ_LOOKUP_NAMES) + prefix_b

def parse_bp_lookup_names(data: bytes) -> List[str]:
    names = []
    if not data or len(data) < 2:
        return names
    offset = 1
    while offset < len(data):
        end = data.find(b'\x00', offset)
        if end == -1:
            break
        name = data[offset:end].decode('latin-1', 'replace')
        if name:
            names.append(name)
        offset = end + 1
    return names

def build_bp_req_get_mail() -> bytes:
    """BP_REQ_GET_MAIL (Opcode 81 / 0x51)"""
    return struct.pack("<B", BP_REQ_GET_MAIL)

def parse_bp_mail(data: bytes) -> Optional[Dict]:
    if not data or len(data) < 6:
        return None
    try:
        idx = struct.unpack_from("<I", data, 1)[0]
        offset = 5
        s_end = data.find(b'\x00', offset)
        if s_end == -1:
            return None
        sender = data[offset:s_end].decode('latin-1', 'replace')
        offset = s_end + 1
        msg_time, num_recip = struct.unpack_from("<IH", data, offset)
        offset += 6
        recips = []
        for _ in range(num_recip):
            r_end = data.find(b'\x00', offset)
            if r_end != -1:
                recips.append(data[offset:r_end].decode('latin-1', 'replace'))
                offset = r_end + 1
        offset += 4
        raw_p = data[offset:].decode('latin-1', 'replace').strip('\x00')
        parts = raw_p.split('\n', 2)
        to_names = parts[0] if len(parts) > 0 else ", ".join(recips)
        subject = parts[1] if len(parts) > 1 else "(No Subject)"
        body = parts[2] if len(parts) > 2 else ""
        return {
            "mail_index": idx,
            "sender": sender,
            "recipients": to_names,
            "timestamp": msg_time,
            "subject": subject,
            "body": body
        }
    except Exception as e:
        news_logger.error(f"Error in parse_bp_mail: {e}")
        return None

def build_bp_send_mail(*args, **kwargs) -> bytes:
    """BP_SEND_MAIL (Opcode 82 / 0x52)"""
    if len(args) == 4:
        recipients = args[1]
        subject = args[2]
        body = args[3]
    elif len(args) == 3:
        recipients = args[0]
        subject = args[1]
        body = args[2]
    else:
        recipients = kwargs.get('recipients', '')
        subject = kwargs.get('subject', '')
        body = kwargs.get('body', '')
    
    if isinstance(recipients, list):
        recipients = ", ".join(str(r) for r in recipients)
    recip_b = recipients.encode('latin-1', 'replace') + b'\x00'
    subj_b = subject.encode('latin-1', 'replace') + b'\x00'
    body_b = body.encode('latin-1', 'replace') + b'\x00'
    return struct.pack("<B", BP_SEND_MAIL) + recip_b + subj_b + body_b

def build_bp_delete_mail(mail_index: int) -> bytes:
    """BP_DELETE_MAIL (Opcode 83 / 0x53)"""
    return struct.pack("<BI", BP_DELETE_MAIL, mail_index)

# Location & Room Mapping Table
LOCATIONS_TABLE = [
    # Inns (Designers' News / Announcements - NID 9)
    {"id": 1, "name": "Jasper Inn (Yonder Inn)", "nid": 9, "type": "Designers_News", "room": "Jasper Inn", "aliases": ["yonder inn of jasper", "yonder inn", "jasper inn"], "keywords": ["jasper", "inn"], "rid": "RID_JAS_INN"},
    {"id": 2, "name": "Tos Inn (Familiars)", "nid": 9, "type": "Designers_News", "room": "Tos Inn", "aliases": ["familiars", "tos inn", "familiar"], "keywords": ["tos", "inn"], "rid": "RID_TOS_INN"},
    {"id": 3, "name": "Barloque Inn (Brownestone Inn)", "nid": 9, "type": "Designers_News", "room": "Barloque Inn", "aliases": ["brownestone inn", "barloque inn"], "keywords": ["barloque", "inn"], "rid": "RID_BAR_INN"},
    {"id": 4, "name": "Cor Noth Inn (Cibilo Creek Inn)", "nid": 9, "type": "Designers_News", "room": "Cor Noth Inn", "aliases": ["cibilo creek inn", "cor noth inn"], "keywords": ["cor noth", "inn"], "rid": "RID_COR_INN"},
    {"id": 5, "name": "Ko'catan Inn (The Aerie Guest House)", "nid": 9, "type": "Designers_News", "room": "Ko'catan Inn", "aliases": ["the aerie guest house", "aerie guest house", "ko'catan inn", "kocatan inn"], "keywords": ["ko'catan", "inn"], "rid": "RID_KOC_INN"},
    {"id": 6, "name": "Marion Inn (The Limping Toad Inn)", "nid": 9, "type": "Designers_News", "room": "Marion Inn", "aliases": ["the limping toad inn and tavern", "limping toad inn", "marion inn"], "keywords": ["marion", "inn"], "rid": "RID_MAR_INN"},
    {"id": 7, "name": "Raza Inn (Starter Inn)", "nid": 9, "type": "Designers_News", "room": "Raza Inn", "aliases": ["raza inn", "starter inn"], "keywords": ["raza", "inn"], "rid": "RID_NEWB1"},

    # Adventurer Halls (General / Hall News - NID 20)
    {"id": 8, "name": "Jasper Hall (Adventurer's Hall)", "nid": 20, "type": "General_News", "room": "Jasper Hall", "aliases": ["adventurer's hall of jasper", "adventurer hall of jasper", "jasper hall"], "keywords": ["jasper", "hall"], "rid": "RID_JAS_HALL"},
    {"id": 9, "name": "Tos Hall (Adventurer's Hall)", "nid": 20, "type": "General_News", "room": "Tos Hall", "aliases": ["the adventurer's hall of tos", "adventurer's hall of tos", "tos hall"], "keywords": ["tos", "hall"], "rid": "RID_TOS_HALL"},
    {"id": 10, "name": "Barloque Hall (Adventurer's Hall)", "nid": 20, "type": "General_News", "room": "Barloque Hall", "aliases": ["adventurer's hall of barloque", "barloque hall"], "keywords": ["barloque", "hall"], "rid": "RID_BAR_HALL"},
    {"id": 11, "name": "Cor Noth Hall (Adventurer's Hall)", "nid": 20, "type": "General_News", "room": "Cor Noth Hall", "aliases": ["the adventurer's hall of cor noth", "adventurer's hall of cor noth", "cor noth hall"], "keywords": ["cor noth", "hall"], "rid": "RID_COR_HALL"},
    {"id": 12, "name": "Ko'catan Hall (The Hall of Heroes)", "nid": 20, "type": "General_News", "room": "Ko'catan Hall", "aliases": ["the hall of heroes", "hall of heroes", "ko'catan hall", "kocatan hall"], "keywords": ["ko'catan", "hall"], "rid": "RID_KOC_HALL_OF_HEROES"},
    {"id": 13, "name": "Marion Hall (Adventurer's Hall)", "nid": 20, "type": "General_News", "room": "Marion Hall", "aliases": ["the adventurer's hall of marion", "adventurer's hall of marion", "marion hall"], "keywords": ["marion", "hall"], "rid": "RID_MAR_HALL"},

    # Special Newsgroups
    {"id": 14, "name": "Jasper Tavern (Tales of Adventure)", "nid": 5, "type": "Tales_of_Adventure", "room": "Jasper Tavern", "aliases": ["jasper tavern", "the jasper tavern"], "keywords": ["jasper", "tavern"], "rid": "RID_JAS_TAVERN"},
    {"id": 15, "name": "Tos Grey Dragon (Game News)", "nid": 3, "type": "Game_News", "room": "Tos Grey Dragon", "aliases": ["grey dragon", "tos grey dragon"], "keywords": ["grey dragon"], "rid": "RID_TOS_INN"},
    {"id": 16, "name": "Barloque Court (Book of Jala / Justicar)", "nid": 4, "type": "Justicar_News", "room": "Barloque Court", "aliases": ["barloque court", "barloque law court"], "keywords": ["barloque", "court"], "rid": "RID_BAR_COURT"},
    {"id": 17, "name": "Barloque GM Hall (Guild Charter)", "nid": 10, "type": "Guild_Charter", "room": "Barloque GM Hall", "aliases": ["barloque gm hall", "guildmaster's hall"], "keywords": ["guildmaster", "hall"], "rid": "RID_GM_HALL"},
    {"id": 18, "name": "Marion Elder (Event Schedule)", "nid": 6, "type": "Event_Schedule", "room": "Marion Elder", "aliases": ["the home of the elder", "marion elder"], "keywords": ["marion", "elder"], "rid": "RID_MAR_ELDER_HUT"},
]

NEWS_GLOBE_MAP = {loc["nid"]: loc for loc in LOCATIONS_TABLE}

# Logging Helpers
def raw_log(msg: str):
    try:
        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        with open(RAW_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass

def trace_log(msg: str):
    try:
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        with open(PACKET_TRACE_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass

def console_log(msg: str, prefix: str = "INFO"):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    formatted = f"[{ts}] [{prefix}] {msg}"
    news_logger.info(formatted)
    raw_log(f"[{prefix}] {msg}")

# Frida Script
FRIDA_HARVESTER_SCRIPT = r"""
var socket_fd = -1;
var pSend = null;
var pRecv = null;
var pToServer = null;
var pCustomMsgTable = null;
var mailnewsModule = null;

function findModuleExport(moduleName, exportName) {
    try {
        if (typeof Module.findExportByName === 'function') {
            var res = Module.findExportByName(moduleName, exportName);
            if (res) return res;
        }
    } catch (e) {}
    try {
        if (moduleName && typeof Process.getModuleByName === 'function') {
            var m = Process.getModuleByName(moduleName);
            if (m && typeof m.findExportByName === 'function') {
                return m.findExportByName(exportName);
            }
        }
    } catch (e) {}
    return null;
}

try {
    pSend = findModuleExport("ws2_32.dll", "send") || findModuleExport("wsock32.dll", "send");
    pRecv = findModuleExport("ws2_32.dll", "recv") || findModuleExport("wsock32.dll", "recv");
} catch (e) {
    send({ type: 'log', data: "Winsock lookup error: " + e });
}

var modules = [];
try {
    modules = Process.enumerateModules();
} catch (e) {}

var mainModule = modules.length > 0 ? modules[0] : null;

for (var i = 0; i < modules.length; i++) {
    try {
        var modName = modules[i].name.toLowerCase();
        if (modName.indexOf("mailnews") !== -1) {
            mailnewsModule = modules[i];
        }
    } catch(e) {}
}

try {
    pToServer = findModuleExport(null, "ToServer");
    if (!pToServer && mainModule) {
        pToServer = findModuleExport(mainModule.name, "ToServer");
    }
} catch (e) {}

try {
    pCustomMsgTable = Memory.alloc(17 * 8);
    for (var k = 0; k < 17 * 8; k++) {
        pCustomMsgTable.add(k).writeU8(0);
    }
    // Entry 0: BP_REQ_ARTICLES (85 / 0x55) -> [PARAM_NEWSID (13), PARAM_END (100)]
    pCustomMsgTable.add(0).writeU8(85);
    pCustomMsgTable.add(1).writeU8(13);
    pCustomMsgTable.add(2).writeU8(100);

    // Entry 1: BP_REQ_ARTICLE (86 / 0x56) -> [PARAM_NEWSID (13), PARAM_INDEX (14), PARAM_END (100)]
    pCustomMsgTable.add(17).writeU8(86);
    pCustomMsgTable.add(18).writeU8(13);
    pCustomMsgTable.add(19).writeU8(14);
    pCustomMsgTable.add(20).writeU8(100);

    // Entry 2: BP_REQ_LOOK (116 / 0x74) -> [PARAM_ID (1), PARAM_END (100)]
    pCustomMsgTable.add(34).writeU8(116);
    pCustomMsgTable.add(35).writeU8(1);
    pCustomMsgTable.add(36).writeU8(100);

    // Entry 3: BP_LOOKUP_NAMES (84 / 0x54)
    pCustomMsgTable.add(51).writeU8(84);
    pCustomMsgTable.add(52).writeU8(16);
    pCustomMsgTable.add(53).writeU8(6);
    pCustomMsgTable.add(54).writeU8(100);

    pCustomMsgTable.add(68).writeU8(0);
} catch (e) {}

function getExportByOrdinal(modBase, ordinal) {
    try {
        if (!modBase || modBase.isNull()) return null;
        var dosHeader = modBase;
        var e_lfanew = dosHeader.add(0x3C).readU32();
        var ntHeaders = modBase.add(e_lfanew);
        var exportDirRva = ntHeaders.add(0x78).readU32();
        if (exportDirRva === 0) return null;
        var exportDir = modBase.add(exportDirRva);
        var ordinalBase = exportDir.add(0x10).readU32();
        var numFunctions = exportDir.add(0x14).readU32();
        var functionsRva = exportDir.add(0x1C).readU32();
        var funcTable = modBase.add(functionsRva);
        var index = ordinal - ordinalBase;
        if (index >= 0 && index < numFunctions) {
            var funcRva = funcTable.add(index * 4).readU32();
            if (funcRva !== 0) return modBase.add(funcRva);
        }
    } catch (e) {}
    return null;
}

var lastEsmOpcode = -1;
var lastEsmLen = -1;
var lastEsmTime = 0;

function hookModuleServerMessage(mod) {
    if (!mod || !mod.base) return;
    try {
        var pTarget = getExportByOrdinal(mod.base, 2);
        if (!pTarget || pTarget.isNull()) {
            pTarget = findModuleExport(mod.name, "EventServerMessage");
        }

        if (pTarget && !pTarget.isNull()) {
            Interceptor.attach(pTarget, {
                onEnter: function(args) {
                    try {
                        var msgPtr = args[0];
                        var msgLen = args[1].toInt32();
                        if (msgLen > 0 && !msgPtr.isNull()) {
                            var opcode = msgPtr.readU8();
                            var now = Date.now();
                            if (opcode === lastEsmOpcode && msgLen === lastEsmLen && (now - lastEsmTime) < 80) {
                                return;
                            }
                            lastEsmOpcode = opcode;
                            lastEsmLen = msgLen;
                            lastEsmTime = now;

                            send({
                                type: 'unscrambled_msg',
                                opcode: opcode,
                                len: msgLen,
                                module: mod.name
                            }, msgPtr.readByteArray(msgLen));
                        }
                    } catch (err) {}
                }
            });
        }
    } catch (e) {}
}

var mailnewsHooked = false;
for (var mIdx = 0; mIdx < modules.length; mIdx++) {
    try {
        var m = modules[mIdx];
        if (m && m.name && m.name.toLowerCase().indexOf("mailnews.dll") !== -1) {
            hookModuleServerMessage(m);
            mailnewsHooked = true;
            break;
        }
    } catch (e) {}
}

// 5. Hook SendMessageA / SendMessageW to capture UI article deliveries
try {
    var pSendMsgA = findModuleExport("user32.dll", "SendMessageA");
    var pSendMsgW = findModuleExport("user32.dll", "SendMessageW");
    var handleSendMsg = function(args) {
        try {
            var msg = args[1].toInt32();
            var lParam = args[3];
            if (msg === 1145 && !lParam.isNull()) {
                var bodyStr = lParam.readAnsiString();
                if (bodyStr && bodyStr.length > 0) {
                    send({
                        type: 'ui_article_body',
                        body: bodyStr
                    });
                }
            }
        } catch(e) {}
    };
    if (pSendMsgA) Interceptor.attach(pSendMsgA, { onEnter: handleSendMsg });
    if (pSendMsgW) Interceptor.attach(pSendMsgW, { onEnter: handleSendMsg });
} catch (e) {}

// 6. Winsock Sniffing & Active Socket Tracking
if (pSend && pRecv) {
    try {
        Interceptor.attach(pSend, {
            onEnter: function(args) {
                try {
                    var s = args[0].toInt32();
                    var buf = args[1];
                    var len = args[2].toInt32();
                    
                    if (len >= 7 && !buf.isNull()) {
                        var u8 = new Uint8Array(buf.readByteArray(len));
                        var len_val = u8[0] | (u8[1] << 8);
                        var len_rep = u8[4] | (u8[5] << 8);
                        
                        if (len_val === len_rep && (len_val + 7) === len) {
                            if (s !== socket_fd) {
                                socket_fd = s;
                                send({ type: 'socket_bound', socket: s });
                            }
                            
                            var opcode = u8[7];
                            if ((opcode === 0x74 || opcode === 0x75) && len >= 12) {
                                var globeId = u8[8] | (u8[9] << 8) | (u8[10] << 16) | (u8[11] << 24);
                                send({
                                    type: 'globe_captured',
                                    id: globeId
                                });
                            }
                            
                            send({
                                type: 'packet_out',
                                socket: s,
                                length: len
                            }, buf.readByteArray(len));
                        }
                    }
                } catch(e) {}
            }
        });
    } catch(e) {}

    try {
        Interceptor.attach(pRecv, {
            onEnter: function(args) {
                this.s = args[0].toInt32();
                this.buf = args[1];
                this.len = args[2].toInt32();
            },
            onLeave: function(retval) {
                try {
                    var count = retval.toInt32();
                    if (count > 0 && !this.buf.isNull()) {
                        send({
                            type: 'packet_in_raw',
                            socket: this.s,
                            length: count
                        }, this.buf.readByteArray(count));
                    }
                } catch(e) {}
            }
        });
    } catch(e) {}
}

rpc.exports = {
    checkHooks: function() {
        return {
            hasToServer: pToServer !== null,
            hasMsgTable: pCustomMsgTable !== null,
            hasSocket: socket_fd !== -1,
            mailnewsBase: mailnewsModule ? mailnewsModule.base.toString() : null
        };
    },
    checkhooks: function() {
        return {
            hasToServer: pToServer !== null,
            hasMsgTable: pCustomMsgTable !== null,
            hasSocket: socket_fd !== -1,
            mailnewsBase: mailnewsModule ? mailnewsModule.base.toString() : null
        };
    },
    check_hooks: function() {
        return {
            hasToServer: pToServer !== null,
            hasMsgTable: pCustomMsgTable !== null,
            hasSocket: socket_fd !== -1,
            mailnewsBase: mailnewsModule ? mailnewsModule.base.toString() : null
        };
    },

    nativeRequestArticles: function(groupId) {
        if (pToServer && pCustomMsgTable) {
            try {
                var toServer3 = new NativeFunction(pToServer, 'void', ['uint8', 'pointer', 'uint16']);
                toServer3(85, pCustomMsgTable, groupId);
                send({ type: 'log', data: 'Invoked ToServer(BP_REQ_ARTICLES, ' + groupId + ')' });
                return true;
            } catch (e) {
                send({ type: 'log', data: 'nativeRequestArticles error: ' + e });
            }
        }
        return false;
    },
    native_request_articles: function(groupId) {
        if (pToServer && pCustomMsgTable) {
            try {
                var toServer3 = new NativeFunction(pToServer, 'void', ['uint8', 'pointer', 'uint16']);
                toServer3(85, pCustomMsgTable, groupId);
                send({ type: 'log', data: 'Invoked ToServer(BP_REQ_ARTICLES, ' + groupId + ')' });
                return true;
            } catch (e) {
                send({ type: 'log', data: 'nativeRequestArticles error: ' + e });
            }
        }
        return false;
    },
    nativerequestarticles: function(groupId) {
        if (pToServer && pCustomMsgTable) {
            try {
                var toServer3 = new NativeFunction(pToServer, 'void', ['uint8', 'pointer', 'uint16']);
                toServer3(85, pCustomMsgTable, groupId);
                send({ type: 'log', data: 'Invoked ToServer(BP_REQ_ARTICLES, ' + groupId + ')' });
                return true;
            } catch (e) {
                send({ type: 'log', data: 'nativeRequestArticles error: ' + e });
            }
        }
        return false;
    },

    nativeRequestArticle: function(groupId, articleId) {
        if (pToServer && pCustomMsgTable) {
            try {
                var toServer4 = new NativeFunction(pToServer, 'void', ['uint8', 'pointer', 'uint16', 'uint32']);
                toServer4(86, pCustomMsgTable, groupId, articleId);
                return true;
            } catch (e) {
                send({ type: 'log', data: 'nativeRequestArticle error: ' + e });
            }
        }
        return false;
    },
    native_request_article: function(groupId, articleId) {
        if (pToServer && pCustomMsgTable) {
            try {
                var toServer4 = new NativeFunction(pToServer, 'void', ['uint8', 'pointer', 'uint16', 'uint32']);
                toServer4(86, pCustomMsgTable, groupId, articleId);
                return true;
            } catch (e) {
                send({ type: 'log', data: 'nativeRequestArticle error: ' + e });
            }
        }
        return false;
    },
    nativerequestarticle: function(groupId, articleId) {
        if (pToServer && pCustomMsgTable) {
            try {
                var toServer4 = new NativeFunction(pToServer, 'void', ['uint8', 'pointer', 'uint16', 'uint32']);
                toServer4(86, pCustomMsgTable, groupId, articleId);
                return true;
            } catch (e) {
                send({ type: 'log', data: 'nativeRequestArticle error: ' + e });
            }
        }
        return false;
    },

    nativeRequestLook: function(objectId) {
        if (pToServer && pCustomMsgTable) {
            try {
                var toServerLook = new NativeFunction(pToServer, 'void', ['uint8', 'pointer', 'uint32']);
                toServerLook(116, pCustomMsgTable, objectId);
                send({ type: 'log', data: 'Invoked ToServer(BP_REQ_LOOK, 0x' + objectId.toString(16) + ')' });
                return true;
            } catch (e) {
                send({ type: 'log', data: 'nativeRequestLook error: ' + e });
            }
        }
        return false;
    },
    native_request_look: function(objectId) {
        if (pToServer && pCustomMsgTable) {
            try {
                var toServerLook = new NativeFunction(pToServer, 'void', ['uint8', 'pointer', 'uint32']);
                toServerLook(116, pCustomMsgTable, objectId);
                send({ type: 'log', data: 'Invoked ToServer(BP_REQ_LOOK, 0x' + objectId.toString(16) + ')' });
                return true;
            } catch (e) {
                send({ type: 'log', data: 'nativeRequestLook error: ' + e });
            }
        }
        return false;
    },
    nativerequestlook: function(objectId) {
        if (pToServer && pCustomMsgTable) {
            try {
                var toServerLook = new NativeFunction(pToServer, 'void', ['uint8', 'pointer', 'uint32']);
                toServerLook(116, pCustomMsgTable, objectId);
                send({ type: 'log', data: 'Invoked ToServer(BP_REQ_LOOK, 0x' + objectId.toString(16) + ')' });
                return true;
            } catch (e) {
                send({ type: 'log', data: 'nativeRequestLook error: ' + e });
            }
        }
        return false;
    },

    injectPacket: function(packetData) {
        if (socket_fd !== -1) {
            try {
                var buf = Memory.alloc(packetData.length);
                buf.writeByteArray(packetData);
                var sendFunc = new NativeFunction(Module.findExportByName(null, 'send'), 'int', ['int', 'pointer', 'int', 'int']);
                sendFunc(socket_fd, buf, packetData.length, 0);
                return true;
            } catch (e) {}
        }
        return false;
    },
    injectpacket: function(packetData) {
        if (socket_fd !== -1) {
            try {
                var buf = Memory.alloc(packetData.length);
                buf.writeByteArray(packetData);
                var sendFunc = new NativeFunction(Module.findExportByName(null, 'send'), 'int', ['int', 'pointer', 'int', 'int']);
                sendFunc(socket_fd, buf, packetData.length, 0);
                return true;
            } catch (e) {}
        }
        return false;
    },
    inject_packet: function(packetData) {
        if (socket_fd !== -1) {
            try {
                var buf = Memory.alloc(packetData.length);
                buf.writeByteArray(packetData);
                var sendFunc = new NativeFunction(Module.findExportByName(null, 'send'), 'int', ['int', 'pointer', 'int', 'int']);
                sendFunc(socket_fd, buf, packetData.length, 0);
                return true;
            } catch (e) {}
        }
        return false;
    }
};
"""

class ArticleHeader:
    def __init__(self, art_num: int, timestamp: int, author: str, subject: str, newsgroup: str = "General_News", is_read: bool = False):
        self.num = art_num
        self.timestamp = timestamp
        self.author = author
        self.subject = subject
        self.newsgroup = newsgroup
        self.is_read = is_read
        self.date_str = ""
        try:
            if timestamp > 0:
                dt = datetime.datetime.fromtimestamp(timestamp + M59_EPOCH_OFFSET)
                self.date_str = dt.strftime("%a, %b %d, %Y, %H:%M")
        except Exception:
            self.date_str = f"Raw Time: {timestamp}"

    def to_dict(self):
        return {
            "id": self.num,
            "timestamp": self.timestamp,
            "datetime": self.date_str,
            "author": self.author,
            "subject": self.subject,
            "newsgroup": self.newsgroup,
            "is_read": self.is_read
        }

# -------------------------------------------------------------------------
# SQLite & JSON Archiver Database Layer
# -------------------------------------------------------------------------
class NewsGlobeDatabase:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, db_path=None):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(NewsGlobeDatabase, cls).__new__(cls)
                cls._instance._init_db(db_path)
            return cls._instance

    def _init_db(self, db_path=None):
        if db_path is None:
            settings_dir = "settings"
            if not os.path.exists(settings_dir):
                os.makedirs(settings_dir, exist_ok=True)
            db_path = os.path.join(settings_dir, "m59_news_globe.db")

            # Migration: Move database from data/ or root if present
            if not os.path.exists(db_path):
                old_data_db = os.path.join("data", "m59_news_globe.db")
                old_root_db = "m59_news_globe.db"
                if os.path.exists(old_data_db):
                    try:
                        shutil.move(old_data_db, db_path)
                        news_logger.info(f"DB MIGRATION: Moved {old_data_db} -> {db_path}")
                    except Exception as e:
                        news_logger.error(f"DB MIGRATION ERROR: {e}")
                elif os.path.exists(old_root_db):
                    try:
                        shutil.move(old_root_db, db_path)
                        news_logger.info(f"DB MIGRATION: Moved {old_root_db} -> {db_path}")
                    except Exception as e:
                        news_logger.error(f"DB MIGRATION ERROR: {e}")

        self.db_path = db_path
        self._create_tables()
        self.load_existing_json_archives()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _create_tables(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS globe_articles (
                    nid INTEGER NOT NULL,
                    article_num INTEGER NOT NULL,
                    timestamp INTEGER NOT NULL,
                    poster TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT,
                    is_read INTEGER DEFAULT 0,
                    fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (nid, article_num)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS mail_messages (
                    mail_index INTEGER PRIMARY KEY,
                    sender TEXT NOT NULL,
                    recipients TEXT NOT NULL,
                    msg_time INTEGER NOT NULL,
                    subject TEXT,
                    body TEXT,
                    is_read INTEGER DEFAULT 0,
                    received_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS newsgroup_sync_meta (
                    nid INTEGER PRIMARY KEY,
                    last_synced_at DATETIME,
                    total_articles INTEGER DEFAULT 0,
                    full_sync_completed INTEGER DEFAULT 0,
                    unread_count INTEGER DEFAULT 0,
                    permission INTEGER DEFAULT 0
                )
            """)

            # Migration: Ensure missing columns are added to existing SQLite database tables
            try:
                cursor.execute("PRAGMA table_info(globe_articles)")
                cols = [r['name'] for r in cursor.fetchall()]
                if 'is_read' not in cols:
                    cursor.execute("ALTER TABLE globe_articles ADD COLUMN is_read INTEGER DEFAULT 0")
                if 'fetched_at' not in cols:
                    cursor.execute("ALTER TABLE globe_articles ADD COLUMN fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP")

                cursor.execute("PRAGMA table_info(mail_messages)")
                mail_cols = [r['name'] for r in cursor.fetchall()]
                if 'is_read' not in mail_cols:
                    cursor.execute("ALTER TABLE mail_messages ADD COLUMN is_read INTEGER DEFAULT 0")
                if 'received_at' not in mail_cols:
                    cursor.execute("ALTER TABLE mail_messages ADD COLUMN received_at DATETIME DEFAULT CURRENT_TIMESTAMP")

                cursor.execute("PRAGMA table_info(newsgroup_sync_meta)")
                meta_cols = [r['name'] for r in cursor.fetchall()]
                if 'unread_count' not in meta_cols:
                    cursor.execute("ALTER TABLE newsgroup_sync_meta ADD COLUMN unread_count INTEGER DEFAULT 0")
                if 'permission' not in meta_cols:
                    cursor.execute("ALTER TABLE newsgroup_sync_meta ADD COLUMN permission INTEGER DEFAULT 0")
                if 'full_sync_completed' not in meta_cols:
                    cursor.execute("ALTER TABLE newsgroup_sync_meta ADD COLUMN full_sync_completed INTEGER DEFAULT 0")
                if 'total_articles' not in meta_cols:
                    cursor.execute("ALTER TABLE newsgroup_sync_meta ADD COLUMN total_articles INTEGER DEFAULT 0")
            except Exception as e:
                console_log(f"Migration check error: {e}", "WARN")

            conn.commit()

    def load_existing_json_archives(self):
        """Loads legacy JSON archive entries into DB if DB is empty."""
        paths_to_check = [ARCHIVE_JSON_PATH, "General_News.json", "Designers_News.json"]
        for path in paths_to_check:
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        for item in data:
                            aid = item.get("id")
                            if aid:
                                ng_str = item.get("newsgroup", "General_News")
                                nid = self.get_nid_from_name(ng_str)
                                timestamp = item.get("timestamp", 0)
                                author = item.get("author", "Unknown")
                                subject = item.get("subject", "No Subject")
                                body = item.get("body", "")
                                if body == "[Body not retrieved yet]":
                                    body = None
                                is_read = 1 if item.get("is_read") else 0
                                self.upsert_article_header(nid, aid, timestamp, author, subject, is_read=is_read)
                                if body:
                                    self.update_article_body(nid, aid, body)
                except Exception as e:
                    console_log(f"Error loading JSON archive '{path}': {e}", "WARN")

    def save_json_archives(self):
        """Exports DB contents to JSON files to match user's archival requirements."""
        try:
            records = []
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM globe_articles ORDER BY timestamp DESC, article_num DESC")
                for r in cursor.fetchall():
                    nid = r['nid']
                    ng_name = NEWSGROUP_NAMES.get(nid, f"Newsgroup_{nid}")
                    hdr = ArticleHeader(r['article_num'], r['timestamp'], r['poster'], r['title'], ng_name, is_read=bool(r['is_read']))
                    entry = hdr.to_dict()
                    entry['body'] = r['body'] if r['body'] else "[Body not retrieved yet]"
                    records.append(entry)

            with open(ARCHIVE_JSON_PATH, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2, ensure_ascii=False)

            for ng_name, nid_val in [("General_News", 20), ("Designers_News", 9)]:
                ng_records = [r for r in records if r.get("newsgroup") == ng_name or self.get_nid_from_name(r.get("newsgroup")) == nid_val]
                with open(f"{ng_name}.json", "w", encoding="utf-8") as f:
                    json.dump(ng_records, f, indent=2, ensure_ascii=False)
        except Exception as e:
            console_log(f"Error writing JSON archives: {e}", "WARN")

    def get_nid_from_name(self, name: str) -> int:
        if "designers" in name.lower() or name == "Designers_News":
            return NID_ANNOUNCEMENTS
        elif "general" in name.lower() or name == "General_News":
            return NID_TOS_HALL
        elif "adventure" in name.lower():
            return NID_ADVENTURE
        elif "justicar" in name.lower():
            return NID_JUSTICAR
        elif "game" in name.lower():
            return NID_GAME
        elif "event" in name.lower():
            return NID_EVENT_SCHEDULE
        return NID_TOS_HALL

    def upsert_article_header(self, nid, article_num, timestamp, poster, title, is_read=0):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO globe_articles (nid, article_num, timestamp, poster, title, body, is_read, fetched_at)
                VALUES (?, ?, ?, ?, ?, NULL, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(nid, article_num) DO UPDATE SET
                    timestamp = excluded.timestamp,
                    poster = excluded.poster,
                    title = excluded.title
            """, (int(nid), int(article_num), int(timestamp), str(poster), str(title), int(is_read)))
            conn.commit()

    def update_article_body(self, nid, article_num, body_text):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE globe_articles
                SET body = ?, fetched_at = CURRENT_TIMESTAMP
                WHERE nid = ? AND article_num = ?
            """, (str(body_text), int(nid), int(article_num)))
            conn.commit()
        self.save_json_archives()

    def mark_article_read(self, nid, article_num):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE globe_articles SET is_read = 1 WHERE nid = ? AND article_num = ?", (int(nid), int(article_num)))
            conn.commit()

    def get_articles(self, nid, limit=250, offset=0):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT nid, article_num, timestamp, poster, title, body, is_read, fetched_at
                FROM globe_articles
                WHERE nid = ?
                ORDER BY timestamp DESC, article_num DESC
                LIMIT ? OFFSET ?
            """, (int(nid), int(limit), int(offset)))
            return [dict(r) for r in cursor.fetchall()]

    def get_missing_body_article_ids(self, nid):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT article_num FROM globe_articles
                WHERE nid = ? AND (body IS NULL OR body = '' OR body = '[Body not retrieved yet]')
                ORDER BY timestamp DESC
            """, (int(nid),))
            return [r['article_num'] for r in cursor.fetchall()]

    def is_full_sync_completed(self, nid) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT full_sync_completed FROM newsgroup_sync_meta WHERE nid = ?", (int(nid),))
            row = cursor.fetchone()
            return bool(row and row['full_sync_completed'])

    def set_full_sync_completed(self, nid, status=True):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO newsgroup_sync_meta (nid, full_sync_completed, last_synced_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(nid) DO UPDATE SET
                    full_sync_completed = excluded.full_sync_completed,
                    last_synced_at = CURRENT_TIMESTAMP
            """, (int(nid), 1 if status else 0))
            conn.commit()

    def get_unread_count(self, nid) -> int:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) as unread FROM globe_articles WHERE nid = ? AND is_read = 0", (int(nid),))
                row = cursor.fetchone()
                return row['unread'] if row else 0
        except Exception as e:
            console_log(f"Error reading unread count for NID {nid}: {e}", "WARN")
            return 0

    def get_globe_article_counts(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT nid, COUNT(*) as count FROM globe_articles GROUP BY nid")
            counts = {int(r['nid']): int(r['count']) for r in cursor.fetchall()}
            for nid in NEWSGROUP_NAMES.keys():
                if nid not in counts:
                    counts[nid] = 0
            return counts

    # Mail CRUD
    def upsert_mail_message(self, mail_index, sender, recipients, msg_time, subject, body):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO mail_messages (mail_index, sender, recipients, msg_time, subject, body, received_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(mail_index) DO UPDATE SET
                    sender = excluded.sender,
                    recipients = excluded.recipients,
                    msg_time = excluded.msg_time,
                    subject = excluded.subject,
                    body = excluded.body
            """, (int(mail_index), str(sender), str(recipients), int(msg_time), str(subject), str(body)))
            conn.commit()

    def get_mail_messages(self, limit=100, offset=0):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT mail_index, sender, recipients, msg_time, subject, body, is_read, received_at
                FROM mail_messages
                ORDER BY msg_time DESC, mail_index DESC
                LIMIT ? OFFSET ?
            """, (int(limit), int(offset)))
            return [dict(r) for r in cursor.fetchall()]

    def delete_mail_message(self, mail_index):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM mail_messages WHERE mail_index = ?", (int(mail_index),))
            conn.commit()

# -------------------------------------------------------------------------
# Frida Process Finder & Attachment
# -------------------------------------------------------------------------
def find_meridian_game_process() -> Tuple[Optional[int], Optional[str], Optional[int]]:
    target_executables = {"meridian.exe", "merid32.exe", "meridian_3d.exe"}
    if HAS_FRIDA:
        try:
            device = frida.get_local_device()
            processes = device.enumerate_processes()
            for proc in processes:
                pname = proc.name.lower()
                if pname in target_executables or (pname.startswith("meridian") and pname.endswith(".exe")):
                    return proc.pid, proc.name, None
        except Exception:
            pass

    if HAS_WIN32:
        res = [None, None, None]
        def enum_cb(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                tl = title.lower()
                if "meridian 59" in tl or "meridian" in tl:
                    blacklist = ["discord", "chrome", "firefox", "edge", "terminal", "python"]
                    if not any(kw in tl for kw in blacklist):
                        _, pid = win32process.GetWindowThreadProcessId(hwnd)
                        res[0] = pid
                        res[1] = title
                        res[2] = hwnd
        try:
            win32gui.EnumWindows(enum_cb, None)
        except Exception:
            pass
        if res[0]:
            return res[0], res[1], res[2]

    return None, None, None

# -------------------------------------------------------------------------
# Room & Location Resolvers
# -------------------------------------------------------------------------
def get_globes_for_room_name(room_name: str, rid: Optional[str] = None) -> List[Dict]:
    """Returns list of newsglobe dicts present in specified room."""
    if not room_name and not rid:
        return []

    r_raw = str(room_name).strip() if room_name else ""
    r_clean = r_raw.lower().replace("'", "").replace("’", "").replace("the ", "")
    rid_str = str(rid).strip() if rid else ""

    results = []

    for loc in LOCATIONS_TABLE:
        # 1. RID exact match
        if rid_str and loc.get("rid") == rid_str:
            results.append(loc)
            continue

        # 2. Alias match
        matched = False
        for alias in loc.get("aliases", []):
            alias_clean = alias.lower().replace("'", "").replace("’", "").replace("the ", "")
            if alias_clean in r_clean or r_clean in alias_clean:
                results.append(loc)
                matched = True
                break
        if matched:
            continue

        # 3. Required keywords match (e.g. ['jasper', 'hall'])
        keywords = loc.get("keywords", [])
        if keywords:
            clean_kws = [kw.lower().replace("'", "").replace("’", "") for kw in keywords]
            if all(kw in r_clean for kw in clean_kws):
                results.append(loc)
                continue

        # 4. Fallback room name substring match
        loc_room_clean = loc["room"].lower().replace("'", "").replace("’", "").replace("the ", "")
        if loc_room_clean in r_clean or r_clean in loc_room_clean:
            results.append(loc)

    unique_globes = []
    seen_nids = set()
    for g in results:
        if g["nid"] not in seen_nids:
            seen_nids.add(g["nid"])
            unique_globes.append(g)
    return unique_globes

get_news_globes_for_room = get_globes_for_room_name

def format_hex(data) -> str:
    if not data:
        return ""
    if isinstance(data, (bytes, bytearray)):
        return ' '.join(f'{b:02X}' for b in data)
    return str(data)

def resolve_room_id(room_name: str, gps_manager=None) -> Optional[str]:
    if not room_name:
        return None
    if gps_manager and hasattr(gps_manager, 'resolve_name_to_rid'):
        rid = gps_manager.resolve_name_to_rid(room_name)
        if rid:
            return rid
    r_clean = room_name.lower().replace("'", "").replace("’", "").replace("the ", "")
    for loc in LOCATIONS_TABLE:
        for alias in loc.get("aliases", []):
            if alias.lower().replace("'", "").replace("’", "").replace("the ", "") in r_clean:
                return loc["rid"]
    return None

def get_all_news_groups(db=None) -> List[Dict]:
    groups = []
    seen_nids = set()
    for loc in LOCATIONS_TABLE:
        nid = loc["nid"]
        if nid not in seen_nids:
            seen_nids.add(nid)
            count = 0
            if db and hasattr(db, 'get_article_count'):
                count = db.get_article_count(nid)
            groups.append({
                "nid": nid,
                "name": loc["name"],
                "count": count,
                "description": loc["type"]
            })
    return groups

# -------------------------------------------------------------------------
# NewsSyncEngine: Location-Aware Automated Sync Engine
# -------------------------------------------------------------------------
class NewsSyncEngine:
    def __init__(self, db=None, gps_manager=None, packet_injector=None):
        self.db = db or NewsGlobeDatabase()
        self.gps_manager = gps_manager
        self.packet_injector = packet_injector
        self.current_room_name = None
        self.current_rid = None
        self.accessible_globes = []
        
        self.frida_session = None
        self.frida_script = None
        self.frida_attached = False

        self.pending_body_queue: List[Tuple[int, int]] = []
        self.is_syncing = False
        self.article_received_event = threading.Event()
        self.callbacks = []

        self.init_frida_attachment()

    def register_update_callback(self, cb):
        if cb not in self.callbacks:
            self.callbacks.append(cb)

    def _notify(self, event_type, data):
        for cb in self.callbacks:
            try:
                cb(event_type, data)
            except Exception as e:
                news_logger.error(f"Callback error ({event_type}): {e}")

    def init_frida_attachment(self):
        if not HAS_FRIDA:
            return
        pid, win_title, hwnd = find_meridian_game_process()
        if pid:
            try:
                self.frida_session = frida.attach(pid)
                self.frida_script = self.frida_session.create_script(FRIDA_HARVESTER_SCRIPT)
                self.frida_script.on('message', self._on_frida_message)
                self.frida_script.load()
                self.frida_attached = True
                console_log(f"Frida attached successfully to Meridian 59 (PID {pid}).", "FRIDA")
            except Exception as e:
                console_log(f"Frida attachment exception: {e}", "WARN")

    def _call_frida_export(self, method_name, *args):
        if not self.frida_script or not self.frida_attached:
            return False
        candidates = [
            method_name,
            method_name.lower(),
            method_name.replace("_", ""),
            "".join(w.capitalize() if i > 0 else w for i, w in enumerate(method_name.split("_"))),
            ''.join(['_' + c.lower() if c.isupper() else c for c in method_name]).lstrip('_')
        ]
        exp_target = getattr(self.frida_script, "exports_sync", None)
        if exp_target is None:
            exp_target = getattr(self.frida_script, "exports", None)

        if exp_target is not None:
            for cand in candidates:
                try:
                    fn = getattr(exp_target, cand, None)
                    if fn is not None:
                        res = fn(*args)
                        return res if res is not None else True
                except Exception as e:
                    pass
        return False

    def inject_packet(self, packet_bytes: bytes) -> bool:
        """Injects raw binary packet into Meridian 59 client via Frida exports."""
        if not packet_bytes:
            return False
        return self._call_frida_export("injectPacket", list(packet_bytes))

    def _on_frida_message(self, message, data):
        if message.get('type') == 'send':
            payload = message.get('payload', {})
            ptype = payload.get('type')
            if ptype == 'unscrambled_msg' and data:
                opcode = payload.get('opcode', 0)
                self.handle_unscrambled_packet(opcode, data)

    def handle_unscrambled_packet(self, opcode: int, chunk: bytes):
        def _read_str(data: bytes, offset: int) -> Tuple[str, int]:
            if offset >= len(data):
                return "", offset
            if offset + 2 <= len(data):
                slen = struct.unpack_from("<H", data, offset)[0]
                if 0 < slen <= (len(data) - offset - 2):
                    s = data[offset+2 : offset+2+slen].decode('latin-1', 'replace').rstrip('\x00')
                    return s, offset + 2 + slen
            end = data.find(b'\x00', offset)
            if end != -1:
                s = data[offset:end].decode('latin-1', 'replace')
                return s, end + 1
            s = data[offset:].decode('latin-1', 'replace')
            return s, len(data)

        if opcode == 181 and len(chunk) >= 7:
            try:
                nid, part, max_part, num_articles = struct.unpack_from("<HBBH", chunk, 1)
                ptr = 7
                ng_name = NEWSGROUP_NAMES.get(nid, f"Newsgroup_{nid}")
                new_headers_count = 0

                for _ in range(num_articles):
                    if ptr + 8 > len(chunk): break
                    art_id, art_time = struct.unpack_from("<II", chunk, ptr)
                    ptr += 8

                    author, ptr = _read_str(chunk, ptr)
                    subject, ptr = _read_str(chunk, ptr)

                    self.db.upsert_article_header(nid, art_id, art_time, author, subject)
                    new_headers_count += 1

                    if subject.lower().startswith("re:") or subject.lower().startswith("re "):
                        self._notify("reply_notification", {"nid": nid, "newsgroup": ng_name, "poster": author, "title": subject})

                console_log(f"Received {new_headers_count} article headers for {ng_name} (NID {nid}).", "INDEX")
                self._notify("articles_indexed", {"nid": nid, "count": new_headers_count})

                if part == max_part:
                    self.process_post_catalog_sync(nid)

            except Exception as e:
                news_logger.error(f"Error parsing BP_ARTICLES: {e}")

        elif opcode == 182 and len(chunk) >= 3:
            try:
                body_len = struct.unpack_from("<H", chunk, 1)[0]
                body_text = chunk[3:3+body_len].decode('latin-1', 'replace')
                
                if hasattr(self, 'current_fetching_item') and self.current_fetching_item:
                    nid, aid = self.current_fetching_item
                    self.db.update_article_body(nid, aid, body_text)
                    console_log(f"Archived Article [{aid}] body ({len(body_text)} chars).", "DOWNLOAD")
                    self._notify("article_body_received", {"nid": nid, "article_num": aid})
                    self.article_received_event.set()
            except Exception as e:
                news_logger.error(f"Error parsing BP_ARTICLE: {e}")

        elif opcode == 80 and len(chunk) >= 6:
            try:
                idx = struct.unpack_from("<I", chunk, 1)[0]
                offset = 5
                s_end = chunk.find(b'\x00', offset)
                if s_end != -1:
                    sender = chunk[offset:s_end].decode('latin-1', 'replace')
                    offset = s_end + 1
                    msg_time, num_recip = struct.unpack_from("<IH", chunk, offset)
                    offset += 6
                    recips = []
                    for _ in range(num_recip):
                        r_end = chunk.find(b'\x00', offset)
                        if r_end != -1:
                            recips.append(chunk[offset:r_end].decode('latin-1', 'replace'))
                            offset = r_end + 1
                    offset += 4
                    raw_p = chunk[offset:].decode('latin-1', 'replace').strip('\x00')
                    parts = raw_p.split('\n', 2)
                    to_names = parts[0] if len(parts) > 0 else ", ".join(recips)
                    subject = parts[1] if len(parts) > 1 else "(No Subject)"
                    body = parts[2] if len(parts) > 2 else ""
                    self.db.upsert_mail_message(idx, sender, to_names, msg_time, subject, body)
                    self._notify("mail_received", {"index": idx, "sender": sender, "subject": subject})
            except Exception as e:
                news_logger.error(f"Error parsing BP_MAIL: {e}")

    def on_room_changed(self, room_name: str, rid: Optional[str] = None):
        """Called when character changes room in game."""
        if not room_name or room_name == self.current_room_name:
            return

        self.current_room_name = room_name
        self.current_rid = rid
        self.accessible_globes = get_globes_for_room_name(room_name, rid)

        console_log(f"Room Changed: '{room_name}' (RID: {rid}). Accessible Globes: {len(self.accessible_globes)}", "LOCATION")
        self._notify("room_changed", {
            "room_name": self.current_room_name,
            "rid": self.current_rid,
            "globes": self.accessible_globes
        })

        if self.accessible_globes:
            self.trigger_sync_for_accessible_globes()

    def trigger_sync_for_accessible_globes(self):
        """Requests headers for globes present in room."""
        for globe in self.accessible_globes:
            nid = globe["nid"]
            console_log(f"Triggering header scan for NID {nid} ({globe['name']})...", "SYNC")
            if not self._call_frida_export("nativeRequestArticles", nid):
                if self.packet_injector:
                    pkt = struct.pack("<BH", BP_REQ_ARTICLES, nid)
                    self.packet_injector(pkt)

    def process_post_catalog_sync(self, nid: int):
        """Handles First-Time Run prompt vs Automatic Incremental Sync."""
        missing = self.db.get_missing_body_article_ids(nid)
        ng_name = NEWSGROUP_NAMES.get(nid, f"Newsgroup_{nid}")

        if not missing:
            console_log(f"Newsgroup {ng_name} (NID {nid}) is fully up to date.", "SUCCESS")
            return

        is_first_time = not self.db.is_full_sync_completed(nid)

        if is_first_time:
            console_log(f"First-time run detected for {ng_name} (NID {nid}). Prompting user for {len(missing)} articles...", "PROMPT")
            self._notify("prompt_first_time_sync", {
                "nid": nid,
                "newsgroup_name": ng_name,
                "total_count": len(missing)
            })
        else:
            console_log(f"Incremental sync: Automatically downloading {len(missing)} missing articles for {ng_name}...", "AUTO_SYNC")
            self.start_body_download_queue(nid, missing)

    def confirm_first_time_sync(self, nid: int):
        """Invoked when user accepts the first-time run sync prompt."""
        self.db.set_full_sync_completed(nid, True)
        missing = self.db.get_missing_body_article_ids(nid)
        if missing:
            self.start_body_download_queue(nid, missing)

    def start_body_download_queue(self, nid: int, article_ids: List[int]):
        """Paced body downloading queue."""
        def worker():
            self.is_syncing = True
            console_log(f"Starting download queue for NID {nid} ({len(article_ids)} articles)...", "DOWNLOAD")
            for aid in article_ids:
                self.current_fetching_item = (nid, aid)
                self.article_received_event.clear()

                res = self._call_frida_export("nativeRequestArticle", nid, aid)
                if not res and self.packet_injector:
                    pkt = struct.pack("<BHI", BP_REQ_ARTICLE, nid, aid)
                    self.packet_injector(pkt)

                received = self.article_received_event.wait(timeout=1.8)
                if not received:
                    raw_log(f"Timeout waiting for body NID {nid} AID {aid}")
                
                time.sleep(0.075)

            self.is_syncing = False
            self.current_fetching_item = None
            console_log(f"Download queue completed for NID {nid}.", "SUCCESS")
            self.db.save_json_archives()

        t = threading.Thread(target=worker, daemon=True)
        t.start()
