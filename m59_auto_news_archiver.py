#!/usr/bin/env python3
"""
===============================================================================
 MERIDIAN 59 AUTONOMOUS GLOBE NEWS HARVESTER & ARCHIVER (AUTO-DETECT EDITION)
 Target: Meridian 59 (Classic & D3D Client)
 Synchronized with companion app dataset: data/meridian_rooms_dataset.json

 KEY IMPROVEMENTS OVER TEST SCRIPT:
  1. 100% True Room Names & RIDs: All 18 newsgroup locations are synchronized
     directly with `data/meridian_rooms_dataset.json`. No more informal nicknames.
  2. Autonomous Room Detection: Zero prompt required on launch. Tracks the game
     window title in real-time (via Frida user32!SetWindowText hooks and Win32 /
     ctypes polling). When player is in or enters any town inn, adventurer hall,
     or news venue, the room is recognized instantly.
  3. Automatic Message Pulling: Upon entering a newsroom, the script automatically
     updates the active Newsgroup ID (NID), triggers catalog index synchronization
     (BP_REQ_ARTICLES), and queues automatic downloads of all unread/missing
     article bodies (BP_REQ_ARTICLE) into the local JSON archive.
  4. Non-Blocking CLI & Manual Override: Player can move freely through the game world;
     the script automatically adapts. Manual location picker ('m') remains as an optional
     fallback for custom NIDs.
===============================================================================
"""

import sys
import os
import time
import datetime
import json
import threading
import struct
import re
import ctypes
import sqlite3
from typing import Dict, List, Optional, Tuple, Set, Any, Callable

# Windows API Imports & ctypes fallback
try:
    import win32gui
    import win32process
    import win32con
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

# Frida Dynamic Binary Instrumentation
try:
    import frida
    HAS_FRIDA = True
except ImportError:
    HAS_FRIDA = False

M59_EPOCH_OFFSET = 1534000000

def get_db_path() -> str:
    """
    Resolves the SQLite database path to settings/m59_companion.db.
    Ensures the parent directory exists.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cand1 = os.path.join(script_dir, "settings", "m59_companion.db")
    if os.path.isdir(os.path.join(script_dir, "settings")):
        os.makedirs(os.path.dirname(cand1), exist_ok=True)
        return cand1
    cand2 = os.path.join(os.getcwd(), "settings", "m59_companion.db")
    if os.path.isdir(os.path.join(os.getcwd(), "settings")):
        os.makedirs(os.path.dirname(cand2), exist_ok=True)
        return cand2
    os.makedirs(os.path.dirname(cand1), exist_ok=True)
    return cand1

DB_PATH = get_db_path()

# Global database reference for silent trace/event recording without disk log files
db_instance: Optional['NewsDatabase'] = None

# Global console log toggle (Disabled by default to prevent stdout flood)
ENABLE_NEWS_CONSOLE_LOGS: bool = False

def set_news_logging_enabled(enabled: bool):
    """Dynamically enables or disables verbose news wire & trace stdout output."""
    global ENABLE_NEWS_CONSOLE_LOGS
    ENABLE_NEWS_CONSOLE_LOGS = bool(enabled)

def is_news_logging_enabled() -> bool:
    """Returns whether news console output is enabled."""
    return ENABLE_NEWS_CONSOLE_LOGS

def raw_log(msg: str):
    """Logs raw wire transactions to SQLite, and to terminal only if enabled."""
    if ENABLE_NEWS_CONSOLE_LOGS:
        print(f"[RAW] {msg}", flush=True)
    if db_instance is not None:
        db_instance.log_event("RAW_WIRE", msg)

def trace_log(msg: str):
    """Logs packet traces and annotations to SQLite, and to terminal only if enabled."""
    if ENABLE_NEWS_CONSOLE_LOGS:
        print(f"[TRACE] {msg}", flush=True)
    if db_instance is not None:
        db_instance.log_event("PACKET_TRACE", msg)

def console_log(msg: str, prefix: str = "INFO"):
    """Outputs to terminal console (if enabled or essential) and registers into SQLite news_logs."""
    if ENABLE_NEWS_CONSOLE_LOGS or prefix in ("ERR", "CRIT", "SYSTEM", "SQLITE_READY"):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        formatted = f"[{timestamp}] [{prefix}] {msg}"
        print(formatted, flush=True)
    if db_instance is not None:
        db_instance.log_event(prefix, msg)

# =============================================================================
# DATASET LOADER & HARMONIZED LOCATIONS TABLE
# =============================================================================

def load_meridian_rooms_dataset() -> Dict[str, Any]:
    """Loads the authoritative Meridian 59 room dataset."""
    search_paths = [
        os.path.join(os.path.dirname(__file__), "data", "meridian_rooms_dataset.json"),
        os.path.join("data", "meridian_rooms_dataset.json"),
        "meridian_rooms_dataset.json",
        os.path.join(os.path.dirname(__file__), "meridian_rooms_dataset.json")
    ]
    for p in search_paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data
            except Exception as e:
                console_log(f"Failed loading rooms dataset from '{p}': {e}", "WARN")
    return {}

WORLD_ROOMS = load_meridian_rooms_dataset()
ROOM_NAME_TO_RID: Dict[str, str] = {v.get("name", "").lower(): k for k, v in WORLD_ROOMS.items() if v.get("name")}

# Exact matching table aligned with meridian_rooms_dataset.json:
LOCATIONS_TABLE: List[Dict[str, Any]] = [
    # Inns (Designers' News / Announcements - NID 9)
    {"id": 1, "name": "Jasper Inn (Yonder Inn)", "nid": 9, "type": "Designers_News", "room": "Yonder Inn of Jasper", "rid": "RID_JAS_INN"},
    {"id": 2, "name": "Tos Inn (Familiars)", "nid": 9, "type": "Designers_News", "room": "Familiars", "rid": "RID_TOS_INN"},
    {"id": 3, "name": "Barloque Inn (Brownestone Inn)", "nid": 9, "type": "Designers_News", "room": "Brownestone Inn", "rid": "RID_BAR_INN"},
    {"id": 4, "name": "Cor Noth Inn (Cibilo Creek Inn)", "nid": 9, "type": "Designers_News", "room": "Cibilo Creek Inn", "rid": "RID_COR_INN"},
    {"id": 5, "name": "Ko'catan Inn (The Aerie Guest House)", "nid": 9, "type": "Designers_News", "room": "The Aerie Guest House", "rid": "RID_KOC_INN"},
    {"id": 6, "name": "Marion Inn (The Limping Toad Inn)", "nid": 9, "type": "Designers_News", "room": "The Limping Toad Inn and Tavern", "rid": "RID_MAR_INN"},
    {"id": 7, "name": "Raza Inn (Starter Inn)", "nid": 9, "type": "Designers_News", "room": "Raza Inn", "rid": "RID_NEWB1"},

    # Adventurer Halls (General / Hall News - NID 20)
    {"id": 8, "name": "Jasper Hall (Adventurer's Hall)", "nid": 20, "type": "General_News", "room": "Adventurer's Hall of Jasper", "rid": "RID_JAS_HALL"},
    {"id": 9, "name": "Tos Hall (Adventurer's Hall)", "nid": 20, "type": "General_News", "room": "The Adventurer's Hall of Tos", "rid": "RID_TOS_HALL"},
    {"id": 10, "name": "Barloque Hall (Adventurer's Hall)", "nid": 20, "type": "General_News", "room": "Adventurer's Hall of Barloque", "rid": "RID_BAR_HALL"},
    {"id": 11, "name": "Cor Noth Hall (Adventurer's Hall)", "nid": 20, "type": "General_News", "room": "The Adventurer's Hall of Cor Noth", "rid": "RID_COR_HALL"},
    {"id": 12, "name": "Ko'catan Hall (The Hall of Heroes)", "nid": 20, "type": "General_News", "room": "The Hall of Heroes", "rid": "RID_KOC_HALL_OF_HEROES"},
    {"id": 13, "name": "Marion Hall (Adventurer's Hall)", "nid": 20, "type": "General_News", "room": "The Adventurer's Hall of Marion", "rid": "RID_MAR_HALL"},

    # Special Newsgroups
    {"id": 14, "name": "Jasper Tavern (Tales of Adventure)", "nid": 5, "type": "Tales_of_Adventure", "room": "Pietro's Wicked Brews", "rid": "RID_JAS_TAVERN"},
    {"id": 15, "name": "Tos Grey Dragon (Game News)", "nid": 3, "type": "Game_News", "room": "Abandoned Building", "rid": "RID_TOS_GREY"},
    {"id": 16, "name": "Barloque Court (Book of Jala / Justicar)", "nid": 4, "type": "Justicar_News", "room": "Office of the Justicar", "rid": "RID_BAR_COURT"},
    {"id": 17, "name": "Barloque GM Hall (Guild Charter)", "nid": 10, "type": "Guild_Charter", "room": "The Guildmaster's Hall", "rid": "RID_GM_HALL"},
    {"id": 18, "name": "Marion Elder (Event Schedule)", "nid": 6, "type": "Event_Schedule", "room": "The home of the elder", "rid": "RID_MAR_ELDER_HUT"},
]

# Fast lookup by lower-case room name and RID
NEWS_ROOM_BY_NAME: Dict[str, Dict[str, Any]] = {item["room"].lower(): item for item in LOCATIONS_TABLE}
NEWS_ROOM_BY_RID: Dict[str, Dict[str, Any]] = {item["rid"].upper(): item for item in LOCATIONS_TABLE}

# =============================================================================
# PRECISION REVERSE-ENGINEERED FRIDA INJECTION SCRIPT
# =============================================================================

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

// 1. Resolve Winsock Primitives
try {
    pSend = findModuleExport("ws2_32.dll", "send") || findModuleExport("wsock32.dll", "send");
    pRecv = findModuleExport("ws2_32.dll", "recv") || findModuleExport("wsock32.dll", "recv");
} catch (e) {
    send({ type: 'log', data: "Winsock lookup error: " + e });
}

// 2. Enumerate loaded modules safely
var modules = [];
try {
    modules = Process.enumerateModules();
} catch (e) {
    send({ type: 'log', data: 'Error enumerating modules: ' + e });
}

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

if (pToServer) {
    send({ type: 'log', data: 'Located ToServer export at ' + pToServer });
}

// 3. Dynamically construct complete ClientMsgTable in memory for mail & news
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

    // Entry 4: End of table (0)
    pCustomMsgTable.add(68).writeU8(0);
    send({ type: 'log', data: 'Successfully allocated custom ClientMsgTable at ' + pCustomMsgTable });
} catch (e) {
    send({ type: 'log', data: 'Error creating custom MsgTable: ' + e });
}

// 4. Resolve Export by Ordinal directly from 32-bit PE Header in memory
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
            if (funcRva !== 0) {
                return modBase.add(funcRva);
            }
        }
    } catch (e) {
        send({ type: 'log', data: 'Error resolving ordinal ' + ordinal + ': ' + e });
    }
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
            send({ type: 'log', data: 'Attached EventServerMessage hook in ' + mod.name + ' at ' + pTarget });
        }
    } catch (e) {
        send({ type: 'log', data: 'Failed attaching to ' + mod.name + ': ' + e });
    }
}

// Dynamic mailnews.dll hooker
var mailnewsHooked = false;

function ensureMailnewsHooked() {
    if (mailnewsHooked) return true;
    try {
        var currentModules = Process.enumerateModules();
        for (var mIdx = 0; mIdx < currentModules.length; mIdx++) {
            var m = currentModules[mIdx];
            if (m && m.name && m.name.toLowerCase().indexOf("mailnews") !== -1) {
                mailnewsModule = m;
                hookModuleServerMessage(m);
                mailnewsHooked = true;
                send({ type: 'log', data: 'Dynamically hooked mailnews.dll at ' + m.base });
                return true;
            }
        }
    } catch (e) {
        send({ type: 'log', data: 'ensureMailnewsHooked error: ' + e });
    }
    return false;
}

// Initial hook attempt
ensureMailnewsHooked();

// Hook LoadLibrary so whenever mailnews.dll is loaded on room entry, it is hooked immediately
function hookLoadLib(fnName) {
    var pFn = findModuleExport("kernel32.dll", fnName) || findModuleExport("kernelbase.dll", fnName);
    if (pFn) {
        try {
            Interceptor.attach(pFn, {
                onLeave: function(retval) {
                    if (!retval.isNull() && !mailnewsHooked) {
                        ensureMailnewsHooked();
                    }
                }
            });
        } catch (e) {}
    }
}
hookLoadLib("LoadLibraryA");
hookLoadLib("LoadLibraryW");
hookLoadLib("LoadLibraryExA");
hookLoadLib("LoadLibraryExW");

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

// 6. Hook SetWindowTextA / SetWindowTextW for Instant Room Detection
try {
    var pSetWindowTextA = findModuleExport("user32.dll", "SetWindowTextA");
    var pSetWindowTextW = findModuleExport("user32.dll", "SetWindowTextW");
    if (pSetWindowTextA) {
        Interceptor.attach(pSetWindowTextA, {
            onEnter: function(args) {
                try {
                    var str = args[1].readAnsiString();
                    if (str && str.length > 0) {
                        send({ type: 'window_title_changed', title: str });
                    }
                } catch(e) {}
            }
        });
    }
    if (pSetWindowTextW) {
        Interceptor.attach(pSetWindowTextW, {
            onEnter: function(args) {
                try {
                    var str = args[1].readUtf16String();
                    if (str && str.length > 0) {
                        send({ type: 'window_title_changed', title: str });
                    }
                } catch(e) {}
            }
        });
    }
} catch (e) {}

// 6b. Startup Window Title Recovery (for Wine/Linux compatibility)
try {
    var pGetWindowTextW = findModuleExport("user32.dll", "GetWindowTextW");
    var pGetWindowTextLengthW = findModuleExport("user32.dll", "GetWindowTextLengthW");
    var pEnumWindows = findModuleExport("user32.dll", "EnumWindows");
    var pGetWindowThreadProcessId = findModuleExport("user32.dll", "GetWindowThreadProcessId");

    if (pEnumWindows && pGetWindowThreadProcessId && pGetWindowTextW && pGetWindowTextLengthW) {
        var GetWindowThreadProcessId = new NativeFunction(pGetWindowThreadProcessId, 'uint32', ['pointer', 'pointer']);
        var GetWindowTextLengthW = new NativeFunction(pGetWindowTextLengthW, 'int', ['pointer']);
        var GetWindowTextW = new NativeFunction(pGetWindowTextW, 'int', ['pointer', 'pointer', 'int']);
        var myPid = Process.id;

        var abi = Process.pointerSize === 4 ? 'stdcall' : 'default';
        var enumWindowsProc = new NativeCallback(function (hwnd, lParam) {
            var pPid = Memory.alloc(4);
            GetWindowThreadProcessId(hwnd, pPid);
            var pid = pPid.readU32();
            if (pid === myPid) {
                var len = GetWindowTextLengthW(hwnd);
                if (len > 0) {
                    var buf = Memory.alloc((len + 1) * 2);
                    GetWindowTextW(hwnd, buf, len + 1);
                    var title = buf.readUtf16String();
                    if (title && (title.indexOf("Meridian") !== -1 || title.indexOf(" --- ") !== -1)) {
                        send({ type: 'window_title_changed', title: title });
                        return 0; // stop enumeration (FALSE)
                    }
                }
            }
            return 1; // continue (TRUE)
        }, 'int', ['pointer', 'pointer'], abi);

        var EnumWindows = new NativeFunction(pEnumWindows, 'int', ['pointer', 'pointer']);
        EnumWindows(enumWindowsProc, ptr(0));
    }
} catch (e) {
    send({ type: 'log', data: "Error getting initial window title: " + e });
}

// 7. Winsock Sniffing & Active Socket Tracking
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

// 8. Native RPC Exports
rpc.exports = {
    ensureHooks: function() {
        return ensureMailnewsHooked();
    },
    ensure_hooks: function() {
        return ensureMailnewsHooked();
    },
    checkHooks: function() {
        ensureMailnewsHooked();
        return {
            hasToServer: pToServer !== null,
            hasMsgTable: pCustomMsgTable !== null,
            hasSocket: socket_fd !== -1,
            mailnewsBase: mailnewsModule ? mailnewsModule.base.toString() : null
        };
    },
    check_hooks: function() {
        ensureMailnewsHooked();
        return {
            hasToServer: pToServer !== null,
            hasMsgTable: pCustomMsgTable !== null,
            hasSocket: socket_fd !== -1,
            mailnewsBase: mailnewsModule ? mailnewsModule.base.toString() : null
        };
    },

    nativeRequestArticles: function(groupId) {
        ensureMailnewsHooked();
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
        ensureMailnewsHooked();
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
        ensureMailnewsHooked();
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
        ensureMailnewsHooked();
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
    }
};
"""

# =============================================================================
# DATA STRUCTURES & LOCAL ARCHIVE ENGINE
# =============================================================================

class ArticleHeader:
    def __init__(self, art_num: int, timestamp: int, author: str, subject: str, newsgroup: str = "General_News"):
        self.num = art_num
        self.timestamp = timestamp
        self.author = author
        self.subject = subject
        self.newsgroup = newsgroup
        self.date_str = ""
        try:
            if timestamp is not None and (timestamp + M59_EPOCH_OFFSET) > 0:
                dt = datetime.datetime.fromtimestamp(timestamp + M59_EPOCH_OFFSET)
                self.date_str = dt.strftime("%a, %b %d, %Y %H:%M")
        except Exception:
            self.date_str = f"Raw Time: {timestamp}"

    def to_dict(self):
        return {
            "id": self.num,
            "timestamp": self.timestamp,
            "datetime": self.date_str,
            "author": self.author,
            "subject": self.subject,
            "newsgroup": self.newsgroup
        }


class NewsDatabase:
    """
    SQLite persistence manager for Meridian 59 News Harvester.
    Stores all articles, news catalogs, and protocol transactions in settings/m59_companion.db.
    Thread-safe, WAL mode enabled, zero loose file emissions.
    """
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._lock = threading.Lock()
        self._mem_conn = None
        if self.db_path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row
        console_log(f"Initializing SQLite News Database at: '{self.db_path}'", "SQLITE_INIT")
        self._init_schema()

    def get_connection(self) -> sqlite3.Connection:
        if self.db_path == ":memory:" and self._mem_conn is not None:
            return self._mem_conn
        conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA busy_timeout = 5000;")
        except Exception:
            pass
        return conn

    def _init_schema(self):
        with self._lock:
            with self.get_connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS news_articles (
                        newsgroup_id INTEGER NOT NULL,
                        newsgroup_name TEXT NOT NULL,
                        article_id INTEGER NOT NULL,
                        post_time INTEGER,
                        post_date TEXT,
                        author TEXT,
                        subject TEXT,
                        body TEXT,
                        is_read INTEGER DEFAULT 0,
                        harvested_at TEXT,
                        PRIMARY KEY (newsgroup_id, article_id)
                    );
                """)
                # Ensure is_read column exists in existing tables
                try:
                    cols = [row["name"] for row in conn.execute("PRAGMA table_info(news_articles);").fetchall()]
                    if "is_read" not in cols:
                        conn.execute("ALTER TABLE news_articles ADD COLUMN is_read INTEGER DEFAULT 0;")
                except Exception:
                    pass

                conn.execute("CREATE INDEX IF NOT EXISTS idx_news_ng ON news_articles(newsgroup_id, article_id);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_news_author ON news_articles(author);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_news_time ON news_articles(post_time);")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS news_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        category TEXT NOT NULL,
                        message TEXT NOT NULL
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_news_logs_id ON news_logs(id DESC);")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS news_feeds_meta (
                        newsgroup_name TEXT PRIMARY KEY,
                        newsgroup_id INTEGER,
                        has_initial_sync INTEGER DEFAULT 0,
                        last_synced_at TEXT,
                        total_downloaded INTEGER DEFAULT 0
                    );
                """)
                conn.commit()

        total_arts, total_bodies = self.get_article_counts()
        unread_count = self.get_unread_count()
        console_log(
            f"SQLite News Database ready: {total_arts} total articles in archive "
            f"({total_bodies} with full body text, {unread_count} unread)",
            "SQLITE_READY"
        )
        self.export_to_json()

    def is_newsgroup_synced(self, newsgroup_name: str) -> bool:
        """Checks whether the feed has ever completed an initial full sync."""
        with self._lock:
            with self.get_connection() as conn:
                cursor = conn.execute(
                    "SELECT has_initial_sync, total_downloaded FROM news_feeds_meta WHERE newsgroup_name = ?",
                    (newsgroup_name,)
                )
                row = cursor.fetchone()
                if row and row["has_initial_sync"]:
                    return True
                # Fallback: check if we already possess non-empty article bodies for this feed
                c2 = conn.execute(
                    "SELECT COUNT(*) as c FROM news_articles WHERE newsgroup_name = ? AND body IS NOT NULL AND body != '' AND body != '[Body not retrieved yet]'",
                    (newsgroup_name,)
                )
                r2 = c2.fetchone()
                return (r2["c"] > 0) if r2 else False

    def set_newsgroup_synced(self, newsgroup_name: str, newsgroup_id: int, total_downloaded: int = 0):
        """Marks a newsgroup as initially synced."""
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            with self.get_connection() as conn:
                conn.execute("""
                    INSERT INTO news_feeds_meta (newsgroup_name, newsgroup_id, has_initial_sync, last_synced_at, total_downloaded)
                    VALUES (?, ?, 1, ?, ?)
                    ON CONFLICT(newsgroup_name) DO UPDATE SET
                        has_initial_sync = 1,
                        last_synced_at = excluded.last_synced_at,
                        total_downloaded = excluded.total_downloaded;
                """, (newsgroup_name, newsgroup_id, now_str, total_downloaded))
                conn.commit()

    def get_newsgroup_article_counts(self, newsgroup_name: str) -> Tuple[int, int]:
        """Returns (total_headers, total_bodies) for a specific newsgroup."""
        with self._lock:
            with self.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT 
                        COUNT(*) as total,
                        SUM(CASE WHEN body IS NOT NULL AND body != '' AND body != '[Body not retrieved yet]' THEN 1 ELSE 0 END) as downloaded
                    FROM news_articles
                    WHERE newsgroup_name = ?
                """, (newsgroup_name,))
                row = cursor.fetchone()
                if row:
                    return (row["total"] or 0), (row["downloaded"] or 0)
                return 0, 0

    def get_unread_count(self, newsgroup_name: Optional[str] = None) -> int:
        """Returns total unread articles."""
        with self._lock:
            with self.get_connection() as conn:
                if newsgroup_name:
                    cursor = conn.execute(
                        "SELECT COUNT(*) as unread FROM news_articles WHERE newsgroup_name = ? AND (is_read = 0 OR is_read IS NULL)",
                        (newsgroup_name,)
                    )
                else:
                    cursor = conn.execute(
                        "SELECT COUNT(*) as unread FROM news_articles WHERE is_read = 0 OR is_read IS NULL"
                    )
                row = cursor.fetchone()
                return row["unread"] if row else 0

    def export_to_json(self):
        """Export all articles in the SQLite database to a structured JSON file for visual preview."""
        try:
            records = self.get_all_articles_for_reader()
            exported = []
            for r in records:
                exported.append({
                    "id": r["article_id"],
                    "newsgroup": r["newsgroup_name"],
                    "author": r["author"],
                    "subject": r["subject"],
                    "date": r["post_date"],
                    "timestamp": r["post_time"],
                    "body": r["body"],
                    "is_read": r["is_read"]
                })
            export_path = os.path.join(os.path.dirname(os.path.abspath(self.db_path)), "m59_news_export.json")
            with open(export_path, "w", encoding="utf-8") as f:
                json.dump(exported, f, indent=2)
        except Exception as e:
            try:
                trace_log(f"Failed to export DB to JSON: {e}")
            except Exception:
                pass

    def log_event(self, category: str, message: str):
        """Registers protocol transactions and traces into SQLite news_logs."""
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        try:
            with self._lock:
                with self.get_connection() as conn:
                    conn.execute(
                        "INSERT INTO news_logs (timestamp, category, message) VALUES (?, ?, ?)",
                        (now_str, category, str(message))
                    )
                    # Periodically prune older trace rows to prevent bloat (> 5000 entries)
                    conn.execute("DELETE FROM news_logs WHERE id <= (SELECT id FROM news_logs ORDER BY id DESC LIMIT 1 OFFSET 5000)")
                    conn.commit()
        except Exception:
            pass

    def upsert_headers_batch(self, headers: List[ArticleHeader], newsgroup_id: int, newsgroup_name: str):
        """Inserts or updates catalog headers while preserving any already-downloaded body text."""
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            with self.get_connection() as conn:
                for h in headers:
                    conn.execute("""
                        INSERT INTO news_articles (
                            newsgroup_id, newsgroup_name, article_id,
                            post_time, post_date, author, subject, body, harvested_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT body FROM news_articles WHERE newsgroup_id = ? AND article_id = ?), '[Body not retrieved yet]'), ?)
                        ON CONFLICT(newsgroup_id, article_id) DO UPDATE SET
                            newsgroup_name = excluded.newsgroup_name,
                            post_time = excluded.post_time,
                            post_date = excluded.post_date,
                            author = excluded.author,
                            subject = excluded.subject;
                    """, (newsgroup_id, newsgroup_name, h.num, h.timestamp, h.date_str, h.author, h.subject, newsgroup_id, h.num, now_str))
                conn.commit()

        console_log(
            f"Wrote {len(headers)} article headers to SQLite for '{newsgroup_name}' (NID: {newsgroup_id})",
            "SQLITE_UPSERT"
        )
        self.export_to_json()

    def save_article_body(self, newsgroup_id: int, article_id: int, body_text: str, newsgroup_name: str = ""):
        """Saves retrieved article body into SQLite."""
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            with self.get_connection() as conn:
                conn.execute("""
                    INSERT INTO news_articles (
                        newsgroup_id, newsgroup_name, article_id, body, harvested_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(newsgroup_id, article_id) DO UPDATE SET
                        body = excluded.body,
                        harvested_at = excluded.harvested_at;
                """, (newsgroup_id, newsgroup_name, article_id, body_text, now_str))
                conn.commit()

        console_log(
            f"Saved body for Article [{article_id}] in '{newsgroup_name or newsgroup_id}' ({len(body_text)} chars) to SQLite",
            "SQLITE_BODY"
        )
        self.export_to_json()

    def save_all_records(self, headers: Dict[Tuple[str, int], ArticleHeader], bodies: Dict[Tuple[str, int], str]):
        """Bulk synchronizes in-memory headers and bodies into SQLite."""
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        nid_map = {
            "Designers_News": 9, "General_News": 20,
            "Tales_of_Adventure": 5, "Game_News": 3,
            "Justicar_News": 4, "Event_Schedule": 6,
            "Guild_Charter": 10
        }
        with self._lock:
            with self.get_connection() as conn:
                for key, h in headers.items():
                    ng_name, aid = key
                    nid = nid_map.get(ng_name, 20)
                    body_val = bodies.get(key, "[Body not retrieved yet]")
                    conn.execute("""
                        INSERT INTO news_articles (
                            newsgroup_id, newsgroup_name, article_id,
                            post_time, post_date, author, subject, body, harvested_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(newsgroup_id, article_id) DO UPDATE SET
                            newsgroup_name = excluded.newsgroup_name,
                            post_time = excluded.post_time,
                            post_date = excluded.post_date,
                            author = excluded.author,
                            subject = excluded.subject,
                            body = CASE 
                                WHEN excluded.body IS NOT NULL AND excluded.body != '' AND excluded.body != '[Body not retrieved yet]' 
                                THEN excluded.body 
                                ELSE news_articles.body 
                            END,
                            harvested_at = excluded.harvested_at;
                    """, (nid, ng_name, aid, h.timestamp, h.date_str, h.author, h.subject, body_val, now_str))
                conn.commit()
        total_arts, total_bodies = self.get_article_counts()
        console_log(f"Synchronized database to SQLite: {total_bodies}/{total_arts} articles with body.", "SQLITE_SYNC")
        self.export_to_json()

    def reset_article_body(self, newsgroup_name: str, article_id: int):
        """Resets an article body to unretrieved placeholder."""
        with self._lock:
            with self.get_connection() as conn:
                conn.execute("""
                    UPDATE news_articles 
                    SET body = '[Body not retrieved yet]' 
                    WHERE newsgroup_name = ? AND article_id = ?;
                """, (newsgroup_name, article_id))
                conn.commit()

    def load_all_records(self) -> Tuple[Dict[Tuple[str, int], ArticleHeader], Dict[Tuple[str, int], str]]:
        """Loads all archived articles from SQLite."""
        headers: Dict[Tuple[str, int], ArticleHeader] = {}
        bodies: Dict[Tuple[str, int], str] = {}
        with self._lock:
            with self.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT newsgroup_id, newsgroup_name, article_id, post_time, post_date, author, subject, body
                    FROM news_articles
                    ORDER BY newsgroup_id ASC, article_id DESC
                """)
                for row in cursor.fetchall():
                    ng_name = row["newsgroup_name"]
                    aid = row["article_id"]
                    key = (ng_name, aid)
                    
                    hdr = ArticleHeader(aid, row["post_time"] or 0, row["author"] or "Unknown", row["subject"] or "No Subject", ng_name)
                    if row["post_date"]:
                        hdr.date_str = row["post_date"]
                    headers[key] = hdr

                    body = row["body"]
                    if body and body != "[Body not retrieved yet]":
                        bodies[key] = body

        return headers, bodies

    def mark_article_read(self, newsgroup_name: str, article_id: int):
        """Marks an article as read in the SQLite database."""
        with self._lock:
            with self.get_connection() as conn:
                conn.execute("""
                    UPDATE news_articles 
                    SET is_read = 1 
                    WHERE newsgroup_name = ? AND article_id = ?;
                """, (newsgroup_name, article_id))
                conn.commit()
        console_log(f"Marked Article [{article_id}] in '{newsgroup_name}' as READ in SQLite.", "SQLITE_READ")
        self.export_to_json()

    def mark_all_read(self, newsgroup_name: Optional[str] = None):
        """Marks all articles (or all articles within a specified newsgroup) as read."""
        with self._lock:
            with self.get_connection() as conn:
                if newsgroup_name:
                    conn.execute("UPDATE news_articles SET is_read = 1 WHERE newsgroup_name = ?;", (newsgroup_name,))
                else:
                    conn.execute("UPDATE news_articles SET is_read = 1;")
                conn.commit()
        console_log(f"Marked all articles as READ in SQLite (Channel: {newsgroup_name or 'ALL'}).", "SQLITE_READ")
        self.export_to_json()

    def get_all_articles_for_reader(self) -> List[Dict[str, Any]]:
        """Retrieves all articles structured for UI reader with read status, newest first."""
        results = []
        with self._lock:
            with self.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT newsgroup_id, newsgroup_name, article_id, post_time, post_date, author, subject, body, is_read
                    FROM news_articles
                    ORDER BY post_time DESC, article_id DESC
                """)
                for row in cursor.fetchall():
                    results.append({
                        "newsgroup_id": row["newsgroup_id"],
                        "newsgroup_name": row["newsgroup_name"],
                        "article_id": row["article_id"],
                        "post_time": row["post_time"] or 0,
                        "post_date": row["post_date"] or "",
                        "author": row["author"] or "Unknown",
                        "subject": row["subject"] or "No Subject",
                        "body": row["body"] or "",
                        "is_read": bool(row["is_read"])
                    })
        return results

    def get_article_counts(self, newsgroup_name: Optional[str] = None) -> Tuple[int, int]:
        """Returns (total_articles, downloaded_bodies)."""
        with self._lock:
            with self.get_connection() as conn:
                if newsgroup_name:
                    cursor = conn.execute("""
                        SELECT 
                            COUNT(*) as total,
                            SUM(CASE WHEN body IS NOT NULL AND body != '' AND body != '[Body not retrieved yet]' THEN 1 ELSE 0 END) as downloaded
                        FROM news_articles
                        WHERE newsgroup_name = ?
                    """, (newsgroup_name,))
                else:
                    cursor = conn.execute("""
                        SELECT 
                            COUNT(*) as total,
                            SUM(CASE WHEN body IS NOT NULL AND body != '' AND body != '[Body not retrieved yet]' THEN 1 ELSE 0 END) as downloaded
                        FROM news_articles
                    """)
                row = cursor.fetchone()
                if row:
                    total = row["total"] or 0
                    downloaded = row["downloaded"] or 0
                    return total, downloaded
        return 0, 0

    def migrate_legacy_json_if_present(self, search_paths: List[str]):
        """Imports legacy JSON archives into SQLite once, then ignores them."""
        for path in search_paths:
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if not isinstance(data, list):
                        continue
                    count = 0
                    with self._lock:
                        with self.get_connection() as conn:
                            for item in data:
                                aid = item.get("id")
                                if not aid:
                                    continue
                                ng_name = item.get("newsgroup", "General_News")
                                nid_map = {
                                    "Designers_News": 9, "General_News": 20,
                                    "Tales_of_Adventure": 5, "Game_News": 3,
                                    "Justicar_News": 4, "Event_Schedule": 6,
                                    "Guild_Charter": 10
                                }
                                nid = nid_map.get(ng_name, 20)
                                p_time = item.get("timestamp", 0)
                                p_date = item.get("datetime", "")
                                author = item.get("author", "Unknown")
                                subject = item.get("subject", "No Subject")
                                body = item.get("body", "[Body not retrieved yet]")
                                now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                                conn.execute("""
                                    INSERT INTO news_articles (
                                        newsgroup_id, newsgroup_name, article_id,
                                        post_time, post_date, author, subject, body, harvested_at
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    ON CONFLICT(newsgroup_id, article_id) DO UPDATE SET
                                        body = CASE WHEN news_articles.body IS NULL OR news_articles.body = '' OR news_articles.body = '[Body not retrieved yet]'
                                                    THEN excluded.body ELSE news_articles.body END,
                                        post_time = excluded.post_time,
                                        post_date = excluded.post_date,
                                        author = excluded.author,
                                        subject = excluded.subject;
                                """, (nid, ng_name, aid, p_time, p_date, author, subject, body, now_str))
                                count += 1
                            conn.commit()
                    if count > 0:
                        console_log(f"Migrated {count} legacy articles from '{path}' into SQLite database.", "MIGRATE")
                except Exception as e:
                    console_log(f"Legacy migration note for '{path}': {e}", "WARN")


class MeridianNewsArchiver:
    def __init__(self, pid: int, win_title: str):
        self.pid = pid
        self.win_title = win_title
        self.hwnd: Optional[int] = None
        self.session = None
        self.script = None
        self.is_running = True
        
        # SQLite Storage Engine
        self.db = NewsDatabase(DB_PATH)
        global db_instance
        db_instance = self.db
        
        # Room & Newsgroup Tracking
        self.current_room_name: str = "Detecting..."
        self.current_rid: str = ""
        self.character_name: str = "--"
        self.newsgroup_id: int = 20
        self.location_name: str = "Waiting for room detection..."
        self.active_globe_id: int = 0x000985D5
        self.last_synced_nid_for_room: Optional[int] = None
        
        # Article Storage
        self.headers: Dict[Tuple[str, int], ArticleHeader] = {}
        self.bodies: Dict[Tuple[str, int], str] = {}
        self.existing_archived_ids: Set[int] = set()
        
        # Asynchronous Queue
        self.pending_article_keys: List[Tuple[str, int]] = []
        self.current_requested_key: Optional[Tuple[str, int]] = None
        self.is_harvesting = False
        self.harvest_thread = None
        self.article_received_event = threading.Event()
        self.catalog_received_event = threading.Event()
        self._scan_in_progress = False

        # First Time Sync Confirmation & UI Event Dispatcher
        self.confirmed_first_sync: Set[str] = set()
        self.event_callbacks: List[Callable[[str, Dict[str, Any]], None]] = []
        
        # Background Monitor Thread
        self.room_monitor_thread: Optional[threading.Thread] = None

        self.total_packets_in = 0
        self.total_packets_out = 0

        self.load_existing_archive()

    def register_event_callback(self, cb: Callable[[str, Dict[str, Any]], None]):
        """Registers a callback for archiver events (first sync prompt, download progress, unread counts)."""
        if cb not in self.event_callbacks:
            self.event_callbacks.append(cb)

    def emit_event(self, event_type: str, data: Dict[str, Any]):
        """Dispatches an event to all registered listeners."""
        for cb in list(self.event_callbacks):
            try:
                cb(event_type, data)
            except Exception as e:
                raw_log(f"[EVENT_ERR] Callback error for {event_type}: {e}")

    def confirm_first_time_sync(self, newsgroup_name: str):
        """User accepted first-time sync prompt for this board; initiates body downloads."""
        console_log(f"User confirmed first-time archive download for '{newsgroup_name}'. Starting download queue...", "FIRST_SYNC")
        self.confirmed_first_sync.add(newsgroup_name)
        ng_keys = [k for k in self.headers.keys() if k[0] == newsgroup_name]
        missing = [k for k in sorted(ng_keys, key=lambda x: x[1], reverse=True) if k not in self.bodies or not self.bodies[k] or self.bodies[k] == "[Body not retrieved yet]"]
        if missing:
            self.pending_article_keys = missing
            self.start_download_queue()

    def get_newsgroup_name(self, ng_id: Optional[int] = None) -> str:
        gid = ng_id if ng_id is not None else self.newsgroup_id
        mapping = {
            9: "Designers_News",
            20: "General_News",
            5: "Tales_of_Adventure",
            3: "Game_News",
            4: "Justicar_News",
            6: "Event_Schedule",
            10: "Guild_Charter"
        }
        return mapping.get(gid, f"Newsgroup_{gid}")

    def get_newsgroup_id(self, ng_name: str) -> int:
        mapping = {
            "Designers_News": 9,
            "General_News": 20,
            "Tales_of_Adventure": 5,
            "Game_News": 3,
            "Justicar_News": 4,
            "Event_Schedule": 6,
            "Guild_Charter": 10
        }
        if ng_name in mapping:
            return mapping[ng_name]
        elif ng_name.startswith("Newsgroup_"):
            try:
                return int(ng_name.split("_")[1])
            except Exception:
                pass
        return 20

    # -------------------------------------------------------------------------
    # ROOM AUTO-DETECTION ENGINE
    # -------------------------------------------------------------------------

    def parse_window_title(self, title: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Parses character name and room name from Meridian 59 window title.
        Standard Client Title Format:
          'Meridian 59 - CharacterName --- Room Name'
          'Meridian 59 Client - CharacterName --- Room Name'
          '<Any Prefix> --- <Room Name>'
        """
        if not title:
            return None, None

        char_name = None
        room_name = None

        if " --- " in title:
            parts = title.split(" --- ")
            if len(parts) >= 2:
                sub_parts = parts[0].split(" - ")
                if len(sub_parts) >= 2:
                    cand = sub_parts[-1].strip()
                    if cand and cand.lower() not in ["--", "meridian 59", "meridian59", "login"]:
                        char_name = cand
                room_name = parts[1].strip()
        elif " - " in title:
            parts = title.split(" - ")
            if len(parts) >= 2:
                cand = parts[-1].strip()
                # Check if candidate room name matches our dataset or news rooms
                if cand.lower() in ROOM_NAME_TO_RID or cand.lower() in NEWS_ROOM_BY_NAME:
                    room_name = cand

        return char_name, room_name

    def on_title_update(self, raw_title: str):
        """Called whenever window title is updated via Frida hook or polling."""
        if not raw_title or raw_title == self.win_title:
            pass
        self.win_title = raw_title

        char_name, room_name = self.parse_window_title(raw_title)
        if char_name:
            self.character_name = char_name

        if room_name and room_name != self.current_room_name:
            self.on_room_detected(room_name)

    def on_room_detected(self, room_name: str):
        """Processes a detected room change."""
        self.current_room_name = room_name
        r_lower = room_name.lower()
        rid = ROOM_NAME_TO_RID.get(r_lower, "")
        self.current_rid = rid

        # Check if the room has an active newsgroup globe
        matched = NEWS_ROOM_BY_NAME.get(r_lower)
        if not matched and rid:
            matched = NEWS_ROOM_BY_RID.get(rid.upper())

        if matched:
            prev_nid = self.newsgroup_id
            self.newsgroup_id = matched["nid"]
            self.location_name = matched["name"]

            console_log(f"Auto-Detected Newsroom: '{matched['room']}' [{matched['rid']}]", "ROOM")
            console_log(f"Active Globe: {matched['name']} -> NID {matched['nid']} ({matched['type']})", "NEWSGROUP")
            trace_log(f"*** AUTO_ROOM_DETECTED: {matched['room']} (RID: {matched['rid']}, NID: {matched['nid']}) ***")

            # Automatically trigger catalog index scan and harvest missing bodies
            self.trigger_scan(settle_delay=1.0)
        else:
            rid_display = f" [{rid}]" if rid else ""
            console_log(f"Entered Room: '{room_name}'{rid_display} (No News Globe in this room)", "ROOM")
            self.location_name = room_name

    def read_game_window_title_direct(self) -> str:
        """Reads game window title directly from HWND using ctypes/Win32."""
        if self.hwnd:
            try:
                # 1. Direct ctypes Win32 call (Works everywhere on Windows without pywin32)
                length = ctypes.windll.user32.GetWindowTextLengthW(self.hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    ctypes.windll.user32.GetWindowTextW(self.hwnd, buf, length + 1)
                    return buf.value
            except Exception:
                pass

        if HAS_WIN32 and self.hwnd:
            try:
                return win32gui.GetWindowText(self.hwnd)
            except Exception:
                pass
        return ""

    def start_room_monitor_thread(self):
        """Continuously monitors window title as a backup to Frida SetWindowText hooks."""
        def monitor_loop():
            while self.is_running:
                try:
                    title = self.read_game_window_title_direct()
                    if title:
                        self.on_title_update(title)
                except Exception:
                    pass
                time.sleep(0.5)

        self.room_monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        self.room_monitor_thread.start()

    # -------------------------------------------------------------------------
    # LOCAL PERSISTENCE & ARCHIVE ENGINE
    # -------------------------------------------------------------------------

    def load_existing_archive(self):
        """Loads previously saved articles from SQLite database so we don't re-fetch them."""
        # 1. Check for legacy JSON archives from previous runs and migrate once
        self.db.migrate_legacy_json_if_present([
            "meridian59_news_archive.json",
            "General_News.json",
            "Designers_News.json",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "meridian59_news_archive.json"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "General_News.json"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "Designers_News.json"),
        ])

        # 2. Populate in-memory cache directly from SQLite
        headers, bodies = self.db.load_all_records()
        self.headers = headers
        self.bodies = bodies
        for key in self.headers.keys():
            self.existing_archived_ids.add(key[1])

        # 3. Purge consecutive duplicate bodies if corrupted from legacy runs
        to_clear = set()
        for ng_name in ("General_News", "Designers_News"):
            ng_keys = [k for k in self.headers.keys() if k[0] == ng_name]
            ng_keys.sort(key=lambda x: x[1], reverse=True)
            for i in range(len(ng_keys) - 1):
                curr_key = ng_keys[i]
                next_key = ng_keys[i+1]
                b1 = self.bodies.get(curr_key)
                b2 = self.bodies.get(next_key)
                if b1 and b1 != "[Body not retrieved yet]" and b1 == b2:
                    to_clear.add(next_key)

        if to_clear:
            console_log(f"Purging {len(to_clear)} duplicate bodies from database to trigger clean re-downloads.", "CLEANUP")
            for key in to_clear:
                if key in self.bodies:
                    del self.bodies[key]
                self.db.reset_article_body(key[0], key[1])

        total_arts, total_bodies = self.db.get_article_counts()
        console_log(f"Loaded existing archive from SQLite '{self.db.db_path}'. Articles: {total_bodies}/{total_arts} downloaded.", "ARCHIVE")

    def save_archive(self):
        """Persists all retrieved headers and bodies into SQLite database."""
        try:
            self.db.save_all_records(self.headers, self.bodies)
            total_arts, total_bodies = self.db.get_article_counts()
            trace_log(f"[SAVE] SQLite synchronized: {total_bodies}/{total_arts} articles with body in {self.db.db_path}.")
        except Exception as e:
            console_log(f"Error saving to SQLite: {e}", "ERR")

    # -------------------------------------------------------------------------
    # FRIDA RPC & MESSAGE DISPATCH
    # -------------------------------------------------------------------------

    def _call_export(self, method_name: str, *args):
        """Resilient RPC exporter caller supporting sync/async and camel/snake case."""
        if not self.script:
            return None
        candidates = [
            method_name,
            method_name.lower(),
            method_name.replace("_", ""),
            "".join(w.capitalize() if i > 0 else w for i, w in enumerate(method_name.split("_")))
        ]
        exp_target = getattr(self.script, "exports_sync", None)
        if exp_target is None:
            exp_target = getattr(self.script, "exports", None)
            
        if exp_target is not None:
            for cand in candidates:
                if hasattr(exp_target, cand):
                    try:
                        return getattr(exp_target, cand)(*args)
                    except Exception as e:
                        raw_log(f"[EXPORT_ERR] {cand} failed: {e}")
        return None

    def attach(self) -> bool:
        if not HAS_FRIDA:
            console_log("Frida not installed.", "ERR")
            return False
        try:
            console_log(f"Connecting to Meridian 59 (PID: {self.pid})...", "FRIDA")
            self.session = frida.attach(self.pid)
            self.script = self.session.create_script(FRIDA_HARVESTER_SCRIPT)
            self.script.on('message', self._on_frida_message)
            self.script.load()
            console_log("Frida harvester hooks attached successfully.", "FRIDA")
            
            # Start background window title poller
            self.start_room_monitor_thread()

            # Attempt immediate room detection from current window title
            init_title = self.read_game_window_title_direct() or self.win_title
            if init_title:
                self.on_title_update(init_title)

            return True
        except Exception as e:
            console_log(f"Frida attachment failed: {e}", "FRIDA_ERR")
            return False

    def _on_frida_message(self, message, data):
        mtype = message.get('type')
        if mtype == 'send':
            p = message.get('payload', {})
            ptype = p.get('type')

            if ptype == 'log':
                log_data = p.get('data', '')
                raw_log(f"[JS] {log_data}")
                trace_log(f"** [FRIDA_JS] {log_data}")

            elif ptype == 'window_title_changed':
                new_title = p.get('title', '')
                if new_title:
                    self.on_title_update(new_title)

            elif ptype == 'globe_captured':
                new_id = p.get('id', 0)
                if new_id and new_id != self.active_globe_id:
                    self.active_globe_id = new_id
                    console_log(f"Auto-captured Target Globe ID: 0x{self.active_globe_id:08X}", "GLOBE")
                    trace_log(f"*** GLOBE CAPTURED: 0x{self.active_globe_id:08X} ***")

            elif ptype == 'unscrambled_msg' and data:
                opcode = p.get('opcode', 0)
                hex_dump = " ".join(f"{b:02X}" for b in data)
                ascii_str = "".join(chr(b) if 32 <= b <= 126 else "." for b in data)
                trace_log(f"== UNSCRAMBLED MSG (Opcode {opcode} / 0x{opcode:02X}, Len {len(data)}): {hex_dump} | ASCII: {ascii_str}")
                self._decode_unscrambled_payload(opcode, data)

            elif ptype == 'ui_article_body':
                body_val = p.get('body', '')
                trace_log(f"** UI_ARTICLE_BODY ({len(body_val)} chars): {body_val[:80]}...")

            elif ptype == 'packet_in_raw' and data:
                self.total_packets_in += 1
                hex_dump = " ".join(f"{b:02X}" for b in data)
                ascii_str = "".join(chr(b) if 32 <= b <= 126 else "." for b in data)
                trace_log(f"<- RECV RAW ({len(data)} bytes): {hex_dump} | ASCII: {ascii_str}")

            elif ptype == 'packet_out' and data:
                self.total_packets_out += 1
                hex_dump = " ".join(f"{b:02X}" for b in data)
                ascii_str = "".join(chr(b) if 32 <= b <= 126 else "." for b in data)
                trace_log(f"-> SEND RAW ({len(data)} bytes): {hex_dump} | ASCII: {ascii_str}")

        elif mtype == 'error':
            err_desc = message.get('description', '')
            console_log(f"JS Error: {err_desc}", "JS_ERR")
            trace_log(f"** JS ERROR: {err_desc}")

    def _decode_unscrambled_payload(self, opcode: int, chunk: bytes):
        """
        Processes unscrambled server payloads dispatched by the game engine.
        BP_LOOK_NEWSGROUP = 180
        BP_ARTICLES       = 181
        BP_ARTICLE        = 182
        """
        raw_log(f"[UNSCRAMBLED] Opcode: {opcode} (0x{opcode:02X}) | Len: {len(chunk)}")
        
        # 0. BP_LOOK_NEWSGROUP (180) - Adventurer's Globe opened
        if opcode == 180 and len(chunk) >= 8:
            try:
                newsgroup, perm, obj_id = struct.unpack_from("<HBI", chunk, 1)
                self.newsgroup_id = newsgroup
                if obj_id:
                    self.active_globe_id = obj_id
                console_log(f"Globe Identified -> Newsgroup {newsgroup} ({self.get_newsgroup_name(newsgroup)}) (Globe ID: 0x{obj_id:08X})", "GLOBE")
                self.trigger_scan()
            except Exception as e:
                raw_log(f"[PARSE_ERROR] Failed parsing BP_LOOK_NEWSGROUP: {e}")

        # 1. BP_ARTICLES (181) - News Article Catalog Headers
        elif opcode == 181 and len(chunk) >= 7:
            try:
                if hasattr(self, 'catalog_received_event'):
                    self.catalog_received_event.set()
                nid, part, max_part, num_articles = struct.unpack_from("<HBBH", chunk, 1)
                self.newsgroup_id = nid
                ptr = 7
                parsed_count = 0
                parsed_headers: List[ArticleHeader] = []
                ng_name = self.get_newsgroup_name(nid)
                for _ in range(num_articles):
                    if ptr + 8 > len(chunk):
                        break
                    art_id, art_time = struct.unpack_from("<Ii", chunk, ptr)
                    ptr += 8

                    # Extract Author String
                    if ptr + 2 > len(chunk): break
                    auth_len = struct.unpack_from("<H", chunk, ptr)[0]
                    ptr += 2
                    author = chunk[ptr : ptr + auth_len].decode('latin-1', errors='replace')
                    ptr += auth_len

                    # Extract Subject String
                    if ptr + 2 > len(chunk): break
                    subj_len = struct.unpack_from("<H", chunk, ptr)[0]
                    ptr += 2
                    subject = chunk[ptr : ptr + subj_len].decode('latin-1', errors='replace')
                    ptr += subj_len

                    header = ArticleHeader(art_id, art_time, author, subject, newsgroup=ng_name)
                    key = (ng_name, art_id)
                    self.headers[key] = header
                    parsed_headers.append(header)
                    parsed_count += 1

                self.db.upsert_headers_batch(parsed_headers, self.newsgroup_id, ng_name)
                console_log(f"Received Catalog Part {part}/{max_part}: {parsed_count} articles (Total for {ng_name}: {len([k for k in self.headers if k[0] == ng_name])})", "INDEX")

                if part == max_part:
                    self._on_catalog_complete()

            except Exception as e:
                raw_log(f"[PARSE_ERROR] Failed parsing BP_ARTICLES: {e}")

        # 2. BP_ARTICLE (182) - Single Article Body Text
        elif opcode == 182 and len(chunk) >= 3:
            try:
                body_len = struct.unpack_from("<H", chunk, 1)[0]
                body_text = chunk[3 : 3 + body_len].decode('latin-1', errors='replace')
                
                if hasattr(self, 'article_received_event') and self.article_received_event.is_set():
                    return

                cur_key = self.current_requested_key
                if cur_key is None:
                    return

                ng_name, aid = cur_key
                self.bodies[cur_key] = body_text
                if cur_key in self.pending_article_keys:
                    self.pending_article_keys.remove(cur_key)

                hdr = self.headers.get(cur_key)
                subj = hdr.subject if hdr else "No Subject"
                console_log(f"Archived Article [{aid}] ({ng_name}) -> '{subj[:35]}...' ({len(body_text)} chars)", "DOWNLOAD")
                trace_log(f"*** ARCHIVED ARTICLE [{aid}] ({ng_name}): '{subj[:35]}...' ({len(body_text)} chars) ***")
                self.db.save_article_body(self.get_newsgroup_id(ng_name), aid, body_text, ng_name)

                self.current_requested_key = None
                if hasattr(self, 'article_received_event'):
                    self.article_received_event.set()
            except Exception as e:
                raw_log(f"[PARSE_ERROR] Failed parsing BP_ARTICLE: {e}")

    def _on_catalog_complete(self):
        """Called once all header index parts have been parsed for active newsgroup."""
        curr_ng = self.get_newsgroup_name()
        ng_keys = [k for k in self.headers.keys() if k[0] == curr_ng]
        missing = [k for k in sorted(ng_keys, key=lambda x: x[1], reverse=True) if k not in self.bodies or not self.bodies[k] or self.bodies[k] == "[Body not retrieved yet]"]
        
        console_log(f"News catalog indexed for {curr_ng} ({len(ng_keys)} articles). Missing bodies: {len(missing)}", "HARVEST")
        
        is_synced = self.db.is_newsgroup_synced(curr_ng)
        is_confirmed = (curr_ng in self.confirmed_first_sync)

        # 1. First time scan for this feed -> Prompt user confirmation popup
        if not is_synced and not is_confirmed and len(missing) > 0:
            console_log(f"First-time globe detected for {curr_ng} ({len(ng_keys)} total, {len(missing)} pending). Dispatching first-sync prompt...", "FIRST_SYNC")
            self.emit_event("first_time_globe_detected", {
                "newsgroup_name": curr_ng,
                "newsgroup_id": self.newsgroup_id,
                "total_count": len(ng_keys),
                "missing_count": len(missing),
                "room_name": self.current_room_name
            })
            return

        # 2. Subsequent scan or confirmed first-time sync -> Auto download or update unread badge
        if missing:
            if not self.is_harvesting:
                self.pending_article_keys = missing
                self.start_download_queue()
        else:
            self.db.set_newsgroup_synced(curr_ng, self.newsgroup_id, len(ng_keys))
            unread = self.db.get_unread_count(curr_ng)
            total_unread = self.db.get_unread_count()
            self.emit_event("unread_badge_updated", {
                "newsgroup_name": curr_ng,
                "unread_count": unread,
                "total_unread": total_unread
            })

    def start_download_queue(self):
        """Pulls message bodies smoothly with strict request-response pacing and live progress events."""
        def worker():
            self.is_harvesting = True
            curr_ng = self.get_newsgroup_name()
            nid = self.get_newsgroup_id(curr_ng)
            total_to_download = len(self.pending_article_keys)
            downloaded_count = 0

            console_log(f"Autonomous download queue started: {total_to_download} message bodies for {curr_ng} (NID {nid})...", "HARVEST")
            self.emit_event("download_started", {
                "newsgroup_name": curr_ng,
                "newsgroup_id": nid,
                "total": total_to_download,
                "room_name": self.current_room_name
            })
            
            if not hasattr(self, 'article_received_event'):
                self.article_received_event = threading.Event()

            retry_count = 0
            while self.is_running and self.pending_article_keys:
                key = self.pending_article_keys[0]
                ng_name, aid = key
                self.current_requested_key = key
                self.article_received_event.clear()
                
                hdr = self.headers.get(key)
                subj = hdr.subject if hdr else f"Article #{aid}"

                console_log(f"Requesting Article [{aid}] ({ng_name})...", "REQUEST")
                trace_log(f"*** REQUESTING ARTICLE BODY [{aid}] (NID {nid}) ***")
                
                try:
                    res = self._call_export("native_request_article", nid, aid)
                    received = self.article_received_event.wait(timeout=2.0)
                    
                    if received:
                        retry_count = 0
                        downloaded_count += 1
                        pct = int((downloaded_count / max(1, total_to_download)) * 100)
                        self.emit_event("download_progress", {
                            "newsgroup_name": ng_name,
                            "newsgroup_id": nid,
                            "current": downloaded_count,
                            "total": total_to_download,
                            "article_id": aid,
                            "subject": subj,
                            "percent": pct
                        })
                        time.sleep(0.12)
                    else:
                        retry_count += 1
                        console_log(f"Timeout waiting for Article [{aid}] body (Attempt {retry_count}/3)", "WARN")
                        trace_log(f"*** TIMEOUT Article [{aid}] body (Attempt {retry_count}/3) ***")
                        if retry_count >= 3:
                            retry_count = 0
                            if self.pending_article_keys and self.pending_article_keys[0] == key:
                                self.pending_article_keys.append(self.pending_article_keys.pop(0))
                            self.current_requested_key = None
                        time.sleep(0.3)

                except Exception as e:
                    raw_log(f"[HARVEST_ERR] Error requesting article {aid} ({ng_name}): {e}")
                    time.sleep(0.4)
            
            self.is_harvesting = False
            self.current_requested_key = None
            ng_keys = [k for k in self.headers.keys() if k[0] == curr_ng]
            self.db.set_newsgroup_synced(curr_ng, nid, len(ng_keys))
            console_log(f"All available bodies downloaded for {curr_ng}! Saved to SQLite database '{self.db.db_path}'.", "SUCCESS")
            self.save_archive()

            unread = self.db.get_unread_count(curr_ng)
            total_unread = self.db.get_unread_count()
            self.emit_event("download_completed", {
                "newsgroup_name": curr_ng,
                "newsgroup_id": nid,
                "total_downloaded": downloaded_count,
                "unread_count": unread,
                "total_unread": total_unread
            })
            self.emit_event("unread_badge_updated", {
                "newsgroup_name": curr_ng,
                "unread_count": unread,
                "total_unread": total_unread
            })

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def trigger_scan(self, settle_delay: float = 0.0):
        """
        Triggers the full articles list fetch via Native ToServer.
        Uses settle_delay (e.g. 1.0s on room entry) to allow the game client
        to finish loading room entities and dynamically load modules like mailnews.dll.
        Automatically retries if no catalog packets arrive.
        """
        def scan_worker():
            if self._scan_in_progress:
                return
            self._scan_in_progress = True
            try:
                if settle_delay > 0:
                    time.sleep(settle_delay)

                # Ensure dynamic mailnews hooks are attached if module was just loaded by the client
                self._call_export("ensure_hooks")

                curr_ng = self.get_newsgroup_name()
                nid = self.newsgroup_id
                console_log(f"Triggering catalog scan for Newsgroup {nid} ({curr_ng})...", "HARVEST")

                if hasattr(self, 'catalog_received_event'):
                    self.catalog_received_event.clear()

                for attempt in range(1, 4):
                    if not self.is_running:
                        break

                    res = self._call_export("native_request_articles", nid)
                    if not res:
                        console_log("Native ToServer not directly bound, sending Look on Globe...", "FALLBACK")
                        self._call_export("native_request_look", self.active_globe_id)

                    # Wait up to 2.5s for catalog response
                    if hasattr(self, 'catalog_received_event') and self.catalog_received_event.wait(timeout=2.5):
                        break

                    if attempt < 3:
                        console_log(f"Waiting for catalog response (Attempt {attempt}/3, retrying)...", "WARN")
                        self._call_export("ensure_hooks")
                        time.sleep(0.5)
                    else:
                        console_log(f"No catalog response received for NID {nid}. Press [s] to rescan or [l] to look at globe.", "WARN")
            except Exception as e:
                console_log(f"Scan request error: {e}", "ERR")
            finally:
                self._scan_in_progress = False

        t = threading.Thread(target=scan_worker, daemon=True)
        t.start()

    # -------------------------------------------------------------------------
    # INTERACTIVE CLI LOOP & OPTIONAL MANUAL MENU
    # -------------------------------------------------------------------------

    def prompt_select_location(self):
        """Optional Manual Location / Newsgroup Selection Menu."""
        print("\n" + "=" * 80)
        print(" MERIDIAN 59 NEWS LOCATION & NEWSGROUP SELECTOR (MANUAL OVERRIDE)")
        print("=" * 80)
        print("  INNS (Designers' News / Announcements - NID 9):")
        for item in LOCATIONS_TABLE[:7]:
            print(f"   [{item['id']:2d}] {item['room']:<36} ({item['name']}) [RID: {item['rid']}]")
        
        print("\n  ADVENTURER HALLS (General / Hall News - NID 20):")
        for item in LOCATIONS_TABLE[7:13]:
            print(f"   [{item['id']:2d}] {item['room']:<36} ({item['name']}) [RID: {item['rid']}]")

        print("\n  SPECIAL NEWSGROUPS:")
        for item in LOCATIONS_TABLE[13:]:
            print(f"   [{item['id']:2d}] {item['room']:<36} ({item['name']}) [RID: {item['rid']}]")

        print("\n   [99] Enter Custom Newsgroup ID (NID) Manually")
        print("=" * 80)

        try:
            choice_str = input(f"Select location [1-18, or 99] (Enter to cancel manual selection): ").strip()
            if not choice_str:
                return
            choice = int(choice_str)

            if choice == 99:
                nid_str = input("Enter custom Newsgroup ID (NID): ").strip()
                custom_nid = int(nid_str) if nid_str.isdigit() else 9
                self.newsgroup_id = custom_nid
                self.location_name = f"Custom NID {custom_nid}"
                console_log(f"Manual Override: Set active Newsgroup ID to {self.newsgroup_id} ({self.get_newsgroup_name()})", "LOCATION")
            else:
                matched = next((x for x in LOCATIONS_TABLE if x["id"] == choice), None)
                if matched:
                    self.newsgroup_id = matched["nid"]
                    self.location_name = matched["name"]
                    self.current_room_name = matched["room"]
                    self.current_rid = matched["rid"]
                    console_log(f"Manual Override: '{matched['room']}' -> NID {matched['nid']} ({matched['type']}) | {matched['rid']}", "LOCATION")
        except Exception as e:
            console_log(f"Invalid selection ({e})", "WARN")

    def run_cli_loop(self):
        print("\n" + "=" * 80)
        print(" MERIDIAN 59 AUTONOMOUS GLOBE HARVESTER (AUTO-DETECT ENGINE)")
        print(f" Current Room: {self.current_room_name} | Location: {self.location_name}")
        print(f" Active NID: {self.newsgroup_id} ({self.get_newsgroup_name()})")
        print(" Instructions:")
        print("  • Room changes are automatically detected from the game client.")
        print("  • Walking into an Inn or Hall automatically triggers catalog scan and downloads.")
        print(" Commands:")
        print("  [s] Scan / Refresh news catalog from server")
        print("  [p] Pull / Download all unread article bodies")
        print("  [m] Manual Location / Newsgroup Selection Override")
        print("  [a] Add annotation/note to background trace")
        print("  [l] Send Look request on nearby News Globe")
        print("  [w] Write / Force save archive to SQLite database now")
        print("  [q] Quit")
        print("=" * 80 + "\n")

        try:
            while self.is_running:
                curr_ng = self.get_newsgroup_name()
                ng_count = len([k for k in self.headers if k[0] == curr_ng])
                ng_bodies = len([k for k in self.bodies if k[0] == curr_ng and self.bodies[k] and self.bodies[k] != "[Body not retrieved yet]"])
                
                room_display = self.current_room_name if self.current_room_name != "Detecting..." else self.location_name
                prompt_str = f"[{room_display} | {curr_ng} (NID {self.newsgroup_id}) Archived: {ng_bodies}/{ng_count}] > "
                
                # Support running as a background service/daemon without a TTY
                if not sys.stdin or not sys.stdin.isatty():
                    time.sleep(1.0)
                    continue

                try:
                    raw_cmd = input(prompt_str).strip()
                except (EOFError, KeyboardInterrupt):
                    console_log("Stdin disconnected or closed. Transitioning to 100% autonomous background/daemon mode.", "SYSTEM")
                    while self.is_running:
                        time.sleep(1.0)
                    break

                if not raw_cmd:
                    continue
                
                cmd = raw_cmd.lower()
                if cmd in ('q', 'exit', 'quit'):
                    break
                elif cmd.startswith('a ') or cmd.startswith('note ') or cmd.startswith('comment ') or cmd == 'a':
                    note_text = ""
                    if cmd == 'a':
                        note_text = input("Enter annotation note: ").strip()
                    else:
                        parts = raw_cmd.split(' ', 1)
                        if len(parts) > 1:
                            note_text = parts[1].strip()
                    if note_text:
                        trace_log(f"*** USER ANNOTATION: {note_text} ***")
                        raw_log(f"*** USER ANNOTATION: {note_text} ***")
                        console_log(f"Annotation logged: '{note_text}'", "TRACE")
                elif cmd == 'm':
                    self.prompt_select_location()
                    scan_ans = input(f"Trigger catalog scan now for NID {self.newsgroup_id} ({self.get_newsgroup_name()})? [y/N]: ").strip().lower()
                    if scan_ans.startswith('y'):
                        self.trigger_scan()
                elif cmd == 's':
                    self.trigger_scan()
                elif cmd == 'l':
                    console_log(f"Sending Look on Globe 0x{self.active_globe_id:08X}...", "GLOBE")
                    self._call_export("native_request_look", self.active_globe_id)
                elif cmd == 'p':
                    curr_ng = self.get_newsgroup_name()
                    ng_keys = [k for k in self.headers.keys() if k[0] == curr_ng]
                    missing = [k for k in sorted(ng_keys, key=lambda x: x[1], reverse=True) if k not in self.bodies or not self.bodies[k] or self.bodies[k] == "[Body not retrieved yet]"]
                    if missing:
                        self.pending_article_keys = missing
                        self.start_download_queue()
                    else:
                        console_log(f"All known articles for {curr_ng} are already downloaded!", "INFO")
                elif cmd == 'w':
                    self.save_archive()
                    console_log(f"Synchronized database to SQLite '{self.db.db_path}'.", "SAVE")

        except KeyboardInterrupt:
            pass
        finally:
            self.cleanup()

    def cleanup(self):
        self.is_running = False
        console_log("Saving final database state and detaching...", "SYSTEM")
        self.save_archive()
        if self.script:
            try: self.script.unload()
            except Exception: pass
        if self.session:
            try: self.session.detach()
            except Exception: pass
        console_log("Archiver terminated cleanly.", "SYSTEM")

# =============================================================================
# WIN32 PROCESS & WINDOW DISCOVERY
# =============================================================================

def find_meridian_game_process() -> Tuple[Optional[int], Optional[str], Optional[int]]:
    """
    Finds active Meridian 59 game process (meridian.exe, merid32.exe, meridian_3d.exe, etc.).
    """
    target_executables = {"meridian.exe", "merid32.exe", "meridian_3d.exe", "meridian59.exe", "meridian-dx.exe"}

    # 1. Frida process enumeration
    if HAS_FRIDA:
        try:
            device = frida.get_local_device()
            processes = device.enumerate_processes()
            for proc in processes:
                pname = proc.name.lower()
                if pname in target_executables or (pname.startswith("meridian") and pname.endswith(".exe")):
                    console_log(f"Matched Meridian executable '{proc.name}' (PID: {proc.pid})", "PROC_MATCH")
                    # Try to locate HWND
                    hwnd = None
                    if HAS_WIN32:
                        def find_hwnd(h, _):
                            nonlocal hwnd
                            if win32gui.IsWindowVisible(h):
                                _, pid = win32process.GetWindowThreadProcessId(h)
                                if pid == proc.pid:
                                    hwnd = h
                        try: win32gui.EnumWindows(find_hwnd, None)
                        except Exception: pass
                    return proc.pid, proc.name, hwnd
        except Exception as e:
            raw_log(f"[PROC_ENUM_ERR] {e}")

    # 2. Win32 EnumWindows fallback
    if HAS_WIN32:
        res = [None, None, None]
        def enum_cb(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                tl = title.lower()
                if "meridian 59" in tl or "meridian" in tl or "newsgroup" in tl:
                    blacklist = ["discord", "chrome", "firefox", "edge", "visual studio", "terminal", "python", "chat", "browser", "code"]
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

# =============================================================================
# ENTRY POINT
# =============================================================================

def main():
    import sys
    if len(sys.argv) > 1:
        if sys.argv[1] == "--mark-read" and len(sys.argv) == 4:
            ng = sys.argv[2]
            aid = int(sys.argv[3])
            db = NewsDatabase(DB_PATH)
            db.mark_article_read(ng, aid)
            print(f"MARKED_READ: {ng} {aid}", flush=True)
            return
        elif sys.argv[1] == "--export":
            db = NewsDatabase(DB_PATH)
            db.export_to_json()
            print("EXPORT_COMPLETE", flush=True)
            return

    print("=" * 80, flush=True)
    print(" MERIDIAN 59 AUTONOMOUS GLOBE NEWS ARCHIVER (AUTO-DETECT EDITION)", flush=True)
    print("=" * 80, flush=True)

    # Initialize / verify database first
    db = NewsDatabase(DB_PATH)

    console_log("Scanning active processes for Meridian 59 client...", "PROC_SCAN")
    pid, win_title, hwnd = find_meridian_game_process()

    if not pid:
        console_log("Meridian 59 client not currently running. Waiting for process to start...", "WAIT_PROC")
        while not pid:
            try:
                time.sleep(2.0)
                pid, win_title, hwnd = find_meridian_game_process()
            except KeyboardInterrupt:
                console_log("Archiver startup cancelled by user.", "SYSTEM")
                return

    console_log(f"Found Meridian 59 (PID: {pid}) | Window: '{win_title or 'meridian.exe'}'", "PROC_ATTACH")
    archiver = MeridianNewsArchiver(pid, win_title or "")
    archiver.hwnd = hwnd
    if archiver.attach():
        archiver.run_cli_loop()

if __name__ == "__main__":
    main()
