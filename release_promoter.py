import os
import sys
import subprocess
import time
import re
import shutil

# Optional Module Exclusion Lists for Size Reduction (~60-70MB reduction)
DEFAULT_OPTIMIZED_EXCLUDES = [
    # Alternate/Fallback GUI Frameworks detected by PyInstaller static analysis
    "PyQt5",
    "PyQt5.QtCore",
    "PyQt5.QtGui",
    "PyQt5.QtWidgets",
    "PyQt6",
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "PySide2",
    # Heavyweight Qt subsystems not used by M59 Dashboard
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineProcess",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQuick3D",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DExtras",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtVirtualKeyboard",
    "PySide6.QtSpatialAudio",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtStateMachine",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtTest",
    "PySide6.QtSql",
    "PySide6.QtXml",
    "PySide6.QtWebSockets",
    "PySide6.QtWebChannel",
    # Unused third-party / heavy data packages if present in python environment
    "numpy",
    "scipy",
    "matplotlib",
    "pandas",
    # Unused standard submodules
    "unittest",
    "pydoc",
    "doctest",
    "test",
    "distutils",
    "setuptools"
]

def prompt_build_optimization():
    """
    Prompts the user during the build flow if they want to apply
    executable size optimization (DLL/module exclusions + UPX) or keep a full build.
    """
    print("\nBINARY PACKAGING & OPTIMIZATION:")
    print(" [1] ⚡ Slim / Optimized (~25-30 MB) - Strips unused Qt WebEngine/QML/3D C++ DLLs")
    print(" [2] 📦 Full Build (Unfiltered)      - Includes all default PySide6 C++ DLLs")
    
    choice = input("Select packaging mode [1]: ").strip()
    if choice == "2":
        print("-> Selected FULL build (no binary exclusions).")
        return False
    else:
        print("-> Selected SLIM / OPTIMIZED build (stripping heavy unused Qt C++ DLLs).")
        return True

def generate_spec_file(optimize=True):
    """
    Generates a custom PyInstaller .spec file that actively filters out
    heavy C++ DLLs (like Qt6WebEngineCore.dll, Qt6Qml.dll, Qt63D.dll) and data files
    from a.binaries and a.datas to keep the standalone .exe well under 100 MB.
    """
    asset_tuples = []
    for d in ('imgs', 'sound', 'settings', 'graphics', 'data'):
        if os.path.exists(d):
            asset_tuples.append((d, d))
    if os.path.exists("VERSION"):
        asset_tuples.append(("VERSION", "."))

    spec_content = f"""# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.building.build_main import Analysis, PYZ, EXE

block_cipher = None

# Heavy C++ DLLs and .pyd extensions to filter from binary payload
EXCLUDE_BINARIES = [
    'qtwebengine', 'qt6webengine',
    'qtqml', 'qt6qml',
    'qtquick', 'qt6quick',
    'qt3d', 'qt63d',
    'qtpdf', 'qt6pdf',
    'qtvirtualkeyboard', 'qt6virtualkeyboard',
    'qtspatialaudio', 'qt6spatialaudio',
    'qtmultimedia', 'qt6multimedia',
    'qtbluetooth', 'qt6bluetooth',
    'qtpositioning', 'qt6positioning',
    'qtsensors', 'qt6sensors',
    'qtserialport', 'qt6serialport',
    'qtremoteobjects', 'qt6remoteobjects',
    'qtscxml', 'qt6scxml',
    'qtstatemachine', 'qt6statemachine',
    'qtdesigner', 'qt6designer',
    'qthelp', 'qt6help',
    'qttest', 'qt6test',
    'qtsql', 'qt6sql',
    'qtxml', 'qt6xml',
    'qtwebsockets', 'qt6websockets',
    'qtwebchannel', 'qt6webchannel',
    'opengl32sw',
    'pyqt5', 'pyqt6', 'pyside2'
]

EXCLUDE_DATA = [
    'qtwebengine', 'icudtl.dat', 'qtuiotouch', 'qtvirtualkeyboard'
]

a = Analysis(
    ['m59_dashboard.py'],
    pathex=[],
    binaries=[],
    datas={asset_tuples},
    hiddenimports=['PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets', 'tkinter', '_tkinter', 'tkinter.ttk', 'tkinter.messagebox'],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes={DEFAULT_OPTIMIZED_EXCLUDES if optimize else []},
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

if {optimize}:
    # Filter out heavy C++ DLLs and .pyd extensions from a.binaries
    filtered_binaries = []
    for item in a.binaries:
        dest_name = item[0].lower().replace('\\\\', '/')
        src_name = item[1].lower().replace('\\\\', '/')
        if any(ex in dest_name or ex in src_name for ex in EXCLUDE_BINARIES):
            continue
        filtered_binaries.append(item)
    a.binaries = filtered_binaries

    # Filter out heavy data files from a.datas
    filtered_datas = []
    for item in a.datas:
        dest_name = item[0].lower().replace('\\\\', '/')
        src_name = item[1].lower().replace('\\\\', '/')
        if any(pat in dest_name or pat in src_name for pat in EXCLUDE_DATA):
            continue
        filtered_datas.append(item)
    a.datas = filtered_datas

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='M59Companion',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='imgs/m59comp.ico',
    version='version_info.txt'
)
"""
    with open("M59Companion.spec", "w", encoding="utf-8") as f:
        f.write(spec_content)
    print("-> Generated M59Companion.spec with C++ binary/DLL filtering")

def run_command(cmd, description, capture=False):
    if description:
        print(f"\n>>> {description}...")
    try:
        if capture:
            result = subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
            return result.stdout.strip()
        else:
            subprocess.run(cmd, shell=True, check=True)
            return True
    except subprocess.CalledProcessError as e:
        if description:
            print(f"ERROR: Command failed with exit code {e.returncode}")
        return False

def get_current_version():
    if not os.path.exists("VERSION"):
        return "3.1.0"
    with open("VERSION", "r", encoding="utf-8") as f:
        try:
            content = f.read().strip()
            v_str = content.splitlines()[0].lower().replace('v', '')
            return v_str if v_str else "3.1.0"
        except:
            return "3.1.0"

def get_next_version():
    v_str = get_current_version()
    parts = v_str.split('.')
    if len(parts) >= 1:
        # Increment the last segment (e.g. 3.1.0 -> 3.1.1)
        try:
            parts[-1] = str(int(parts[-1]) + 1)
            return ".".join(parts)
        except:
            return v_str + ".1"
    return "3.1.0"

def get_github_repo_info():
    """Detects GitHub owner/repo from local git remote if available, with fallback."""
    raw = run_command("git config --get remote.origin.url", None, capture=True)
    if raw:
        match = re.search(r"github\.com[:/]([\w\-]+)/([\w\-]+?)(?:\.git)?$", str(raw).strip())
        if match:
            return f"{match.group(1)}/{match.group(2)}"
    return "subvhome/m59-companion-v3"

def update_readme_links(stable_version=None):
    """Dynamically updates or injects the direct download link into README.md."""
    if not os.path.exists("README.md"):
        return
    try:
        repo_slug = get_github_repo_info()
        with open("README.md", "r", encoding="utf-8") as f:
            content = f.read()

        stable_url = f"https://github.com/{repo_slug}/raw/main/dist/M59Companion.exe"
        s_label = f" (v{stable_version})" if stable_version else ""
        latest_line = f"- **🚀 Latest Release**: [Download M59Companion.exe{s_label}]({stable_url})"

        if re.search(r"-\s*\*\*.*Latest.*?\*\*:\s*\[.*\]\(.*M59Companion\.exe.*\)", content, re.IGNORECASE):
            content = re.sub(
                r"-\s*\*\*.*Latest.*?\*\*:\s*\[.*\]\(.*M59Companion\.exe.*\)",
                latest_line,
                content,
                flags=re.IGNORECASE
            )
        elif re.search(r"-\s*\*\*.*Stable.*?\*\*:\s*\[.*\]\(.*M59Companion\.exe.*\)", content, re.IGNORECASE):
            content = re.sub(
                r"-\s*\*\*.*Stable.*?\*\*:\s*\[.*\]\(.*M59Companion\.exe.*\)",
                latest_line,
                content,
                flags=re.IGNORECASE
            )
        else:
            content += f"\n\n### 📥 Direct Download\n{latest_line}\n"

        with open("README.md", "w", encoding="utf-8") as f:
            f.write(content)
        print(f"-> Updated README.md download links for repository: {repo_slug}")
    except Exception as ex:
        print(f"!! Warning: Could not update README.md automatically: {ex}")

def show_restore_menu():
    print("\n--- RESTORE MODULE ---")
    run_command("git fetch --tags", "Fetching latest tags from remote")
    
    tags_raw = run_command("git tag -l", "Fetching local tags", capture=True)
    if not tags_raw:
        print("No tags found in repository.")
        return False

    tags = tags_raw.split('\n')
    print("\nAvailable Versions to Restore:")
    for i, tag in enumerate(tags):
        print(f" [{i+1}] {tag}")
    
    choice = input("\nSelect tag number to restore (or 'q' to cancel): ").strip()
    if choice.lower() == 'q':
        return False
    
    try:
        idx = int(choice) - 1
        target_tag = tags[idx]
        
        confirm = input(f"Confirm RESTORE to {target_tag}? This will overwrite local changes! (yes/no): ").lower()
        if confirm == 'yes':
            # Check if EXE is locked first
            if os.path.exists("dist/M59Companion.exe"):
                try:
                    with open("dist/M59Companion.exe", "a"): pass
                except IOError:
                    print("!! ERROR: 'dist/M59Companion.exe' is locked (likely running).")
                    print("!! Please close the app and try again.")
                    return False

            # 1. Checkout the tag's files
            if not run_command(f"git checkout {target_tag} -- .", f"Restoring files from {target_tag}"):
                return False
            
            # 2. Show what changed
            print("\nFiles Reverted:")
            run_command("git status --short", None)
            
            # 3. Update the VERSION file to match the tag
            version_num = target_tag.lower().replace('v', '')
            with open("VERSION", "w") as f:
                f.write(version_num)
            
            print(f"\n-> SUCCESS: Workspace restored to {target_tag}")
            return True
    except (ValueError, IndexError):
        print("Invalid selection.")
    
    return False

def show_delete_tag_menu():
    print("\n--- DELETE TAG MODULE ---")
    print("Fetching remote tags...")
    tags_raw = run_command("git ls-remote --tags origin", "Fetching remote tags", capture=True)
    
    tags = []
    if tags_raw:
        for line in tags_raw.splitlines():
            parts = line.split("refs/tags/")
            if len(parts) > 1:
                tag = parts[-1].replace('^{}', '')
                if tag not in tags:
                    tags.append(tag)
                    
    if not tags:
        print("No tags found on remote repository.")
        return False

    print("\nAvailable Tags to Delete:")
    for i, tag in enumerate(tags):
        print(f" [{i+1}] {tag}")
    
    choice = input("\nSelect tag number to DELETE (or 'q' to cancel): ").strip()
    if choice.lower() == 'q':
        return False
    
    try:
        idx = int(choice) - 1
        target_tag = tags[idx]
        
        confirm = input(f"Confirm DELETE of tag '{target_tag}'? This will remove it from GitHub and locally! (yes/no): ").lower()
        if confirm == 'yes':
            run_command(f'git tag -d "{target_tag}" 2>nul || true', "Deleting local tag (if exists)")
            
            if run_command(f'git push origin --delete "{target_tag}"', f"Deleting remote tag {target_tag}"):
                print(f"\n-> SUCCESS: Tag {target_tag} has been deleted from GitHub.")
                return True
            else:
                print(f"\n-> ERROR: Failed to delete remote tag {target_tag}.")
    except (ValueError, IndexError):
        print("Invalid selection.")
    
    return False

def start_pipeline():
    while True:
        print("\n==========================================")
        print("      M59 COMPANION: RELEASE PROMOTER    ")
        print("==========================================")
        print(" [1] Promote New Release (Build & Tag)")
        print(" [2] Quick Sync (Code Only - No Build)")
        print(" [3] Restore Working Version (From Tag)")
        print(" [4] Delete a Tag")
        print(" [Q] Exit")
        
        main_choice = input("\nSelect action: ").strip().upper()
        
        if main_choice == '1':
            run_promotion()
            break
        elif main_choice == '2':
            run_quick_sync()
            break
        elif main_choice == '3':
            if show_restore_menu():
                break
        elif main_choice == '4':
            if show_delete_tag_menu():
                break
        elif main_choice == 'Q':
            break

def perform_git_sync(do_full_sync, commit_msg, version=None, tag_msg=None, extra_files_to_force_add=None):
    """Core Git logic shared by Release and Quick Sync."""
    if extra_files_to_force_add is None:
        extra_files_to_force_add = []
        if os.path.exists("dist/M59Companion.exe"):
            extra_files_to_force_add.append("dist/M59Companion.exe")

    # 1. Sync Logic
    if do_full_sync:
        print("-> ALERT: Performing Full Sync (Mirroring local to remote)")
        run_command("git rm -r --cached .", "Refreshing index to respect .gitignore")
        if not run_command("git add -A :/", "Staging ALL changes"): return False
        for fpath in extra_files_to_force_add:
            if os.path.exists(fpath):
                run_command(f'git add -f "{fpath}"', f"Force-staging {fpath}")
    else:
        run_command("git add .", "Staging project files")
        for fpath in extra_files_to_force_add:
            if os.path.exists(fpath):
                run_command(f'git add -f "{fpath}"', f"Force-staging {fpath}")

    if not run_command(f'git commit -m "{commit_msg}"', "Committing changes"): return False
    
    # 2. Optional Tagging
    if version:
        tag_name = f"v{version}"
        print(f"\n>>> Checking historical tags for {tag_name}...")
        
        subprocess.run(f'git tag -d "{tag_name}" 2>nul', shell=True)
        subprocess.run(f'git push origin :refs/tags/"{tag_name}" 2>nul', shell=True)
        
        if not run_command(f'git tag -a "{tag_name}" -m "{tag_msg}"', f"Creating Release Tag {tag_name}"): 
            return False

    # 3. Pushing
    print("\nFinal Step: Pushing to Remote...")
    push_cmd = "git push origin main --force" if do_full_sync else "git push origin main"
    if not run_command(push_cmd, "Pushing Code"):
        print("\n!! Standard push was rejected because remote has changes or tags not present locally.")
        print("-> Attempting 'git pull --rebase origin main' to merge latest remote state...")
        if run_command("git pull --rebase origin main", "Rebasing onto remote main"):
            print("-> Rebase successful. Retrying push...")
            if not run_command("git push origin main", "Pushing Code"):
                print("-> Retrying with forced push to align remote...")
                if not run_command("git push origin main --force", "Force Pushing Code"):
                    return False
        else:
            print("-> Rebase encountered conflicts. Aborting rebase and force-pushing release state...")
            subprocess.run("git rebase --abort 2>nul", shell=True)
            if not run_command("git push origin main --force", "Force Pushing Code"):
                return False

    if version:
        tag_name = f"v{version}"
        if not run_command(f'git push origin "{tag_name}" --force', f"Pushing Release Tag {tag_name}"): return False
        
    return True

def run_quick_sync():
    print("\n--- QUICK SYNC (CODE ONLY) ---")
    print("SYNC MODE:")
    print(" [1] Standard (Update changed files only)")
    print(" [2] Full Mirror (Respect .gitignore strictly)")
    sync_choice = input("Select mode [1]: ").strip()
    do_full_sync = (sync_choice == "2")

    commit_msg = input("Enter sync message [Quick Sync]: ").strip()
    if not commit_msg: commit_msg = "Quick Sync"

    if perform_git_sync(do_full_sync, commit_msg):
        print("\n==========================================")
        print("      SUCCESS: Code Synced to GitHub!     ")
        print("==========================================")

def get_git_tracked_assets():
    """Dynamically identifies files to bundle in the EXE based on what Git is tracking and local files."""
    asset_exts = {'.json', '.csv', '.wav', '.txt', '.md'}
    asset_dirs = {'imgs', 'sound', 'graphics', 'data'}
    
    cmd = "git ls-files"
    raw = run_command(cmd, "Detecting tracked assets", capture=True)
    
    bundled = []
    seen_dirs = set()
    added_files = set()
    
    if raw:
        for line in raw.splitlines():
            line = line.strip()
            if not line: continue
            
            found_dir = False
            for d in asset_dirs:
                if line.startswith(d + "/"):
                    if d not in seen_dirs and os.path.exists(d):
                        bundled.append(f'--add-data "{d};{d}"')
                        seen_dirs.add(d)
                    found_dir = True
                    break
            if found_dir: continue
            
            if line == "VERSION" or any(line.endswith(ext) for ext in asset_exts):
                if os.path.exists(line):
                    added_files.add(line)
                    if "/" in line:
                        dest_folder = os.path.dirname(line)
                        bundled.append(f'--add-data "{line};{dest_folder}"')
                    else:
                        bundled.append(f'--add-data "{line};."')

    # ALWAYS ensure crucial folders and version file are bundled into the binary:
    for d in ('imgs', 'sound', 'settings', 'graphics', 'data'):
        if d not in seen_dirs and os.path.exists(d):
            bundled.append(f'--add-data "{d};{d}"')
            seen_dirs.add(d)

    if "VERSION" not in added_files and os.path.exists("VERSION"):
        bundled.append('--add-data "VERSION;."')
        added_files.add("VERSION")
                
    return bundled

def generate_version_info(version_str):
    """Generates a PyInstaller version info file to help prevent antivirus false positives."""
    parts = []
    for p in version_str.replace('v', '').split('-')[0].split('.'):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    while len(parts) < 4:
        parts.append(0)
    
    version_tuple = tuple(parts[:4])
    version_str_clean = ".".join(map(str, parts[:4]))
    desc_str = "M59 Companion Application"
    file_name_str = "M59Companion.exe"
    
    content = f"""
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={version_tuple},
    prodvers={version_tuple},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
    ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        u'040904B0',
        [StringStruct(u'CompanyName', u'M59 Companion Project'),
        StringStruct(u'FileDescription', u'{desc_str}'),
        StringStruct(u'FileVersion', u'{version_str_clean}'),
        StringStruct(u'InternalName', u'M59Companion'),
        StringStruct(u'LegalCopyright', u'Copyright (c) 2026 M59 Companion'),
        StringStruct(u'OriginalFilename', u'{file_name_str}'),
        StringStruct(u'ProductName', u'M59 Companion'),
        StringStruct(u'ProductVersion', u'{version_str_clean}')])
      ]), 
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"""
    with open("version_info.txt", "w", encoding="utf-8") as f:
        f.write(content.strip())
    print("-> Generated version_info.txt for PyInstaller metadata")

def check_file_size_limit(file_path, limit_mb=90.0):
    """Verifies generated binary is within GitHub's standard upload ceiling."""
    if os.path.exists(file_path):
        size_bytes = os.path.getsize(file_path)
        size_mb = size_bytes / (1024 * 1024)
        print(f"-> Executable size: {size_mb:.2f} MB")
        if size_mb >= 100.0:
            print(f"\n!! CRITICAL ERROR: {file_path} is {size_mb:.2f} MB, which EXCEEDS GitHub's 100.00 MB hard limit!")
            print("!! GitHub will reject pushing files over 100 MB.")
            print("!! Solution: Re-run release_promoter.py and select option [1] (Slim / Optimized build).")
            return False
        elif size_mb > limit_mb:
            print(f"!! WARNING: {file_path} is {size_mb:.2f} MB (nearing GitHub's 100MB limit).")
            return True
        else:
            print(f"-> Size check PASSED ({size_mb:.2f} MB / {limit_mb:.2f} MB target limit)")
            return True
    return False

def run_promotion():
    # 1. Version Selection
    default_v = get_next_version()
    user_v = input(f"Enter target version [{default_v}]: ").strip()
    version = user_v if user_v else default_v
    
    update_message = input("Enter release notes/update message for users (or press enter to skip): ").strip()
    
    with open("VERSION", "w", encoding="utf-8") as f:
        f.write(version)
        if update_message:
            f.write(f"\n{update_message}")
    print(f"-> VERSION file updated to v{version}")

    # 2. Sync Selection
    print("\nSYNC MODE:")
    print(" [1] Standard (Update changed files only)")
    print(" [2] Full Mirror (Replace EVERYTHING on GitHub with local state)")
    sync_choice = input("Select mode [1]: ").strip()
    do_full_sync = (sync_choice == "2")

    # 3. Compilation & Packaging Optimization
    should_optimize = prompt_build_optimization()
    print("\n-> Compiling Master Dashboard (M59Companion.exe)...")
    
    generate_version_info(version)
    generate_spec_file(optimize=should_optimize)
    
    # Wipe old build cache to prevent stale module exclusions
    shutil.rmtree("build", ignore_errors=True)
    
    compile_cmd = 'python -m PyInstaller --clean M59Companion.spec'
    
    if not run_command(compile_cmd, "Building Standalone Binary"):
        print("!! COMPILATION FAILED. Pipeline aborted.")
        return

    if not check_file_size_limit("dist/M59Companion.exe", limit_mb=90.0):
        print("!! PIPELINE ABORTED: Executable size exceeds GitHub's limit.")
        return

    # 4. Update README.md with Download Link
    update_readme_links(stable_version=version)

    # 5. Verification Pause
    print("\n" + "!"*40)
    print(f" PIPELINE PAUSED: Please test 'dist/M59Companion.exe'")
    print(f" Verify title bar shows v{version}")
    print("!"*40)

    confirm = input("\nReady to push to GitHub and TAG this release? (yes/no): ").lower()
    if confirm != "yes":
        print(f"Pipeline aborted. Version remains at {version} but nothing pushed.")
        return

    commit_msg = input(f"Enter commit message [Release v{version}]: ").strip()
    if not commit_msg:
        commit_msg = f"Release v{version}"

    # 6. Git Operations & Pushing
    tag_msg = f"Version {version}"
    if update_message: tag_msg += f"\n\nRelease Notes:\n{update_message}"

    if perform_git_sync(do_full_sync, commit_msg, version=version, tag_msg=tag_msg, extra_files_to_force_add=["dist/M59Companion.exe"]):
        print("\n==========================================")
        print(f"   SUCCESS: Version {version} is LIVE!   ")
        print(f"   Tag v{version} created for easy revert.  ")
        print("==========================================")

if __name__ == "__main__":
    if not os.path.exists("m59_dashboard.py"):
        print("ERROR: 'm59_dashboard.py' not found. Run this from the project root directory.")
    else:
        try:
            start_pipeline()
        except KeyboardInterrupt:
            print("\n\n!! Execution cancelled by user. Exiting.")
            sys.exit(0)
