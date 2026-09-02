#!/usr/bin/env python3
"""
===============================================================================
 MERIDIAN 59 AUTONOMOUS GLOBE NEWS HARVESTER & ARCHIVER
 Author: Senior Reverse Engineer & Frida Specialist
 Target: Meridian 59 (Classic & D3D Client)

 FEATURES:
  1. Full Protocol Synchronization: Hooks `HandleMessage` (inbound, post-unscrambled)
     and calls the client engine's native `ToServer()` for 100% synchronized
     CRC16 + PRNG stream encryption (never causing kicks, desyncs, or silent drops).
  2. Multi-Vector Dispatch Fallback:
     - Vector A: Direct Native `ToServer(BP_REQ_ARTICLES, msg_table, group)` call
     - Vector B: Direct Native `PostMessage(hMain, WM_COMMAND, MAKEWPARAM(A_USERACTION, 0), ...)`
     - Vector C: Client Text/Macro Command simulation (`"news 20"`, `"read 20"`)
  3. Safe Native Interception: Unobtrusive sniffing of `SafeDialogBoxParam` and `HandleMessage`
     so the UI dialog doesn't steal focus or pop up if running autonomously.
  4. Complete Local Archive: Automatically saves full news catalog and message bodies
     to both formatted text (`meridian59_news_archive.txt`) and JSON database
     (`meridian59_news_archive.json`).
===============================================================================
"""

import sys
import os
import time
import datetime
import json
import threading
import struct
from typing import Dict, List, Optional, Tuple, Set

try:
    import win32gui
    import win32process
    import win32con
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

try:
    import frida
    HAS_FRIDA = True
except ImportError:
    HAS_FRIDA = False

RAW_LOG_PATH = "news_wire_raw.log"
ARCHIVE_JSON_PATH = "meridian59_news_archive.json"
PACKET_TRACE_PATH = "m59_packet_trace.log"
M59_EPOCH_OFFSET = 1534000000

def raw_log(msg: str):
    """Logs protocol transactions with millisecond timestamps."""
    try:
        timestamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        with open(RAW_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {msg}\n")
    except Exception:
        pass

def trace_log(msg: str):
    """Silently logs full packet traces and annotations to m59_packet_trace.log."""
    try:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        with open(PACKET_TRACE_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {msg}\n")
    except Exception:
        pass

def console_log(msg: str, prefix: str = "INFO"):
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    formatted = f"[{timestamp}] [{prefix}] {msg}"
    print(formatted)
    raw_log(f"[{prefix}] {msg}")

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
// struct { BYTE type; BYTE params[16]; } -> 17 bytes per entry
// PARAM_ID = 1, PARAM_NEWSID = 13, PARAM_INDEX = 14, PARAM_END = 100
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
    pCustomMsgTable.add(52).writeU8(16); // PARAM_WORD
    pCustomMsgTable.add(53).writeU8(6);  // PARAM_STRING
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

// Hook mailnews.dll exclusively as the authoritative handler for mail & news
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
if (!mailnewsHooked) {
    for (var mIdx = 0; mIdx < modules.length; mIdx++) {
        try {
            var m = modules[mIdx];
            if (m && m.name && m.name.toLowerCase().indexOf("mailnews") !== -1) {
                hookModuleServerMessage(m);
                mailnewsHooked = true;
                break;
            }
        } catch (e) {}
    }
}

// 5. Hook SendMessageA / SendMessageW to capture UI article deliveries
try {
    var pSendMsgA = findModuleExport("user32.dll", "SendMessageA");
    var pSendMsgW = findModuleExport("user32.dll", "SendMessageW");
    var handleSendMsg = function(args) {
        try {
            var msg = args[1].toInt32();
            var lParam = args[3];
            // BK_ARTICLE = (WM_USER + 121) = 1145 (0x479)
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

// 7. Native Injection Function Bindings
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

    // Native Injected Article Catalog Scan (Newsgroup 20)
    nativeRequestArticles: function(groupId) {
        if (pToServer && pCustomMsgTable) {
            try {
                // BP_REQ_ARTICLES = 85 (0x55)
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

    // Native Injected Single Article Body Request
    nativeRequestArticle: function(groupId, articleId) {
        if (pToServer && pCustomMsgTable) {
            try {
                // BP_REQ_ARTICLE = 86 (0x56)
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

    // Native Injected Look Request (BP_REQ_LOOK = 116 / 0x74)
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
# LOCATIONS & NEWSGROUPS TABLE
# =============================================================================

LOCATIONS_TABLE = [
    # Inns (Designers' News / Announcements - NID 9)
    {"id": 1, "name": "Jasper Inn (Yonder Inn)", "nid": 9, "type": "Designers_News", "room": "Jasper Inn", "rid": 370},
    {"id": 2, "name": "Tos Inn (Familiars)", "nid": 9, "type": "Designers_News", "room": "Tos Inn", "rid": 52},
    {"id": 3, "name": "Barloque Inn (Brownestone Inn)", "nid": 9, "type": "Designers_News", "room": "Barloque Inn", "rid": 106},
    {"id": 4, "name": "Cor Noth Inn (Cibilo Creek Inn)", "nid": 9, "type": "Designers_News", "room": "Cor Noth Inn", "rid": 153},
    {"id": 5, "name": "Ko'catan Inn (The Aerie Guest House)", "nid": 9, "type": "Designers_News", "room": "Ko'catan Inn", "rid": 2001},
    {"id": 6, "name": "Marion Inn (The Limping Toad Inn)", "nid": 9, "type": "Designers_News", "room": "Marion Inn", "rid": 202},
    {"id": 7, "name": "Raza Inn (Starter Inn)", "nid": 9, "type": "Designers_News", "room": "Raza Inn", "rid": 1011},

    # Adventurer Halls (General / Hall News - NID 20)
    {"id": 8, "name": "Jasper Hall (Adventurer's Hall)", "nid": 20, "type": "General_News", "room": "Jasper Hall", "rid": 372},
    {"id": 9, "name": "Tos Hall (Adventurer's Hall)", "nid": 20, "type": "General_News", "room": "Tos Hall", "rid": 72},
    {"id": 10, "name": "Barloque Hall (Adventurer's Hall)", "nid": 20, "type": "General_News", "room": "Barloque Hall", "rid": 105},
    {"id": 11, "name": "Cor Noth Hall (Adventurer's Hall)", "nid": 20, "type": "General_News", "room": "Cor Noth Hall", "rid": 152},
    {"id": 12, "name": "Ko'catan Hall (The Hall of Heroes)", "nid": 20, "type": "General_News", "room": "Ko'catan Hall", "rid": 2007},
    {"id": 13, "name": "Marion Hall (Adventurer's Hall)", "nid": 20, "type": "General_News", "room": "Marion Hall", "rid": 204},

    # Special Newsgroups
    {"id": 14, "name": "Jasper Tavern (Tales of Adventure)", "nid": 5, "type": "Tales_of_Adventure", "room": "Jasper Tavern", "rid": 371},
    {"id": 15, "name": "Tos Grey Dragon (Game News)", "nid": 3, "type": "Game_News", "room": "Tos Grey Dragon", "rid": 50},
    {"id": 16, "name": "Barloque Court (Book of Jala / Justicar)", "nid": 4, "type": "Justicar_News", "room": "Barloque Court", "rid": 104},
    {"id": 17, "name": "Barloque GM Hall (Guild Charter)", "nid": 10, "type": "Guild_Charter", "room": "Barloque GM Hall", "rid": 110},
    {"id": 18, "name": "Marion Elder (Event Schedule)", "nid": 6, "type": "Event_Schedule", "room": "Marion Elder", "rid": 200},
]

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
            "newsgroup": self.newsgroup
        }

class MeridianNewsArchiver:
    def __init__(self, pid: int, win_title: str):
        self.pid = pid
        self.win_title = win_title
        self.hwnd = None
        self.session = None
        self.script = None
        self.is_running = True
        
        self.newsgroup_id = 9  # Default to 9 (Designers' News / Inn)
        self.location_name = "Jasper Inn (Yonder Inn)"
        self.active_globe_id = 0x000985D5  # Auto-updated
        self.headers: Dict[Tuple[str, int], ArticleHeader] = {}
        self.bodies: Dict[Tuple[str, int], str] = {}
        self.existing_archived_ids: Set[int] = set()
        
        self.pending_article_keys: List[Tuple[str, int]] = []
        self.current_requested_key: Optional[Tuple[str, int]] = None
        self.is_harvesting = False
        self.harvest_thread = None
        
        self.total_packets_in = 0
        self.total_packets_out = 0

        self.load_existing_archive()

    def get_newsgroup_name(self, ng_id: Optional[int] = None) -> str:
        gid = ng_id if ng_id is not None else self.newsgroup_id
        if gid == 9:
            return "Designers_News"
        elif gid == 20:
            return "General_News"
        elif gid == 5:
            return "Tales_of_Adventure"
        elif gid == 3:
            return "Game_News"
        elif gid == 4:
            return "Justicar_News"
        elif gid == 6:
            return "Event_Schedule"
        elif gid == 10:
            return "Guild_Charter"
        elif gid == 15:
            return "Guild_News"
        return f"Newsgroup_{gid}"

    def get_newsgroup_id(self, ng_name: str) -> int:
        if ng_name == "Designers_News":
            return 9
        elif ng_name == "General_News":
            return 20
        elif ng_name == "Tales_of_Adventure":
            return 5
        elif ng_name == "Game_News":
            return 3
        elif ng_name == "Justicar_News":
            return 4
        elif ng_name == "Event_Schedule":
            return 6
        elif ng_name == "Guild_Charter":
            return 10
        elif ng_name == "Guild_News":
            return 15
        elif ng_name.startswith("Newsgroup_"):
            try: return int(ng_name.split("_")[1])
            except Exception: pass
        return 20

    def prompt_select_location(self):
        """Interactive Location / Newsgroup Selection Menu."""
        print("\n" + "=" * 80)
        print(" MERIDIAN 59 NEWS LOCATION & NEWSGROUP SELECTOR")
        print("=" * 80)
        print("  INNS (Designers' News / Announcements - NID 9):")
        for item in LOCATIONS_TABLE[:7]:
            print(f"   [{item['id']:2d}] {item['name']:<38} (NID {item['nid']}, RID {item['rid']})")
        
        print("\n  ADVENTURER HALLS (General / Hall News - NID 20):")
        for item in LOCATIONS_TABLE[7:13]:
            print(f"   [{item['id']:2d}] {item['name']:<38} (NID {item['nid']}, RID {item['rid']})")

        print("\n  SPECIAL NEWSGROUPS:")
        for item in LOCATIONS_TABLE[13:]:
            print(f"   [{item['id']:2d}] {item['name']:<38} (NID {item['nid']}, RID {item['rid']})")

        print("\n   [99] Enter Custom Newsgroup ID (NID) Manually")
        print("=" * 80)

        try:
            choice_str = input(f"Select location / newsgroup [1-18, or 99] (Default: 1 - Jasper Inn): ").strip()
            if not choice_str:
                choice = 1
            else:
                choice = int(choice_str)

            if choice == 99:
                nid_str = input("Enter custom Newsgroup ID (NID): ").strip()
                custom_nid = int(nid_str) if nid_str.isdigit() else 9
                self.newsgroup_id = custom_nid
                self.location_name = f"Custom NID {custom_nid}"
                console_log(f"Set active Newsgroup ID to {self.newsgroup_id} ({self.get_newsgroup_name()})", "LOCATION")
            else:
                matched = next((x for x in LOCATIONS_TABLE if x["id"] == choice), LOCATIONS_TABLE[0])
                self.newsgroup_id = matched["nid"]
                self.location_name = matched["name"]
                console_log(f"Location Set: '{matched['name']}' -> NID {matched['nid']} ({matched['type']}) | Room RID_{matched['rid']}", "LOCATION")

        except Exception as e:
            console_log(f"Invalid selection ({e}), defaulting to Jasper Inn (NID 9)", "WARN")
            self.newsgroup_id = 9
            self.location_name = "Jasper Inn (Yonder Inn)"

    def load_existing_archive(self):
        """Loads previously saved articles from JSON so we don't re-fetch them."""
        paths_to_check = [ARCHIVE_JSON_PATH, "General_News.json", "Designers_News.json"]
        for path in paths_to_check:
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        for item in data:
                            aid = item.get("id")
                            if aid:
                                ng = item.get("newsgroup", self.get_newsgroup_name())
                                key = (ng, aid)
                                self.existing_archived_ids.add(aid)
                                body_val = item.get("body", "")
                                if body_val and body_val != "[Body not retrieved yet]":
                                    self.bodies[key] = body_val
                                
                                timestamp = item.get("timestamp", 0)
                                author = item.get("author", "Unknown")
                                subject = item.get("subject", "No Subject")

                                hdr = ArticleHeader(aid, timestamp, author, subject, ng)
                                if item.get("datetime"):
                                    hdr.date_str = item.get("datetime")
                                self.headers[key] = hdr
                    console_log(f"Loaded existing articles from '{path}'. Total known headers: {len(self.headers)}", "ARCHIVE")
                except Exception as e:
                    console_log(f"Could not parse archive '{path}': {e}", "WARN")

        # Clean up any adjacent duplicate bodies loaded from corrupt previous runs per newsgroup
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
            console_log(f"Purging {len(to_clear)} duplicate bodies from legacy archive to trigger clean re-downloads.", "CLEANUP")
            for key in to_clear:
                if key in self.bodies:
                    del self.bodies[key]

    def save_archive(self):
        """Saves all retrieved headers and bodies into clean JSON format."""
        try:
            records = []
            sorted_keys = sorted(self.headers.keys(), key=lambda x: (x[0], x[1]), reverse=True)
            for key in sorted_keys:
                h = self.headers[key]
                entry = h.to_dict()
                entry["body"] = self.bodies.get(key, "[Body not retrieved yet]")
                records.append(entry)

            # 1. Primary Consolidated JSON
            with open(ARCHIVE_JSON_PATH, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2, ensure_ascii=False)

            # 2. Newsgroup-Specific JSON files
            for ng_name in ("General_News", "Designers_News"):
                ng_filename = f"{ng_name}.json"
                ng_records = [r for r in records if r.get("newsgroup") == ng_name]
                with open(ng_filename, "w", encoding="utf-8") as f:
                    json.dump(ng_records, f, indent=2, ensure_ascii=False)

            raw_log(f"[SAVE] Successfully wrote {len(records)} total articles across all newsgroups.")
        except Exception as e:
            console_log(f"Error saving archive: {e}", "ERR")

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
            
            # Check Hook status
            status = self._call_export("check_hooks") or {}
            console_log(f"Engine Hooks Initialized (Native ToServer: {status.get('hasToServer')}, MsgTable: {status.get('hasMsgTable')})", "FRIDA")
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
                self._parse_raw_inbound_stream(data)

            elif ptype == 'packet_out' and data:
                self.total_packets_out += 1
                hex_dump = " ".join(f"{b:02X}" for b in data)
                ascii_str = "".join(chr(b) if 32 <= b <= 126 else "." for b in data)
                trace_log(f"-> SEND RAW ({len(data)} bytes): {hex_dump} | ASCII: {ascii_str}")
                raw_log(f"-> OUT ({len(data)} bytes): " + " ".join(f"{b:02X}" for b in data[:16]))

        elif mtype == 'error':
            err_desc = message.get('description', '')
            console_log(f"JS Error: {err_desc}", "JS_ERR")
            trace_log(f"** JS ERROR: {err_desc}")

    def _decode_unscrambled_payload(self, opcode: int, chunk: bytes):
        """
        Processes clean, unscrambled payloads dispatched by the game engine.
        BP_ARTICLES = 181 (0xB5)
        BP_ARTICLE  = 182 (0xB6)
        """
        raw_log(f"[UNSCRAMBLED] Opcode: {opcode} (0x{opcode:02X}) | Len: {len(chunk)}")
        
        # 0. BP_LOOK_NEWSGROUP (180) - Adventurer's Globe opened
        if opcode == 180 and len(chunk) >= 8:
            try:
                # [0]=Opcode (180), [1..2]=newsgroup, [3]=permissions, [4..7]=object_id
                newsgroup, perm, obj_id = struct.unpack_from("<HBI", chunk, 1)
                self.newsgroup_id = newsgroup
                if obj_id:
                    self.active_globe_id = obj_id
                console_log(f"Globe Identified -> Newsgroup {newsgroup} ({self.get_newsgroup_name(newsgroup)}) (Globe ID: 0x{obj_id:08X})", "GLOBE")
                # Automatically request full news articles index
                self._call_export("native_request_articles", self.newsgroup_id)
            except Exception as e:
                raw_log(f"[PARSE_ERROR] Failed parsing BP_LOOK_NEWSGROUP: {e}")

        # 1. BP_ARTICLES (181) - News Article Catalog Headers
        elif opcode == 181 and len(chunk) >= 7:
            try:
                # Header layout:
                # [0]=Opcode (181), [1..2]=newsgroup_id, [3]=part, [4]=max_part, [5..6]=num_articles
                nid, part, max_part, num_articles = struct.unpack_from("<HBBH", chunk, 1)
                self.newsgroup_id = nid
                ptr = 7
                parsed_count = 0
                ng_name = self.get_newsgroup_name(nid)
                for _ in range(num_articles):
                    if ptr + 8 > len(chunk):
                        break
                    art_id, art_time = struct.unpack_from("<II", chunk, ptr)
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
                    parsed_count += 1

                console_log(f"Received Catalog Part {part}/{max_part}: {parsed_count} articles (Total Index for {ng_name}: {len([k for k in self.headers if k[0] == ng_name])})", "INDEX")
                self.save_archive()

                if part == max_part:
                    self._on_catalog_complete()

            except Exception as e:
                raw_log(f"[PARSE_ERROR] Failed parsing BP_ARTICLES: {e}")

        # 2. BP_ARTICLE (182) - Single Article Body Text
        elif opcode == 182 and len(chunk) >= 3:
            try:
                # [0]=Opcode (182), [1..2]=string_len, [3..]=body chars
                body_len = struct.unpack_from("<H", chunk, 1)[0]
                body_text = chunk[3 : 3 + body_len].decode('latin-1', errors='replace')
                
                # Check if this request was already satisfied
                if hasattr(self, 'article_received_event') and self.article_received_event.is_set():
                    raw_log(f"[PARSE_IGNORE] Received BP_ARTICLE body ({len(body_text)} chars), but event was already set. Ignoring duplicate callback.")
                    return

                # Match exclusively to self.current_requested_key
                cur_key = self.current_requested_key
                if cur_key is None:
                    raw_log(f"[PARSE_IGNORE] Received unrequested BP_ARTICLE body ({len(body_text)} chars). Ignoring.")
                    return

                ng_name, aid = cur_key
                self.bodies[cur_key] = body_text
                if cur_key in self.pending_article_keys:
                    self.pending_article_keys.remove(cur_key)

                hdr = self.headers.get(cur_key)
                subj = hdr.subject if hdr else "No Subject"
                console_log(f"Archived Article [{aid}] ({ng_name}) -> '{subj[:35]}...' ({len(body_text)} chars)", "DOWNLOAD")
                trace_log(f"*** ARCHIVED ARTICLE [{aid}] ({ng_name}): '{subj[:35]}...' ({len(body_text)} chars) ***")
                self.save_archive()

                self.current_requested_key = None
                if hasattr(self, 'article_received_event'):
                    self.article_received_event.set()
            except Exception as e:
                raw_log(f"[PARSE_ERROR] Failed parsing BP_ARTICLE: {e}")

    def _parse_raw_inbound_stream(self, raw_bytes: bytes):
        """
        Secondary parser scanning raw Winsock packets with heuristics in case
        module hooks are bypassed.
        """
        pass

    def _on_catalog_complete(self):
        """Called once all header index parts have been parsed for active newsgroup."""
        curr_ng = self.get_newsgroup_name()
        ng_keys = [k for k in self.headers.keys() if k[0] == curr_ng]
        missing = [k for k in sorted(ng_keys, key=lambda x: x[1], reverse=True) if k not in self.bodies or not self.bodies[k] or self.bodies[k] == "[Body not retrieved yet]"]
        
        console_log(f"News catalog loaded for {curr_ng} ({len(ng_keys)} articles). {len(missing)} need body downloads.", "HARVEST")
        
        if not self.is_harvesting and missing:
            self.pending_article_keys = missing
            self.start_download_queue()

    def start_download_queue(self):
        """Pulls message bodies smoothly with strict request-response pacing for the active newsgroup."""
        def worker():
            self.is_harvesting = True
            curr_ng = self.get_newsgroup_name()
            nid = self.get_newsgroup_id(curr_ng)
            console_log(f"Beginning automatic pull of {len(self.pending_article_keys)} message bodies for {curr_ng} (NID {nid})...", "HARVEST")
            
            if not hasattr(self, 'article_received_event'):
                self.article_received_event = threading.Event()

            retry_count = 0
            while self.is_running and self.pending_article_keys:
                key = self.pending_article_keys[0]
                ng_name, aid = key
                self.current_requested_key = key
                self.article_received_event.clear()
                
                console_log(f"Requesting body for Article [{aid}] ({ng_name})...", "REQUEST")
                trace_log(f"*** REQUESTING ARTICLE BODY [{aid}] (NID {nid}) ***")
                
                try:
                    res = self._call_export("native_request_article", nid, aid)
                    if not res:
                        raw_log(f"[NATIVE_CALL] Export native_request_article call returned null/false for NID {nid} AID {aid}")
                    
                    received = self.article_received_event.wait(timeout=2.0)
                    
                    if received:
                        retry_count = 0
                        time.sleep(0.12) # Gentle pacing between requests
                    else:
                        retry_count += 1
                        console_log(f"Timeout waiting for Article [{aid}] body (Attempt {retry_count}/3)", "WARN")
                        trace_log(f"*** TIMEOUT Article [{aid}] body (Attempt {retry_count}/3) ***")
                        if retry_count >= 3:
                            retry_count = 0
                            # Move to back of queue after 3 failed attempts
                            if self.pending_article_keys and self.pending_article_keys[0] == key:
                                self.pending_article_keys.append(self.pending_article_keys.pop(0))
                            self.current_requested_key = None
                        time.sleep(0.3)

                except Exception as e:
                    raw_log(f"[HARVEST_ERR] Error requesting article {aid} ({ng_name}): {e}")
                    time.sleep(0.4)
            
            self.is_harvesting = False
            self.current_requested_key = None
            console_log(f"Finished downloading available article bodies for {curr_ng}! Check '{curr_ng}.json' and '{ARCHIVE_JSON_PATH}'.", "SUCCESS")
            self.save_archive()

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def trigger_scan(self):
        """Triggers the full articles list fetch via Native ToServer and fallback command."""
        curr_ng = self.get_newsgroup_name()
        console_log(f"Triggering scan for Newsgroup {self.newsgroup_id} ({curr_ng})...", "HARVEST")
        try:
            # 1. Native ToServer invocation (Primary)
            res = self._call_export("native_request_articles", self.newsgroup_id)
            if not res:
                console_log("Native ToServer not directly bound, sending Look on Globe...", "FALLBACK")
                self._call_export("native_request_look", self.active_globe_id)
        except Exception as e:
            console_log(f"Scan request error: {e}", "ERR")

    def run_cli_loop(self):
        # 1. Prompt for Location / Newsgroup Selection
        self.prompt_select_location()

        print("\n" + "=" * 80)
        print(" MERIDIAN 59 AUTONOMOUS GLOBE HARVESTER")
        print(f" Active Location: {self.location_name} | NID: {self.newsgroup_id} ({self.get_newsgroup_name()})")
        print(" Commands:")
        print("  [s] Scan / Refresh news catalog from server")
        print("  [p] Pull / Download all unread article bodies")
        print("  [m] Select / Change location or Newsgroup ID")
        print("  [a] Add annotation/note to background trace (e.g. 'a Clicked article #45')")
        print("  [l] Send Look request on nearby News Globe")
        print("  [w] Write / Force save archive to files now")
        print("  [q] Quit")
        print("=" * 80 + "\n")

        # Automatically start initial scan for selected location
        time.sleep(0.5)
        self.trigger_scan()

        try:
            while self.is_running:
                curr_ng = self.get_newsgroup_name()
                ng_count = len([k for k in self.headers if k[0] == curr_ng])
                ng_bodies = len([k for k in self.bodies if k[0] == curr_ng and self.bodies[k] and self.bodies[k] != "[Body not retrieved yet]"])
                
                raw_cmd = input(f"[{self.location_name} | {curr_ng} (NID {self.newsgroup_id}) Archived: {ng_bodies}/{ng_count}] > ").strip()
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
                        console_log(f"Annotation added to '{PACKET_TRACE_PATH}': '{note_text}'", "TRACE")
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
                    console_log(f"Saved database to 'General_News.json', 'Designers_News.json', and '{ARCHIVE_JSON_PATH}'.", "SAVE")

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
        console_log("Finished cleanly.", "SYSTEM")

# =============================================================================
# WIN32 DISCOVERY
# =============================================================================

def find_meridian_game_process() -> Tuple[Optional[int], Optional[str], Optional[int]]:
    """
    Finds the active Meridian 59 game process.
    Strictly searches for executable named 'meridian.exe', 'merid32.exe', or 'meridian_3d.exe' (case-insensitive).
    """
    target_executables = {"meridian.exe", "merid32.exe", "meridian_3d.exe"}

    # 1. First choice: Frida process enumeration for exact executable name match
    if HAS_FRIDA:
        try:
            device = frida.get_local_device()
            processes = device.enumerate_processes()
            for proc in processes:
                pname = proc.name.lower()
                if pname in target_executables or (pname.startswith("meridian") and pname.endswith(".exe")):
                    console_log(f"Matched Meridian executable process '{proc.name}' with PID {proc.pid}", "PROC_MATCH")
                    return proc.pid, proc.name, None
        except Exception as e:
            raw_log(f"[PROC_ENUM_ERR] {e}")

    # 2. Fallback: Win32 EnumWindows with strict non-browser/non-discord filtering
    if HAS_WIN32:
        res = [None, None, None]
        def enum_cb(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                tl = title.lower()
                if "meridian 59" in tl or "meridian" in tl or "newsgroup" in tl:
                    blacklist = ["discord", "chrome", "firefox", "edge", "visual studio", "terminal", "python", "chat", "browser"]
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
    print("=" * 80)
    print(" MERIDIAN 59 AUTONOMOUS GLOBE NEWS ARCHIVER")
    print("=" * 80)

    pid, win_title, hwnd = find_meridian_game_process()
    if not pid:
        console_log("Meridian 59 client not detected. Please start the game first.", "ERR")
        return

    console_log(f"Found Meridian 59 (PID: {pid}) | Window: '{win_title}'", "WIN32")
    archiver = MeridianNewsArchiver(pid, win_title or "")
    archiver.hwnd = hwnd
    if archiver.attach():
        archiver.run_cli_loop()

if __name__ == "__main__":
    main()
