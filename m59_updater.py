import os
import sys
import time
import re
import urllib.request
import subprocess
import webbrowser
import threading

# Primary and Fallback Release Repositories
REPO_URLS = [
    {
        "version_url": "https://raw.githubusercontent.com/subvhome/m59-companion/main/VERSION",
        "version_beta_url": "https://raw.githubusercontent.com/subvhome/m59-companion/main/VERSION_BETA",
        "readme_url": "https://raw.githubusercontent.com/subvhome/m59-companion/main/README.md",
        "stable_exe_url": "https://github.com/subvhome/m59-companion/raw/main/dist/M59Companion.exe",
        "beta_exe_url": "https://github.com/subvhome/m59-companion/raw/main/dist/M59Companion_beta.exe",
        "github_site": "https://github.com/subvhome/m59-companion"
    },
    {
        "version_url": "https://raw.githubusercontent.com/Substance-V/m59companion/main/VERSION",
        "version_beta_url": "https://raw.githubusercontent.com/Substance-V/m59companion/main/VERSION_BETA",
        "readme_url": "https://raw.githubusercontent.com/Substance-V/m59companion/main/README.md",
        "stable_exe_url": "https://github.com/Substance-V/m59companion/raw/main/dist/M59Companion.exe",
        "beta_exe_url": "https://github.com/Substance-V/m59companion/raw/main/dist/M59Companion_beta.exe",
        "github_site": "https://github.com/Substance-V/m59companion"
    }
]

def parse_version_tuple(v_str):
    """
    Parses versions like '1.8.0', 'v1.8.1', '1.8.2-beta', '3.0b', '1.8.0.1' into comparable tuples:
    Returns ((major, minor, patch, build), is_beta, raw_str)
    """
    if not v_str:
        return ((0, 0, 0, 0), False, "")
    v_clean = str(v_str).strip().lower().replace('v', '')
    is_beta = 'beta' in v_clean or 'dev' in v_clean or 'rc' in v_clean or 'b' in v_clean
    
    # Extract numerical components
    nums = [int(x) for x in re.findall(r'\d+', v_clean)]
    while len(nums) < 4:
        nums.append(0)
    return (tuple(nums[:4]), is_beta, v_str.strip())

def is_version_newer(candidate_ver, reference_ver):
    """Returns True if candidate_ver is strictly newer than reference_ver."""
    c_nums, c_beta, _ = parse_version_tuple(candidate_ver)
    r_nums, r_beta, _ = parse_version_tuple(reference_ver)
    
    if c_nums > r_nums:
        return True
    elif c_nums == r_nums:
        # If numerical parts are identical: Stable (not beta) > Beta
        if not c_beta and r_beta:
            return True
    return False

def get_installed_version():
    """Reads the local VERSION_BETA or VERSION file."""
    # 1. Check VERSION_BETA first
    if os.path.exists("VERSION_BETA"):
        try:
            with open("VERSION_BETA", "r", encoding="utf-8") as f:
                lines = f.read().strip().splitlines()
                if lines and lines[0].strip():
                    return lines[0].strip()
        except Exception:
            pass

    # 2. Check VERSION
    if os.path.exists("VERSION"):
        try:
            with open("VERSION", "r", encoding="utf-8") as f:
                lines = f.read().strip().splitlines()
                if lines and lines[0].strip():
                    return lines[0].strip()
        except Exception:
            pass

    return "3.0b"

def fetch_url_text(url, timeout=5):
    """Safely retrieves UTF-8 decoded text from a URL with cache-busting timestamp."""
    try:
        busting_url = f"{url}?t={int(time.time())}"
        req = urllib.request.Request(
            busting_url,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) M59CompanionUpdater/1.0'}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode('utf-8', errors='replace').strip()
    except Exception:
        return None

def check_all_releases(current_version=None):
    """
    Fetches both Stable and Beta release metadata from remote repo.
    Returns a dictionary with full release comparison data.
    """
    if current_version is None:
        current_version = get_installed_version()

    result = {
        "current_version": current_version,
        "is_current_beta": 'beta' in str(current_version).lower() or 'b' in str(current_version).lower(),
        "stable_version": None,
        "stable_notes": "",
        "stable_exe_url": None,
        "stable_update_available": False,
        "beta_version": None,
        "beta_notes": "",
        "beta_exe_url": None,
        "beta_update_available": False,
        "github_site": REPO_URLS[0]["github_site"],
        "error": None
    }

    fetched = False
    for repo in REPO_URLS:
        version_txt = fetch_url_text(repo["version_url"])
        version_beta_txt = fetch_url_text(repo.get("version_beta_url")) if repo.get("version_beta_url") else None
        readme_txt = fetch_url_text(repo["readme_url"])

        if version_txt:
            v_lines = version_txt.splitlines()
            stable_v = v_lines[0].strip().replace('v', '') if v_lines else "1.8.0"
            stable_notes = "\n".join(v_lines[1:]).strip() if len(v_lines) > 1 else ""

            # Check for Beta release information: 1. VERSION_BETA, 2. README.md, 3. Fallback
            beta_v = None
            beta_notes = "Latest experimental preview build with upcoming features."

            if version_beta_txt:
                b_lines = version_beta_txt.splitlines()
                if b_lines and b_lines[0].strip():
                    beta_v = b_lines[0].strip().replace('v', '')
                    if len(b_lines) > 1 and "\n".join(b_lines[1:]).strip():
                        beta_notes = "\n".join(b_lines[1:]).strip()

            if not beta_v and readme_txt:
                match = re.search(r"M59Companion_beta\.exe\s*\(([^\)]+)\)", readme_txt, re.IGNORECASE)
                if match:
                    beta_v = match.group(1).strip().replace('v', '')
                else:
                    match2 = re.search(r"-\s*\*\*.*Beta.*?\*\*:\s*\[.*?(v[\d\.\w\-]+).*?\]", readme_txt, re.IGNORECASE)
                    if match2:
                        beta_v = match2.group(1).strip().replace('v', '')

            if not beta_v:
                parts = stable_v.split('.')
                if len(parts) >= 1:
                    try:
                        p_last = str(int(parts[-1]) + 1)
                        beta_v = ".".join(parts[:-1] + [p_last]) + "-beta"
                    except Exception:
                        beta_v = f"{stable_v}-beta"
                else:
                    beta_v = f"{stable_v}-beta"

            result["stable_version"] = stable_v
            result["stable_notes"] = stable_notes or "Latest verified production release with stable features."
            result["stable_exe_url"] = repo["stable_exe_url"]
            result["stable_update_available"] = is_version_newer(stable_v, current_version)

            result["beta_version"] = beta_v
            result["beta_notes"] = beta_notes
            result["beta_exe_url"] = repo["beta_exe_url"]
            result["beta_update_available"] = is_version_newer(beta_v, current_version) or (
                ('beta' in str(current_version).lower() or 'b' in str(current_version).lower()) and is_version_newer(beta_v, current_version)
            )
            result["github_site"] = repo["github_site"]
            fetched = True
            break

    if not fetched:
        result["error"] = "Unable to connect to GitHub update servers."

    return result

def check_for_updates(current_version):
    """Legacy backward-compatible wrapper returning (update_available, remote_version, release_notes)"""
    res = check_all_releases(current_version)
    if res.get("stable_update_available"):
        return True, res["stable_version"], res["stable_notes"]
    elif res.get("beta_update_available"):
        return True, res["beta_version"], res["beta_notes"]
    return False, res.get("stable_version"), res.get("stable_notes")

def download_update(target_type='stable', on_progress=None):
    """
    Downloads the selected executable (stable or beta) with optional progress callback.
    on_progress(downloaded_bytes, total_bytes, percentage_float)
    """
    releases = check_all_releases()
    url = releases["beta_exe_url"] if target_type == 'beta' else releases["stable_exe_url"]
    
    if not url:
        url = REPO_URLS[0]["beta_exe_url"] if target_type == 'beta' else REPO_URLS[0]["stable_exe_url"]

    temp_path = "M59Companion_update_temp.exe"
    
    try:
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) M59CompanionUpdater/1.0'}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            total_size = resp.info().get('Content-Length')
            total_size = int(total_size) if total_size else None
            downloaded = 0
            block_size = 64 * 1024  # 64KB chunks

            with open(temp_path, "wb") as out_file:
                while True:
                    chunk = resp.read(block_size)
                    if not chunk:
                        break
                    out_file.write(chunk)
                    downloaded += len(chunk)
                    if on_progress and total_size:
                        pct = (downloaded / total_size) * 100.0
                        on_progress(downloaded, total_size, pct)

        if os.path.exists(temp_path) and os.path.getsize(temp_path) > 100000:
            return temp_path
        else:
            return None
    except Exception as ex:
        print(f"[M59-UPDATER] Download failed: {ex}", flush=True)
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        return None

def apply_update(new_exe_path):
    """
    Replaces the current running executable with the newly downloaded binary
    using PowerShell, handles binary lock release, and relaunches the updated app.
    """
    if not os.path.exists(new_exe_path):
        print(f"[M59-UPDATER] File not found: {new_exe_path}", flush=True)
        return False

    current_exe = sys.executable
    
    # If running from source python (e.g. 'python.exe m59_dashboard.py'), target dist exe
    if not current_exe.lower().endswith("m59companion.exe") and not current_exe.lower().endswith("m59companion_beta.exe"):
        dist_candidate = os.path.abspath("dist/M59Companion.exe")
        if os.path.exists(dist_candidate):
            current_exe = dist_candidate
        else:
            dist_beta = os.path.abspath("dist/M59Companion_beta.exe")
            if os.path.exists(dist_beta):
                current_exe = dist_beta
            else:
                current_exe = os.path.abspath("M59Companion.exe")

    current_exe = os.path.abspath(current_exe)
    new_exe_path = os.path.abspath(new_exe_path)
    base, ext = os.path.splitext(current_exe)
    backup_exe = base + "_backup" + ext

    print(f"[M59-UPDATER] Staging update swap: '{new_exe_path}' -> '{current_exe}'", flush=True)

    if sys.platform == 'win32':
        # PowerShell Multi-Step Safe Swap & Relaunch Script
        ps_script = f"""
        Start-Sleep -Seconds 3
        $current = "{current_exe}"
        $backup = "{backup_exe}"
        $new = "{new_exe_path}"

        if (Test-Path $new) {{
            if (Test-Path $backup) {{
                Remove-Item -Path $backup -Force -ErrorAction SilentlyContinue
            }}
            if (Test-Path $current) {{
                Rename-Item -Path $current -NewName [System.IO.Path]::GetFileName($backup) -Force -ErrorAction SilentlyContinue
            }}
            Move-Item -Path $new -Destination $current -Force

            # Start updated companion application
            Start-Process -FilePath $current

            [System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null
            [System.Windows.Forms.MessageBox]::Show('M59 Companion has been updated successfully and relaunched.', 'Update Complete', 0, 64)
        }}
        """
        try:
            subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            time.sleep(0.5)
            sys.exit(0)
        except Exception as ex:
            print(f"[M59-UPDATER] Failed to execute PowerShell update swap: {ex}", flush=True)
            return False
    else:
        # Non-Windows POSIX replacement
        try:
            import shutil
            shutil.move(new_exe_path, current_exe)
            print(f"[M59-UPDATER] Update applied to {current_exe}", flush=True)
            return True
        except Exception as ex:
            print(f"[M59-UPDATER] Non-windows swap failed: {ex}", flush=True)
            return False

def open_browser(url=None):
    """Opens release website or repository in default web browser."""
    target = url or REPO_URLS[0]["github_site"]
    webbrowser.open(target)

# ----------------------------------------------------------------------
# Tkinter Dual-Channel Update Dialog (For Original Dashboard Compatibility)
# ----------------------------------------------------------------------
def show_tk_update_dialog(parent, release_data, on_close=None):
    """
    Renders a Tkinter modal dialog with dual release selection (Stable vs Beta)
    for backward compatibility with the original Tkinter dashboard.
    """
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
    except ImportError:
        return

    top = tk.Toplevel(parent)
    top.title("M59 Companion - Software Update")
    top.geometry("520x420")
    top.configure(bg="#0b0f19")
    top.resizable(False, False)
    top.transient(parent)
    top.grab_set()

    # Title & Subtitle
    cur_v = release_data.get('current_version', 'Unknown')
    lbl_title = tk.Label(top, text="Software Updates Available", font=("Segoe UI", 14, "bold"), fg="#f8fafc", bg="#0b0f19")
    lbl_title.pack(anchor="w", padx=20, pady=(15, 2))
    lbl_cur = tk.Label(top, text=f"Current Installed Version: v{cur_v}", font=("Segoe UI", 10), fg="#60a5fa", bg="#0b0f19")
    lbl_cur.pack(anchor="w", padx=20, pady=(0, 10))

    # Channel Selection Frame
    choice_var = tk.StringVar()
    stable_v = release_data.get('stable_version', '1.8.0')
    beta_v = release_data.get('beta_version', '1.8.1-beta')
    stable_avail = release_data.get('stable_update_available', False)
    beta_avail = release_data.get('beta_update_available', False)

    if 'beta' in str(cur_v).lower() and beta_avail:
        choice_var.set("beta")
    elif stable_avail:
        choice_var.set("stable")
    else:
        choice_var.set("beta")

    frame_cards = tk.Frame(top, bg="#0b0f19")
    frame_cards.pack(fill="x", padx=20, pady=5)

    # Stable Radio
    f_s = tk.Frame(frame_cards, bg="#111827", highlightbackground="#1f2937", highlightthickness=1, padx=10, pady=8)
    f_s.pack(fill="x", pady=4)
    r_s = tk.Radiobutton(f_s, text=f"🌟 Stable Release — v{stable_v} {'(NEW)' if stable_avail else ''}",
                         variable=choice_var, value="stable", font=("Segoe UI", 10, "bold"),
                         fg="#34d399", bg="#111827", selectcolor="#065f46", activebackground="#111827", activeforeground="#34d399")
    r_s.pack(anchor="w")
    tk.Label(f_s, text="Recommended production build with maximum stability.", font=("Segoe UI", 9), fg="#94a3b8", bg="#111827").pack(anchor="w", padx=24)

    # Beta Radio
    f_b = tk.Frame(frame_cards, bg="#111827", highlightbackground="#1f2937", highlightthickness=1, padx=10, pady=8)
    f_b.pack(fill="x", pady=4)
    r_b = tk.Radiobutton(f_b, text=f"🧪 Beta Preview — v{beta_v} {'(NEW)' if beta_avail else ''}",
                         variable=choice_var, value="beta", font=("Segoe UI", 10, "bold"),
                         fg="#93c5fd", bg="#111827", selectcolor="#1e3a8a", activebackground="#111827", activeforeground="#93c5fd")
    r_b.pack(anchor="w")
    tk.Label(f_b, text="Experimental preview with upcoming features and fixes.", font=("Segoe UI", 9), fg="#94a3b8", bg="#111827").pack(anchor="w", padx=24)

    # Notes Display
    tk.Label(top, text="Release Details:", font=("Segoe UI", 9, "bold"), fg="#94a3b8", bg="#0b0f19").pack(anchor="w", padx=20, pady=(8, 2))
    notes_box = tk.Text(top, height=4, bg="#030712", fg="#cbd5e1", font=("Segoe UI", 9), wrap="word", relief="flat", highlightbackground="#1f2937", highlightthickness=1)
    notes_box.pack(fill="x", padx=20, pady=(0, 8))

    def update_notes():
        notes_box.config(state="normal")
        notes_box.delete("1.0", "end")
        if choice_var.get() == "stable":
            notes_box.insert("1.0", release_data.get('stable_notes') or "Stable release build.")
        else:
            notes_box.insert("1.0", release_data.get('beta_notes') or "Beta preview release build.")
        notes_box.config(state="disabled")

    choice_var.trace_add("write", lambda *args: update_notes())
    update_notes()

    # Progress & Status
    prog_lbl = tk.Label(top, text="", font=("Segoe UI", 9, "bold"), fg="#60a5fa", bg="#0b0f19")
    prog_lbl.pack(fill="x", padx=20, pady=2)
    prog_bar = ttk.Progressbar(top, orient="horizontal", mode="determinate", length=100)

    # Buttons
    btn_frame = tk.Frame(top, bg="#0b0f19")
    btn_frame.pack(fill="x", padx=20, pady=(6, 12), side="bottom")

    def start_tk_download():
        target_ch = choice_var.get()
        sel_ver = beta_v if target_ch == 'beta' else stable_v
        
        btn_update.config(state="disabled")
        btn_cancel.config(state="disabled")
        r_s.config(state="disabled")
        r_b.config(state="disabled")
        prog_bar.pack(fill="x", padx=20, pady=(0, 4), before=btn_frame)
        prog_lbl.config(text=f"Connecting to download {target_ch.upper()} v{sel_ver}...")

        def _worker():
            def _prog(dl, tot, pct):
                dl_mb = dl / (1024 * 1024)
                tot_mb = tot / (1024 * 1024)
                def _up():
                    prog_bar['value'] = pct
                    prog_lbl.config(text=f"Downloading: {dl_mb:.1f} MB / {tot_mb:.1f} MB ({int(pct)}%)")
                top.after(0, _up)

            try:
                new_file = download_update(target_ch, on_progress=_prog)
                if new_file and os.path.exists(new_file):
                    def _done():
                        prog_lbl.config(text="Download complete! Applying update...", fg="#34d399")
                        top.after(1000, lambda: [top.destroy(), apply_update(new_file)])
                    top.after(0, _done)
                else:
                    def _err():
                        prog_lbl.config(text="Error: Download failed.", fg="#ef4444")
                        btn_update.config(state="normal")
                        btn_cancel.config(state="normal")
                    top.after(0, _err)
            except Exception as e:
                def _err2():
                    prog_lbl.config(text=f"Error: {e}", fg="#ef4444")
                    btn_update.config(state="normal")
                    btn_cancel.config(state="normal")
                top.after(0, _err2)

        threading.Thread(target=_worker, daemon=True).start()

    def do_cancel():
        top.destroy()
        if on_close:
            on_close()

    btn_cancel = tk.Button(btn_frame, text="Remind Me Later", font=("Segoe UI", 9), bg="#1f2937", fg="#94a3b8",
                           activebackground="#374151", activeforeground="#ffffff", relief="flat", padx=12, pady=6, command=do_cancel)
    btn_cancel.pack(side="right", padx=(8, 0))

    btn_update = tk.Button(btn_frame, text="⬇️ Download & Update", font=("Segoe UI", 9, "bold"), bg="#2563eb", fg="#ffffff",
                           activebackground="#1d4ed8", activeforeground="#ffffff", relief="flat", padx=14, pady=6, command=start_tk_download)
    btn_update.pack(side="right")

    tk.Button(btn_frame, text="🌐 GitHub", font=("Segoe UI", 9), bg="#111827", fg="#94a3b8", relief="flat", padx=10, pady=6,
              command=lambda: open_browser()).pack(side="left")


# ----------------------------------------------------------------------
# PySide6 / PyQt Fluid Update Dialog
# ----------------------------------------------------------------------
def show_qt_update_dialog(parent, release_data, auto_install=False):
    """
    Renders a modern modal Qt dialog with dual release selection (Stable vs Beta).
    """
    try:
        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
            QFrame, QProgressBar, QTextEdit, QRadioButton, QButtonGroup,
            QApplication, QMessageBox
        )
        from PySide6.QtCore import Qt, QThread, Signal
    except ImportError:
        try:
            from PyQt6.QtWidgets import (
                QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                QFrame, QProgressBar, QTextEdit, QRadioButton, QButtonGroup,
                QApplication, QMessageBox
            )
            from PySide6.QtCore import Qt, QThread, pyqtSignal as Signal
        except ImportError:
            print("[M59-UPDATER] Qt libraries not available in current environment for dialog.", flush=True)
            return

    class DownloadWorker(QThread):
        progress_signal = Signal(int, str)
        finished_signal = Signal(str)
        error_signal = Signal(str)

        def __init__(self, target_type):
            super().__init__()
            self.target_type = target_type

        def run(self):
            def _prog(dl, total, pct):
                dl_mb = dl / (1024 * 1024)
                tot_mb = total / (1024 * 1024)
                self.progress_signal.emit(int(pct), f"Downloading: {dl_mb:.1f} MB / {tot_mb:.1f} MB ({int(pct)}%)")

            try:
                res_path = download_update(self.target_type, on_progress=_prog)
                if res_path and os.path.exists(res_path):
                    self.finished_signal.emit(res_path)
                else:
                    self.error_signal.emit("Download failed or received empty binary file.")
            except Exception as e:
                self.error_signal.emit(str(e))

    dialog = QDialog(parent)
    dialog.setWindowTitle("🚀 M59 Companion - Software Update Center")
    dialog.setMinimumWidth(560)
    dialog.setMinimumHeight(440)
    dialog.setStyleSheet("""
        QDialog {
            background-color: #0b0f19;
            color: #f1f5f9;
            font-family: 'Segoe UI', sans-serif;
        }
        QFrame.ReleaseCard {
            background-color: #111827;
            border: 1px solid #1f2937;
            border-radius: 8px;
            padding: 12px;
        }
        QFrame.ReleaseCard:hover {
            border: 1px solid #3b82f6;
        }
        QPushButton.UpdateBtn {
            background-color: #2563eb;
            color: #ffffff;
            font-weight: 700;
            font-size: 13px;
            padding: 10px 18px;
            border-radius: 6px;
            border: none;
        }
        QPushButton.UpdateBtn:hover {
            background-color: #1d4ed8;
        }
        QPushButton.CancelBtn {
            background-color: #1f2937;
            color: #94a3b8;
            font-weight: 600;
            font-size: 13px;
            padding: 10px 18px;
            border-radius: 6px;
            border: 1px solid #374151;
        }
        QPushButton.CancelBtn:hover {
            background-color: #374151;
            color: #ffffff;
        }
        QProgressBar {
            background-color: #1f2937;
            border: 1px solid #374151;
            border-radius: 6px;
            text-align: center;
            color: #ffffff;
            font-weight: bold;
            height: 20px;
        }
        QProgressBar::chunk {
            background-color: #3b82f6;
            border-radius: 5px;
        }
        QRadioButton {
            color: #f1f5f9;
            font-size: 13px;
            font-weight: 700;
            spacing: 8px;
        }
        QRadioButton::indicator {
            width: 16px;
            height: 16px;
        }
    """)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.setSpacing(14)

    # Title & Subtitle Header
    header_layout = QVBoxLayout()
    title_lbl = QLabel("Software Updates Available")
    title_lbl.setStyleSheet("font-size: 18px; font-weight: 800; color: #f8fafc;")
    cur_v = release_data.get('current_version', 'Unknown')
    sub_lbl = QLabel(f"Current Installed Version:  <span style='color: #60a5fa; font-weight: 700;'>v{cur_v}</span>")
    sub_lbl.setStyleSheet("font-size: 13px; color: #94a3b8;")
    header_layout.addWidget(title_lbl)
    header_layout.addWidget(sub_lbl)
    layout.addLayout(header_layout)

    # Release Channel Selection Box
    btn_group = QButtonGroup(dialog)

    # 1. Stable Card
    stable_card = QFrame()
    stable_card.setProperty("class", "ReleaseCard")
    sc_layout = QVBoxLayout(stable_card)
    sc_layout.setContentsMargins(12, 12, 12, 12)
    sc_layout.setSpacing(6)

    stable_v = release_data.get('stable_version', '1.8.0')
    stable_avail = release_data.get('stable_update_available', False)
    badge_s = "<span style='background-color: #065f46; color: #34d399; font-size: 11px; padding: 2px 6px; border-radius: 4px;'>NEW</span>" if stable_avail else "<span style='color: #64748b; font-size: 11px;'>(Current)</span>"

    radio_stable = QRadioButton(f"🌟 Stable Release Channel — v{stable_v}")
    sc_header = QHBoxLayout()
    sc_header.addWidget(radio_stable)
    sc_header.addWidget(QLabel(badge_s))
    sc_header.addStretch()
    sc_layout.addLayout(sc_header)

    s_desc = QLabel("Recommended for all players. Thoroughly tested production build with maximum stability.")
    s_desc.setStyleSheet("font-size: 11px; color: #94a3b8; margin-left: 24px;")
    s_desc.setWordWrap(True)
    sc_layout.addWidget(s_desc)
    btn_group.addButton(radio_stable, 1)
    layout.addWidget(stable_card)

    # 2. Beta Card
    beta_card = QFrame()
    beta_card.setProperty("class", "ReleaseCard")
    bc_layout = QVBoxLayout(beta_card)
    bc_layout.setContentsMargins(12, 12, 12, 12)
    bc_layout.setSpacing(6)

    beta_v = release_data.get('beta_version', '1.8.1-beta')
    beta_avail = release_data.get('beta_update_available', False)
    badge_b = "<span style='background-color: #1e3a8a; color: #93c5fd; font-size: 11px; padding: 2px 6px; border-radius: 4px;'>BETA</span>" if beta_avail else "<span style='color: #64748b; font-size: 11px;'>(Preview)</span>"

    radio_beta = QRadioButton(f"🧪 Beta Preview Channel — v{beta_v}")
    bc_header = QHBoxLayout()
    bc_header.addWidget(radio_beta)
    bc_header.addWidget(QLabel(badge_b))
    bc_header.addStretch()
    bc_layout.addLayout(bc_header)

    b_desc = QLabel("Contains cutting-edge features, new experimental UI overlays, and recent fixes before official release.")
    b_desc.setStyleSheet("font-size: 11px; color: #94a3b8; margin-left: 24px;")
    b_desc.setWordWrap(True)
    bc_layout.addWidget(b_desc)
    btn_group.addButton(radio_beta, 2)
    layout.addWidget(beta_card)

    # Pre-select based on update priority or user's current version
    if 'beta' in cur_v.lower() and beta_avail:
        radio_beta.setChecked(True)
    elif stable_avail:
        radio_stable.setChecked(True)
    else:
        radio_beta.setChecked(True)

    # Release Notes / Details Box
    notes_lbl = QLabel("Release Notes:")
    notes_lbl.setStyleSheet("font-size: 12px; font-weight: 700; color: #94a3b8; margin-top: 4px;")
    layout.addWidget(notes_lbl)

    notes_edit = QTextEdit()
    notes_edit.setReadOnly(True)
    notes_edit.setFixedHeight(70)
    notes_edit.setStyleSheet("""
        QTextEdit {
            background-color: #030712;
            border: 1px solid #1f2937;
            border-radius: 6px;
            color: #cbd5e1;
            font-size: 11px;
            padding: 6px;
        }
    """)
    
    def update_notes_display():
        if radio_stable.isChecked():
            notes_edit.setText(release_data.get('stable_notes') or "Stable channel build. Includes all verified updates.")
        else:
            notes_edit.setText(release_data.get('beta_notes') or "Beta channel preview. Includes latest features and fixes.")

    radio_stable.toggled.connect(update_notes_display)
    radio_beta.toggled.connect(update_notes_display)
    update_notes_display()
    layout.addWidget(notes_edit)

    # Progress Bar (Hidden by default)
    progress_bar = QProgressBar()
    progress_bar.setRange(0, 100)
    progress_bar.setValue(0)
    progress_bar.setVisible(False)
    layout.addWidget(progress_bar)

    status_lbl = QLabel("")
    status_lbl.setStyleSheet("font-size: 12px; font-weight: 600; color: #60a5fa;")
    status_lbl.setVisible(False)
    layout.addWidget(status_lbl)

    # Action Buttons
    action_box = QHBoxLayout()
    action_box.setSpacing(10)

    web_btn = QPushButton("🌐 View on GitHub")
    web_btn.setProperty("class", "CancelBtn")
    web_btn.clicked.connect(lambda: open_browser(release_data.get('github_site')))
    action_box.addWidget(web_btn)
    action_box.addStretch()

    cancel_btn = QPushButton("Remind Me Later")
    cancel_btn.setProperty("class", "CancelBtn")
    cancel_btn.clicked.connect(dialog.reject)
    action_box.addWidget(cancel_btn)

    install_btn = QPushButton("⬇️ Download & Update")
    install_btn.setProperty("class", "UpdateBtn")
    
    def start_download():
        target_channel = 'beta' if radio_beta.isChecked() else 'stable'
        selected_version = beta_v if target_channel == 'beta' else stable_v
        
        install_btn.setEnabled(False)
        cancel_btn.setEnabled(False)
        radio_stable.setEnabled(False)
        radio_beta.setEnabled(False)
        progress_bar.setVisible(True)
        status_lbl.setVisible(True)
        status_lbl.setText(f"Connecting to download {target_channel.upper()} v{selected_version}...")

        dialog.worker = DownloadWorker(target_channel)
        
        def on_prog(pct, msg):
            progress_bar.setValue(pct)
            status_lbl.setText(msg)

        def on_done(new_path):
            status_lbl.setText("Download complete! Applying update and restarting...")
            status_lbl.setStyleSheet("font-size: 12px; font-weight: 700; color: #34d399;")
            QApplication.processEvents()
            time.sleep(1)
            dialog.accept()
            apply_update(new_path)

        def on_err(err_msg):
            progress_bar.setVisible(False)
            status_lbl.setStyleSheet("font-size: 12px; font-weight: 700; color: #ef4444;")
            status_lbl.setText(f"Error: {err_msg}")
            install_btn.setEnabled(True)
            cancel_btn.setEnabled(True)
            radio_stable.setEnabled(True)
            radio_beta.setEnabled(True)

        dialog.worker.progress_signal.connect(on_prog)
        dialog.worker.finished_signal.connect(on_done)
        dialog.worker.error_signal.connect(on_err)
        dialog.worker.start()

    install_btn.clicked.connect(start_download)
    action_box.addWidget(install_btn)
    layout.addLayout(action_box)

    dialog.exec()

if __name__ == "__main__":
    print("Testing Meridian 59 Companion Dual Release Checker...")
    data = check_all_releases("1.8.0")
    print(f"Current: {data['current_version']}")
    print(f"Stable Remote: {data['stable_version']} (Update available: {data['stable_update_available']})")
    print(f"Beta Remote: {data['beta_version']} (Update available: {data['beta_update_available']})")
