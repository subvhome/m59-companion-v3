import os
import sys
import time
import re
import urllib.request
import subprocess
import webbrowser
import threading

# ----------------------------------------------------------------------
# Primary and Fallback Release Repositories
# ----------------------------------------------------------------------
REPO_URLS = [
    {
        "version_url": "https://raw.githubusercontent.com/subvhome/m59-companion-v3/main/VERSION",
        "readme_url": "https://raw.githubusercontent.com/subvhome/m59-companion-v3/main/README.md",
        "exe_url": "https://github.com/subvhome/m59-companion-v3/raw/main/dist/M59Companion.exe",
        "github_site": "https://github.com/subvhome/m59-companion-v3"
    },
    {
        "version_url": "https://raw.githubusercontent.com/Substance-V/m59companion-v3/main/VERSION",
        "readme_url": "https://raw.githubusercontent.com/Substance-V/m59companion-v3/main/README.md",
        "exe_url": "https://github.com/Substance-V/m59companion-v3/raw/main/dist/M59Companion.exe",
        "github_site": "https://github.com/Substance-V/m59companion-v3"
    }
]

def parse_version_tuple(v_str):
    """
    Parses versions like '3.0.0', 'v3.1.0', '3.1.1' into comparable tuples:
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
        # If numerical parts are identical: Stable release is newer than pre-release/beta
        if not c_beta and r_beta:
            return True
    return False

def get_installed_version():
    """
    Reads the active version strictly from the VERSION file,
    checking PyInstaller bundle (_MEIPASS), executable directory, and CWD.
    """
    search_dirs = []
    
    # 1. Check PyInstaller bundled location
    if hasattr(sys, '_MEIPASS'):
        search_dirs.append(sys._MEIPASS)
        
    # 2. Check executable directory
    try:
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        if exe_dir and exe_dir not in search_dirs:
            search_dirs.append(exe_dir)
    except Exception:
        pass

    # 3. Check current working directory
    try:
        cwd = os.path.abspath(".")
        if cwd and cwd not in search_dirs:
            search_dirs.append(cwd)
    except Exception:
        pass

    # Check for VERSION across candidate paths
    for base in search_dirs:
        ver_path = os.path.join(base, "VERSION")
        if os.path.exists(ver_path):
            try:
                with open(ver_path, "r", encoding="utf-8") as f:
                    lines = f.read().strip().splitlines()
                    if lines and lines[0].strip():
                        return lines[0].strip().replace('v', '')
            except Exception:
                pass

    return "3.1.0"

def fetch_url_text(url, timeout=5):
    """Safely retrieves UTF-8 decoded text from a URL with cache-busting timestamp."""
    try:
        busting_url = f"{url}?t={int(time.time())}"
        req = urllib.request.Request(
            busting_url,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) M59CompanionUpdater/3.0'}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode('utf-8', errors='replace').strip()
    except Exception:
        return None

def check_all_releases(current_version=None):
    """
    Fetches latest release metadata from remote repository VERSION file.
    Returns a dictionary with version comparison data.
    """
    if current_version is None:
        current_version = get_installed_version()

    result = {
        "current_version": current_version,
        "latest_version": None,
        "release_notes": "",
        "exe_url": REPO_URLS[0]["exe_url"],
        "update_available": False,
        "github_site": REPO_URLS[0]["github_site"],
        "error": None,
        # Backward compatibility aliases
        "stable_version": None,
        "stable_notes": "",
        "stable_exe_url": REPO_URLS[0]["exe_url"],
        "stable_update_available": False,
        "beta_version": None,
        "beta_notes": "",
        "beta_exe_url": REPO_URLS[0]["exe_url"],
        "beta_update_available": False,
    }

    fetched = False
    for repo in REPO_URLS:
        version_txt = fetch_url_text(repo["version_url"])
        if version_txt:
            v_lines = version_txt.splitlines()
            remote_v = v_lines[0].strip().replace('v', '') if v_lines else "3.1.0"
            release_notes = "\n".join(v_lines[1:]).strip() if len(v_lines) > 1 else ""

            is_newer = is_version_newer(remote_v, current_version)

            result["latest_version"] = remote_v
            result["release_notes"] = release_notes or "Latest verified release for Meridian 59 Companion."
            result["exe_url"] = repo["exe_url"]
            result["update_available"] = is_newer
            result["github_site"] = repo["github_site"]

            # Aliases for backward compatibility
            result["stable_version"] = remote_v
            result["stable_notes"] = result["release_notes"]
            result["stable_exe_url"] = repo["exe_url"]
            result["stable_update_available"] = is_newer
            result["beta_version"] = remote_v
            result["beta_notes"] = result["release_notes"]
            result["beta_exe_url"] = repo["exe_url"]
            result["beta_update_available"] = False
            
            fetched = True
            break

    if not fetched:
        result["error"] = "Unable to connect to GitHub update servers."

    return result

def check_for_updates(current_version=None):
    """Legacy wrapper returning (update_available, remote_version, release_notes)"""
    res = check_all_releases(current_version)
    return res.get("update_available", False), res.get("latest_version"), res.get("release_notes")

def download_update(target_type=None, on_progress=None):
    """
    Downloads M59Companion.exe from the release repo with optional progress callback:
    on_progress(downloaded_bytes, total_bytes, percentage_float)
    """
    releases = check_all_releases()
    url = releases.get("exe_url") or REPO_URLS[0]["exe_url"]

    temp_path = "M59Companion_update_temp.exe"
    
    # Try primary then fallback
    urls_to_try = [url]
    for r in REPO_URLS:
        if r["exe_url"] not in urls_to_try:
            urls_to_try.append(r["exe_url"])

    for candidate_url in urls_to_try:
        try:
            print(f"[M59-UPDATER] Downloading update binary from: {candidate_url}", flush=True)
            req = urllib.request.Request(
                candidate_url,
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) M59CompanionUpdater/3.0'}
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
        except Exception as ex:
            print(f"[M59-UPDATER] Download failed from {candidate_url}: {ex}", flush=True)
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
    if not current_exe.lower().endswith("m59companion.exe"):
        dist_candidate = os.path.abspath("dist/M59Companion.exe")
        if os.path.exists(dist_candidate):
            current_exe = dist_candidate
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
# Tkinter Update Dialog (Backward Compatibility)
# ----------------------------------------------------------------------
def show_tk_update_dialog(parent, release_data, on_close=None):
    """
    Renders a Tkinter modal dialog for software updates.
    """
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
    except ImportError:
        return

    cur_v = release_data.get('current_version', 'Unknown')
    latest_v = release_data.get('latest_version') or release_data.get('stable_version', '3.1.0')
    has_update = release_data.get('update_available', False)

    if not has_update:
        messagebox.showinfo("M59 Companion Update", f"You're already running the latest version (v{cur_v})!")
        return

    top = tk.Toplevel(parent)
    top.title("M59 Companion - Software Update")
    top.geometry("500x360")
    top.configure(bg="#0b0f19")
    top.resizable(False, False)
    top.transient(parent)
    top.grab_set()

    lbl_title = tk.Label(top, text="Software Update Available", font=("Segoe UI", 14, "bold"), fg="#f8fafc", bg="#0b0f19")
    lbl_title.pack(anchor="w", padx=20, pady=(15, 2))
    
    lbl_cur = tk.Label(top, text=f"Current: v{cur_v}   ➜   New Version: v{latest_v}", font=("Segoe UI", 11, "bold"), fg="#34d399", bg="#0b0f19")
    lbl_cur.pack(anchor="w", padx=20, pady=(0, 10))

    # Notes Display
    tk.Label(top, text="Release Details:", font=("Segoe UI", 9, "bold"), fg="#94a3b8", bg="#0b0f19").pack(anchor="w", padx=20, pady=(4, 2))
    notes_box = tk.Text(top, height=5, bg="#030712", fg="#cbd5e1", font=("Segoe UI", 9), wrap="word", relief="flat", highlightbackground="#1f2937", highlightthickness=1)
    notes_box.pack(fill="x", padx=20, pady=(0, 8))
    notes_box.insert("1.0", release_data.get('release_notes') or "Latest release build.")
    notes_box.config(state="disabled")

    # Progress & Status
    prog_lbl = tk.Label(top, text="", font=("Segoe UI", 9, "bold"), fg="#60a5fa", bg="#0b0f19")
    prog_lbl.pack(fill="x", padx=20, pady=2)
    prog_bar = ttk.Progressbar(top, orient="horizontal", mode="determinate", length=100)

    # Buttons
    btn_frame = tk.Frame(top, bg="#0b0f19")
    btn_frame.pack(fill="x", padx=20, pady=(6, 12), side="bottom")

    def start_tk_download():
        btn_update.config(state="disabled")
        btn_cancel.config(state="disabled")
        prog_bar.pack(fill="x", padx=20, pady=(0, 4), before=btn_frame)
        prog_lbl.config(text=f"Connecting to download M59Companion.exe v{latest_v}...")

        def _worker():
            def _prog(dl, tot, pct):
                dl_mb = dl / (1024 * 1024)
                tot_mb = tot / (1024 * 1024)
                def _up():
                    prog_bar['value'] = pct
                    prog_lbl.config(text=f"Downloading: {dl_mb:.1f} MB / {tot_mb:.1f} MB ({int(pct)}%)")
                top.after(0, _up)

            try:
                new_file = download_update(on_progress=_prog)
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
              command=lambda: open_browser(release_data.get('github_site'))).pack(side="left")

# ----------------------------------------------------------------------
# PySide6 / PyQt Fluid Update Dialog
# ----------------------------------------------------------------------
def show_qt_update_dialog(parent, release_data, auto_install=False):
    """
    Renders a modern modal Qt dialog for software updates.
    """
    try:
        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
            QFrame, QProgressBar, QTextEdit, QApplication, QMessageBox
        )
        from PySide6.QtCore import Qt, QThread, Signal
    except ImportError:
        try:
            from PyQt6.QtWidgets import (
                QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                QFrame, QProgressBar, QTextEdit, QApplication, QMessageBox
            )
            from PyQt6.QtCore import Qt, QThread, pyqtSignal as Signal
        except ImportError:
            print("[M59-UPDATER] Qt libraries not available in current environment for dialog.", flush=True)
            return

    cur_v = release_data.get('current_version', 'Unknown')
    latest_v = release_data.get('latest_version') or release_data.get('stable_version', '3.1.0')
    has_update = release_data.get('update_available', False)

    # If invoked manually and already up to date, show an info box
    if not has_update:
        QMessageBox.information(
            parent,
            "Software Update",
            f"🎉 You are running the latest version of Meridian 59 Companion!\n\nInstalled Version: v{cur_v}"
        )
        return

    class DownloadWorker(QThread):
        progress_signal = Signal(int, str)
        finished_signal = Signal(str)
        error_signal = Signal(str)

        def run(self):
            def _prog(dl, total, pct):
                dl_mb = dl / (1024 * 1024)
                tot_mb = total / (1024 * 1024)
                self.progress_signal.emit(int(pct), f"Downloading: {dl_mb:.1f} MB / {tot_mb:.1f} MB ({int(pct)}%)")

            try:
                res_path = download_update(on_progress=_prog)
                if res_path and os.path.exists(res_path):
                    self.finished_signal.emit(res_path)
                else:
                    self.error_signal.emit("Download failed or received empty binary file.")
            except Exception as e:
                self.error_signal.emit(str(e))

    dialog = QDialog(parent)
    dialog.setWindowTitle("🚀 M59 Companion - Software Update")
    dialog.setMinimumWidth(520)
    dialog.setMinimumHeight(380)
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
            padding: 14px;
        }
        QPushButton.UpdateBtn {
            background-color: #2563eb;
            color: #ffffff;
            font-weight: 700;
            font-size: 13px;
            padding: 10px 20px;
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
            height: 22px;
        }
        QProgressBar::chunk {
            background-color: #3b82f6;
            border-radius: 5px;
        }
    """)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(22, 22, 22, 22)
    layout.setSpacing(14)

    # Title & Subtitle Header
    header_layout = QVBoxLayout()
    title_lbl = QLabel("✨ New Update Available")
    title_lbl.setStyleSheet("font-size: 19px; font-weight: 800; color: #f8fafc;")
    
    sub_lbl = QLabel(f"Current Version: <span style='color: #94a3b8;'>v{cur_v}</span> &nbsp;&nbsp;➜&nbsp;&nbsp; Latest Version: <span style='color: #34d399; font-weight: 800;'>v{latest_v}</span>")
    sub_lbl.setStyleSheet("font-size: 13px; color: #f1f5f9;")
    header_layout.addWidget(title_lbl)
    header_layout.addWidget(sub_lbl)
    layout.addLayout(header_layout)

    # Release Card
    rel_card = QFrame()
    rel_card.setProperty("class", "ReleaseCard")
    rc_layout = QVBoxLayout(rel_card)
    rc_layout.setContentsMargins(12, 12, 12, 12)
    rc_layout.setSpacing(8)

    card_hdr = QLabel("Release Notes:")
    card_hdr.setStyleSheet("font-size: 12px; font-weight: 700; color: #94a3b8;")
    rc_layout.addWidget(card_hdr)

    notes_edit = QTextEdit()
    notes_edit.setReadOnly(True)
    notes_edit.setFixedHeight(90)
    notes_edit.setStyleSheet("""
        QTextEdit {
            background-color: #030712;
            border: 1px solid #1f2937;
            border-radius: 6px;
            color: #cbd5e1;
            font-size: 12px;
            padding: 8px;
        }
    """)
    notes_edit.setText(release_data.get('release_notes') or "Latest release build for Meridian 59 Companion.")
    rc_layout.addWidget(notes_edit)
    layout.addWidget(rel_card)

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

    web_btn = QPushButton("🌐 GitHub")
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
        install_btn.setEnabled(False)
        cancel_btn.setEnabled(False)
        progress_bar.setVisible(True)
        status_lbl.setVisible(True)
        status_lbl.setText(f"Connecting to download M59Companion.exe v{latest_v}...")

        dialog.worker = DownloadWorker()
        
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

        dialog.worker.progress_signal.connect(on_prog)
        dialog.worker.finished_signal.connect(on_done)
        dialog.worker.error_signal.connect(on_err)
        dialog.worker.start()

    install_btn.clicked.connect(start_download)
    action_box.addWidget(install_btn)
    layout.addLayout(action_box)

    dialog.exec()

if __name__ == "__main__":
    print("Testing Meridian 59 Companion Release Checker...")
    data = check_all_releases("3.0.0")
    print(f"Current: {data['current_version']}")
    print(f"Latest Remote: {data['latest_version']} (Update available: {data['update_available']})")
    print(f"Download URL: {data['exe_url']}")
