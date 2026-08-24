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
        root_logger.addHandler(c_handler)

    # File Handler (logs/companion_debug.log)
    if CURRENT_LOG_SETTINGS["file_debug_enabled"]:
        try:
            f_handler = logging.FileHandler("logs/companion_debug.log", encoding="utf-8")
            f_handler.setLevel(logging.DEBUG if CURRENT_LOG_SETTINGS["console_debug_enabled"] else logging.INFO)
            f_handler.setFormatter(logging.Formatter(log_format))
            root_logger.addHandler(f_handler)
        except Exception as e:
            if CURRENT_LOG_SETTINGS["console_output_enabled"]:
                print(f"[M59-LOG] Warning: Could not initialize companion_debug.log: {e}")

    # Dedicated Progression Log Handler (logs/progression_debug.log)
    prog_logger = logging.getLogger("m59.progression")
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

