import threading
import logging
import time
from m59_logging import is_frida_debug_enabled, log_frida

# --- Meridian 59 Improved Wholist Engine (ASLR-Safe) ---
# Based on wholist-perfect.py (Direct Memory Polling with Relative Offsets)

FRIDA_JS_CODE = """
const log = (msg) => send({type: 'log', data: msg});

var currentUsersPtrAddr = null;
var LookupRscAddr = null;

function start() {
    var config = {};
    try {
        var file = new File("settings\\config.json", "r");
        config = JSON.parse(file.read());
        file.close();
    } catch(e) {}
    var target = (config && config.process && config.process.target_name) ? config.process.target_name : "meridian.exe";
    var meridian = Process.findModuleByName(target) || Process.findModuleByName("meridian.exe") || Process.findModuleByName("Meridian.exe");
    if (!meridian) {
        log("ERROR: Target module " + target + " not found.");
        return;
    }

    var base = meridian.base;
    var size = meridian.size;

    // 1. Resolve LookupRsc using exports
    try {
        var exps = meridian.enumerateExports();
        for (var i = 0; i < exps.length; i++) {
            if (exps[i].name === "LookupRsc" || exps[i].name === "LookupNameRsc") {
                LookupRscAddr = exps[i].address;
                break;
            }
        }
    } catch (e) {
        log("Error enumerating exports: " + e.message);
    }

    if (LookupRscAddr) {
        log("Found 'LookupRsc' export at: " + LookupRscAddr);
    } else {
        log("ERROR: 'LookupRsc' export not found!");
    }

    // 2. Dynamically scan and locate the correct current_users pointer
    var traversalSig = "8B 0D ?? ?? ?? ?? 85 C9 74 ?? 8B 01";
    log("Scanning for dynamic traversal pattern: " + traversalSig);
    try {
        if (LookupRscAddr) {
            var lookupRsc = new NativeFunction(LookupRscAddr, 'pointer', ['uint32']);
            var matches = Memory.scanSync(base, size, traversalSig);
            log("Found " + matches.length + " traversal candidate(s). Verifying...");
            
            for (var idx = 0; idx < matches.length; idx++) {
                var matchAddr = matches[idx].address;
                var ptrAddr = matchAddr.add(2).readPointer();
                
                if (ptrAddr.isNull()) continue;
                
                try {
                    var head = ptrAddr.readPointer();
                    if (head.isNull()) continue;
                    
                    // Traverse first few nodes of the list to see if they look like players
                    var currNode = head;
                    var validCount = 0;
                    var safety = 0;
                    
                    while (!currNode.isNull() && safety < 5) {
                        try {
                            var objPtr = currNode.readPointer();
                            if (!objPtr.isNull()) {
                                var nameResId = objPtr.add(8).readU32();
                                if (nameResId > 0 && nameResId < 150000) {
                                    var nameStrPtr = lookupRsc(nameResId);
                                    if (!nameStrPtr.isNull()) {
                                        var name = nameStrPtr.readCString();
                                        // Player names are alphanumeric, starting with a capital letter, length 2 to 20
                                        if (name && /^[A-Za-z0-9 ]+$/.test(name) && name.length >= 2 && name.length <= 20) {
                                            validCount++;
                                        }
                                    }
                                }
                            }
                        } catch (e) {
                            // Not a valid list node
                        }
                        currNode = currNode.add(8).readPointer();
                        safety++;
                    }
                    
                    if (validCount > 0) {
                        currentUsersPtrAddr = ptrAddr;
                        log("Verification Success! Located player list pointer at: " + currentUsersPtrAddr + " (verified with " + validCount + " active player(s))");
                        break;
                    }
                } catch (e) {
                    // Invalid pointer dereference, skip
                }
            }
        }
    } catch (e) {
        log("Error scanning/verifying candidates: " + e.message);
    }

    if (!currentUsersPtrAddr) {
        var fallbackOffset = 0x2A89A0;
        currentUsersPtrAddr = base.add(fallbackOffset);
        log("Active player list not located dynamically. Using known offset fallback: 0x" + fallbackOffset.toString(16).toUpperCase());
    }

    rpc.exports = {
        getlist: function() {
            if (!LookupRscAddr || !currentUsersPtrAddr) return [];
            try {
                var lookupRsc = new NativeFunction(LookupRscAddr, 'pointer', ['uint32']);
                var head = currentUsersPtrAddr.readPointer();
                if (head.isNull()) return [];

                var players = [];
                var currNode = head;
                var safety = 0;

                while (!currNode.isNull() && safety < 1000) {
                    var objPtr = currNode.readPointer(); 
                    if (!objPtr.isNull()) {
                        var nameResId = objPtr.add(8).readU32();
                        var nameStrPtr = lookupRsc(nameResId);
                        if (!nameStrPtr.isNull()) {
                            var name = nameStrPtr.readCString();
                            
                            // Protocol Flags (Fixed Bits)
                            // Bits 0x4000, 0x8000, 0x10000 describe player type
                            var flags = objPtr.add(20).readU32();
                            var pType = flags & 0x1C000;
                            var status = "WHITE";
                            
                            if (pType === 0x10000 || name === "Zaphod") status = "YELLOW";
                            else if (pType === 0xC000 || pType === 0x14000 || pType === 0x1C000) status = "BLUE";
                            else if (pType === 0x4000) status = "RED";
                            else if (pType === 0x8000) status = "ORANGE"; 

                            if (name && name.length > 1) {
                                players.push({name: name, status: status});
                            }
                        }
                    }
                    currNode = currNode.add(8).readPointer();
                    safety++;
                }
                return players;
            } catch (e) { return []; }
        }
    };
    log("Discovery Complete. ASLR-safe monitoring active.");
}

start();
"""

logger = logging.getLogger("m59.wholist")

class WhoListMonitor:
    def __init__(self, target_pid, on_update_callback):
        self.target_pid = target_pid
        self.on_update_callback = on_update_callback
        self.frida_session = None
        self.frida_script = None
        self.running = False
        self.players = {} # {name: status}

    def start(self):
        """Initializes the monitor in a background thread."""
        if self.running:
            return
        self.running = True
        threading.Thread(target=self._run_frida, daemon=True).start()

    def stop(self):
        """Stops the monitor and detaches Frida safely without blocking RPC threads."""
        self.running = False
        script = self.frida_script
        session = self.frida_session
        self.frida_script = None
        self.frida_session = None

        def _async_detach():
            if script:
                try: script.unload()
                except Exception: pass
            if session:
                try: session.detach()
                except Exception: pass

        threading.Thread(target=_async_detach, daemon=True).start()

    def _run_frida(self):
        try:
            import frida
            if is_frida_debug_enabled():
                log_frida(f"WhoList: Attaching to PID {self.target_pid}...", "info")
            session = frida.attach(self.target_pid)
            self.frida_session = session
            
            script = session.create_script(FRIDA_JS_CODE)
            self.frida_script = script
            
            def on_message(message, data):
                if not is_frida_debug_enabled():
                    return
                if message['type'] == 'send':
                    payload = message['payload']
                    if isinstance(payload, dict) and payload.get('type') == 'log':
                        log_frida(f"FridaLog: {payload.get('data')}", "debug")
            
            script.on('message', on_message)
            script.load()
            if is_frida_debug_enabled():
                log_frida("WhoList: ASLR-safe Memory Polling active.", "info")

            # Polling Loop
            while self.running and script and self.frida_script:
                if not self.running:
                    break
                try:
                    current_data = script.exports_sync.getlist()
                    if not self.running:
                        break
                    
                    # Map colors to Dashboard status tags
                    status_map = {
                        "WHITE": "INNOCENT",
                        "ORANGE": "OUTLAW",
                        "RED": "MURDERER",
                        "BLUE": "STAFF",
                        "YELLOW": "CREATOR"
                    }
                    
                    new_players = {}
                    for p in current_data:
                        raw_status = p['status']
                        new_players[p['name']] = status_map.get(raw_status, "INNOCENT")

                    if new_players != self.players and self.running:
                        self.players = new_players
                        if self.on_update_callback:
                            self.on_update_callback(self.players)
                            
                except Exception as e:
                    if is_frida_debug_enabled():
                        log_frida(f"WhoList: Polling error: {e}", "debug")
                    break
                
                # Interruptible sleep in 100ms intervals
                for _ in range(20):
                    if not self.running:
                        break
                    time.sleep(0.1)
            
        except Exception as e:
            if is_frida_debug_enabled():
                log_frida(f"WhoList: Frida Error: {e}", "error")
            self.running = False

    def trigger_silent_update(self):
        """Manual update requested - polling is already active."""
        if is_frida_debug_enabled():
            log_frida("WhoList: Manual update requested (Polling is already active).", "debug")
