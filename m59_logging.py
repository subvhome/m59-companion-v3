import logging
import sys
import os
import json

# Create a central logger
logger = logging.getLogger("m59")

# Global flags tracking current active state
CURRENT_LOG_SETTINGS = {
    "console_output_enabled": True,
    "console_debug_enabled": True,
    "file_debug_enabled": True,
    "progression_log_enabled": True
}

# --- Frida Memory Polling / Diagnostic Debug Flag ---
# Set to True manually in code OR launch the application/compiled binary with '/frida_debug'
ENABLE_FRIDA_DEBUG = False
FRIDA_DEBUG = False

def is_frida_debug_enabled():
    """
    Returns True if Frida debug logging is explicitly enabled.
    Can be enabled via:
      1. Setting ENABLE_FRIDA_DEBUG = True or FRIDA_DEBUG = True in python code.
      2. Passing '/frida_debug', '--frida_debug', or '-frida_debug' CLI argument.
      3. Setting environment variable M59_FRIDA_DEBUG=1 or FRIDA_DEBUG=1.
    """
    global ENABLE_FRIDA_DEBUG, FRIDA_DEBUG
    if ENABLE_FRIDA_DEBUG or FRIDA_DEBUG:
        return True
    
    # Check environment variable
    if os.environ.get("FRIDA_DEBUG", "").lower() in ("1", "true", "yes") or \
       os.environ.get("M59_FRIDA_DEBUG", "").lower() in ("1", "true", "yes"):
        return True

    # Check CLI arguments
    for arg in sys.argv[1:]:
        clean = arg.strip().lower()
        if clean in ("/frida_debug", "--frida_debug", "-frida_debug", "/fridadebug", "--fridadebug", "/frida", "--frida"):
            return True

    return False

def set_frida_debug(enabled: bool):
    """Programmatically sets the Frida debug logging state."""
    global ENABLE_FRIDA_DEBUG, FRIDA_DEBUG
    ENABLE_FRIDA_DEBUG = bool(enabled)
    FRIDA_DEBUG = bool(enabled)

class FridaLogFilter(logging.Filter):
    """
    Suppresses all Frida instrumentation and memory polling log records
    unless Frida debug is explicitly active.
    """
    def filter(self, record):
        if is_frida_debug_enabled():
            return True
        name = str(getattr(record, 'name', ''))
        if name.endswith('.frida') or '.frida.' in name or name == 'm59.frida':
            return False
        msg = str(record.getMessage() if hasattr(record, 'getMessage') else getattr(record, 'msg', ''))
        if "FridaLog" in msg or "[Frida]" in msg or "Frida Error" in msg:
            return False
        return True

def log_frida(msg, level="debug"):
    """Logs a message from the Frida subsystem only if Frida debugging is enabled."""
    if not is_frida_debug_enabled():
        return
    f_logger = logging.getLogger("m59.frida")
    if level == "error":
        f_logger.error(f"[Frida] {msg}")
    elif level == "warning":
        f_logger.warning(f"[Frida] {msg}")
    elif level == "info":
        f_logger.info(f"[Frida] {msg}")
    else:
        f_logger.debug(f"[Frida] {msg}")

def load_stored_log_settings():
    """Loads logging preferences from settings/gui_settings.json if present."""
    candidate_paths = [
        os.path.join("settings", "gui_settings.json"),
        "gui_settings.json",
        os.path.join("settings", "settings.json"),
        "settings.json"
    ]
    for p in candidate_paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return {
                            "console_output_enabled": data.get("console_output_enabled", True),
                            "console_debug_enabled": data.get("console_debug_enabled", True),
                            "file_debug_enabled": data.get("file_debug_enabled", True),
                            "progression_log_enabled": data.get("progression_log_enabled", True)
                        }
            except Exception:
                pass
    return CURRENT_LOG_SETTINGS.copy()

def setup_logging(debug_enabled=None, console_output=None, file_debug=None, progression_log=None):
    """
    Configures global logging handlers based on preferences.
    """
    stored = load_stored_log_settings()
    
    if console_output is None:
        console_output = stored.get("console_output_enabled", True)
    if debug_enabled is None:
        debug_enabled = stored.get("console_debug_enabled", True)
    if file_debug is None:
        file_debug = stored.get("file_debug_enabled", True)
    if progression_log is None:
        progression_log = stored.get("progression_log_enabled", True)

    CURRENT_LOG_SETTINGS["console_output_enabled"] = bool(console_output)
    CURRENT_LOG_SETTINGS["console_debug_enabled"] = bool(debug_enabled)
    CURRENT_LOG_SETTINGS["file_debug_enabled"] = bool(file_debug)
    CURRENT_LOG_SETTINGS["progression_log_enabled"] = bool(progression_log)

    if not os.path.exists("logs"):
        try:
            os.makedirs("logs", exist_ok=True)
        except Exception:
            pass

    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    
    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    frida_filter = FridaLogFilter()
    root_logger.addFilter(frida_filter)
    
    # Clean existing root handlers
    for handler in root_logger.handlers[:]:
        try:
            handler.close()
        except Exception:
            pass
        root_logger.removeHandler(handler)

    # Console Handler (stdout)
    if CURRENT_LOG_SETTINGS["console_output_enabled"]:
        c_handler = logging.StreamHandler(sys.stdout)
        c_handler.setLevel(logging.DEBUG if CURRENT_LOG_SETTINGS["console_debug_enabled"] else logging.INFO)
        c_handler.setFormatter(logging.Formatter(log_format))
        c_handler.addFilter(frida_filter)
        root_logger.addHandler(c_handler)

    # File Handler (logs/companion_debug.log)
    if CURRENT_LOG_SETTINGS["file_debug_enabled"]:
        try:
            f_handler = logging.FileHandler("logs/companion_debug.log", encoding="utf-8")
            f_handler.setLevel(logging.DEBUG if CURRENT_LOG_SETTINGS["console_debug_enabled"] else logging.INFO)
            f_handler.setFormatter(logging.Formatter(log_format))
            f_handler.addFilter(frida_filter)
            root_logger.addHandler(f_handler)
        except Exception as e:
            if CURRENT_LOG_SETTINGS["console_output_enabled"]:
                print(f"[M59-LOG] Warning: Could not initialize companion_debug.log: {e}")

    # Dedicated Progression Log Handler (logs/progression_debug.log)
    prog_logger = logging.getLogger("m59.progression")
    prog_logger.addFilter(frida_filter)
    for handler in prog_logger.handlers[:]:
        try:
            handler.close()
        except Exception:
            pass
        prog_logger.removeHandler(handler)

    if CURRENT_LOG_SETTINGS["progression_log_enabled"]:
        try:
            prog_handler = logging.FileHandler("logs/progression_debug.log", encoding="utf-8")
            prog_handler.setLevel(logging.DEBUG)
            prog_handler.setFormatter(logging.Formatter(log_format))
            prog_handler.addFilter(frida_filter)
            prog_logger.addHandler(prog_handler)
        except Exception as e:
            if CURRENT_LOG_SETTINGS["console_output_enabled"]:
                print(f"[M59-LOG] Warning: Could not initialize progression_debug.log: {e}")

    logger.info(f"Logging initialized | Console: {CURRENT_LOG_SETTINGS['console_output_enabled']} (Debug: {CURRENT_LOG_SETTINGS['console_debug_enabled']}) | FileDebug: {CURRENT_LOG_SETTINGS['file_debug_enabled']} | ProgressionLog: {CURRENT_LOG_SETTINGS['progression_log_enabled']}")

def clear_log_files():
    """Safely flushes and truncates existing log files."""
    log_files = ["logs/companion_debug.log", "logs/progression_debug.log"]
    cleared = []
    for lf in log_files:
        if os.path.exists(lf):
            try:
                with open(lf, "w", encoding="utf-8") as f:
                    f.truncate(0)
                cleared.append(lf)
            except Exception as e:
                print(f"[M59-LOG] Could not clear {lf}: {e}")
    return cleared

def get_logger(name=None):
    if name:
        return logging.getLogger(f"m59.{name}")
    return logger

