import os
import sys
import subprocess
import time
import re

# Optional Module Exclusion Lists for Size Reduction (~35MB reduction)
DEFAULT_OPTIMIZED_EXCLUDES = [
    # Heavyweight Qt subsystems not used by M59 Dashboard
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQuick3D",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
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
    # Unused standard submodules
    "tkinter",
    "unittest",
    "pydoc",
    "doctest",
    "test"
]

def prompt_build_optimization():
    """
    Prompts the user during the build flow if they want to apply
    executable size optimization (module exclusions + UPX) or keep a full build.
    """
    print("\nBINARY PACKAGING & OPTIMIZATION:")
    print(" [1] ⚡ Slim / Optimized (~20-25 MB) - Strips unused Qt WebEngine/QML/3D modules")
    print(" [2] 📦 Full Build (~55-65 MB)      - Includes all default PySide6 modules without exclusions")
    
    choice = input("Select packaging mode [1]: ").strip()
    if choice == "2":
        print("-> Selected FULL build (no modules excluded).")
        return ""
    else:
        print("-> Selected SLIM / OPTIMIZED build (excluding unused Qt subsystems).")
        exclude_args = " ".join([f'--exclude-module {mod}' for mod in DEFAULT_OPTIMIZED_EXCLUDES])
        return " " + exclude_args

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
        return "1.0.0"
    with open("VERSION", "r", encoding="utf-8") as f:
        try:
            content = f.read().strip()
            v_str = content.splitlines()[0].lower().replace('v', '')
            return v_str if v_str else "1.0.0"
        except:
            return "1.0.0"

def get_next_version():
    v_str = get_current_version()
    parts = v_str.split('.')
    if len(parts) >= 1:
        # Increment the last segment (e.g. 1.8.0 -> 1.8.1)
        try:
            parts[-1] = str(int(parts[-1]) + 1)
            return ".".join(parts)
        except:
            return v_str + ".1"
    return "1.0.0"

def get_current_beta_version():
    """Reads current beta version from VERSION_BETA or falls back to VERSION."""
    if os.path.exists("VERSION_BETA"):
        try:
            with open("VERSION_BETA", "r", encoding="utf-8") as f:
                content = f.read().strip()
                v_str = content.splitlines()[0].lower().replace('v', '')
                if v_str:
                    return v_str
        except Exception:
            pass
    current_stable = get_current_version()
    return f"{current_stable}b"

def get_next_beta_version():
    """
    Calculates the next bumped beta version from VERSION_BETA.
    Examples:
      - 3.0b      -> 3.0.1b
      - 3.0.1b    -> 3.0.2b
      - 3.0b1     -> 3.0b2
      - 3.0-beta1 -> 3.0-beta2
    """
    current_beta = get_current_beta_version()
    
    # 1. Trailing number after b/beta: e.g. 3.0b1 -> 3.0b2
    match_trail = re.search(r"^(.*?b(?:eta)?[\.\-]?)(\d+)$", current_beta, re.IGNORECASE)
    if match_trail:
        prefix = match_trail.group(1)
        num = int(match_trail.group(2)) + 1
        return f"{prefix}{num}"

    # 2. Pattern: X.Y.Zb -> increment Z (e.g. 3.0.1b -> 3.0.2b)
    match_dot = re.search(r"^(\d+\.\d+\.)(\d+)(b(?:eta)?)$", current_beta, re.IGNORECASE)
    if match_dot:
        prefix = match_dot.group(1)
        patch = int(match_dot.group(2)) + 1
        suffix = match_dot.group(3)
        return f"{prefix}{patch}{suffix}"

    # 3. Pattern: X.Yb -> e.g. 3.0b -> 3.0.1b
    match_letter = re.search(r"^(\d+\.\d+)(b(?:eta)?)$", current_beta, re.IGNORECASE)
    if match_letter:
        base = match_letter.group(1)
        suffix = match_letter.group(2)
        return f"{base}.1{suffix}"

    # 4. Fallback: split on dot and increment last segment
    parts = current_beta.split('.')
    if len(parts) >= 1:
        try:
            parts[-1] = str(int(parts[-1]) + 1)
            return ".".join(parts)
        except Exception:
            return current_beta + ".1"
    return "3.0.1b"

def get_github_repo_info():
    """Detects GitHub owner/repo from local git remote if available, with fallback."""
    raw = run_command("git config --get remote.origin.url", None, capture=True)
    if raw:
        match = re.search(r"github\.com[:/]([\w\-]+)/([\w\-]+?)(?:\.git)?$", str(raw).strip())
        if match:
            return f"{match.group(1)}/{match.group(2)}"
    return "subvhome/m59-companion"

def update_readme_links(stable_version=None, beta_version=None):
    """Dynamically updates or injects both Stable & Beta download links into README.md."""
    if not os.path.exists("README.md"):
        return
    try:
        repo_slug = get_github_repo_info()
        with open("README.md", "r", encoding="utf-8") as f:
            content = f.read()

        stable_url = f"https://github.com/{repo_slug}/raw/main/dist/M59Companion.exe"
        beta_url = f"https://github.com/{repo_slug}/raw/main/dist/M59Companion_beta.exe"

        s_label = f" (v{stable_version})" if stable_version else " (Latest)"
        b_label = f" (v{beta_version})" if beta_version else " (Beta Build)"

        stable_line = f"- **🌟 Stable Release**: [Download M59Companion.exe{s_label}]({stable_url})"
        beta_line = f"- **🧪 Beta Preview**: [Download M59Companion_beta.exe{b_label}]({beta_url})"

        if re.search(r"-\s*\*\*.*Stable.*?\*\*:\s*\[.*\]\(.*M59Companion\.exe.*\)", content, re.IGNORECASE):
            content = re.sub(
                r"-\s*\*\*.*Stable.*?\*\*:\s*\[.*\]\(.*M59Companion\.exe.*\)",
                stable_line,
                content,
                flags=re.IGNORECASE
            )
        
        if re.search(r"-\s*\*\*.*Beta.*?\*\*:\s*\[.*\]\(.*M59Companion_beta\.exe.*\)", content, re.IGNORECASE):
            content = re.sub(
                r"-\s*\*\*.*Beta.*?\*\*:\s*\[.*\]\(.*M59Companion_beta\.exe.*\)",
                beta_line,
                content,
                flags=re.IGNORECASE
            )
        elif "M59Companion.exe" in content:
            content = re.sub(
                r"(- \*\*.*Stable.*?\*\*: \[.*?\]\(.*?M59Companion\.exe.*?\))",
                r"\1\n" + beta_line,
                content
            )
        else:
            content += f"\n\n### 📥 Downloads & Releases\n{stable_line}\n{beta_line}\n"

        with open("README.md", "w", encoding="utf-8") as f:
            f.write(content)
        print(f"-> Updated README.md download links for repository: {repo_slug}")
    except Exception as ex:
        print(f"!! Warning: Could not update README.md automatically: {ex}")

def update_readme_beta_link(beta_version):
    """Convenience wrapper for beta updates."""
    update_readme_links(beta_version=beta_version)

def show_restore_menu():
    print("\n--- RESTORE MODULE ---")
    # Make sure we have latest tags
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
            # format: <hash>\trefs/tags/<tagname>
            parts = line.split("refs/tags/")
            if len(parts) > 1:
                tag = parts[-1].replace('^{}', '') # Handle dereferenced tags
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
            # Delete local tag just in case
            run_command(f'git tag -d "{target_tag}" 2>nul || true', "Deleting local tag (if exists)")
            
            # Delete remote tag
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
        print(" [2] Build & Publish Beta Release (_beta.exe)")
        print(" [3] Quick Sync (Code Only - No Build)")
        print(" [4] Restore Working Version (From Tag)")
        print(" [5] Delete a Tag")
        print(" [Q] Exit")
        
        main_choice = input("\nSelect action: ").strip().upper()
        
        if main_choice == '1':
            run_promotion()
            break
        elif main_choice == '2':
            run_beta_promotion()
            break
        elif main_choice == '3':
            run_quick_sync()
            break
        elif main_choice == '4':
            if show_restore_menu():
                break
        elif main_choice == '5':
            if show_delete_tag_menu():
                break
        elif main_choice == 'Q':
            break

def perform_git_sync(do_full_sync, commit_msg, version=None, tag_msg=None, extra_files_to_force_add=None):
    """Core Git logic shared by Release, Beta, and Quick Sync."""
    if extra_files_to_force_add is None:
        extra_files_to_force_add = ["dist/M59Companion.exe"]

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
    if not run_command(push_cmd, "Pushing Code"): return False
    if version:
        if not run_command("git push origin --tags --force", "Pushing Version Tags"): return False
        
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
    """Dynamically identifies files to bundle in the EXE based on what Git is tracking."""
    asset_exts = {'.json', '.csv', '.wav', '.txt', '.md'}
    asset_dirs = {'imgs', 'sound'}
    
    cmd = "git ls-files"
    raw = run_command(cmd, "Detecting tracked assets", capture=True)
    
    bundled = []
    seen_dirs = set()
    
    if raw:
        for line in raw.splitlines():
            line = line.strip()
            if not line: continue
            
            found_dir = False
            for d in asset_dirs:
                if line.startswith(d + "/"):
                    if d not in seen_dirs:
                        bundled.append(f'--add-data "{d};{d}"')
                        seen_dirs.add(d)
                    found_dir = True
                    break
            if found_dir: continue
            
            if line in ("VERSION", "VERSION_BETA") or any(line.endswith(ext) for ext in asset_exts):
                if os.path.exists(line):
                    if "/" in line:
                        dest_folder = os.path.dirname(line)
                        bundled.append(f'--add-data "{line};{dest_folder}"')
                    else:
                        bundled.append(f'--add-data "{line};."')
    else:
        # Fallback if git is not initialized
        for d in asset_dirs:
            if os.path.exists(d):
                bundled.append(f'--add-data "{d};{d}"')
        if os.path.exists("settings"):
            bundled.append('--add-data "settings;settings"')
        if os.path.exists("VERSION"):
            bundled.append('--add-data "VERSION;."')
        if os.path.exists("VERSION_BETA"):
            bundled.append('--add-data "VERSION_BETA;."')
                
    return bundled

def generate_version_info(version_str, is_beta=False):
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
    desc_str = f"M59 Companion Application {'(Beta Preview)' if is_beta else ''}"
    file_name_str = "M59Companion_beta.exe" if is_beta else "M59Companion.exe"
    
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

def check_file_size_limit(file_path, limit_mb=50.0):
    """Verifies generated binary is within GitHub's standard upload ceiling."""
    if os.path.exists(file_path):
        size_bytes = os.path.getsize(file_path)
        size_mb = size_bytes / (1024 * 1024)
        print(f"-> Executable size: {size_mb:.2f} MB")
        if size_mb > limit_mb:
            print(f"!! WARNING: {file_path} is {size_mb:.2f} MB, which exceeds the {limit_mb} MB limit!")
            return False
        else:
            print(f"-> Size check PASSED ({size_mb:.2f} MB / {limit_mb} MB max limit)")
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
    opt_flags = prompt_build_optimization()
    print("\n-> Compiling Master Stable Dashboard (M59Companion.exe)...")
    dynamic_assets = get_git_tracked_assets()
    asset_str = " ".join(dynamic_assets)
    
    generate_version_info(version, is_beta=False)
    
    compile_cmd = f'python -m PyInstaller --clean --onefile --noconsole --icon="imgs/m59comp.ico" --version-file version_info.txt{opt_flags} {asset_str} --name M59Companion m59_dashboard.py'
    
    if not run_command(compile_cmd, "Building Standalone Binary"):
        print("!! COMPILATION FAILED. Pipeline aborted.")
        return

    check_file_size_limit("dist/M59Companion.exe", limit_mb=50.0)

    # 4. Update README.md with Stable Download Link
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

    # 5. Git Operations & Pushing
    tag_msg = f"Version {version}"
    if update_message: tag_msg += f"\n\nRelease Notes:\n{update_message}"

    if perform_git_sync(do_full_sync, commit_msg, version=version, tag_msg=tag_msg, extra_files_to_force_add=["dist/M59Companion.exe"]):
        print("\n==========================================")
        print(f"   SUCCESS: Version {version} is LIVE!   ")
        print(f"   Tag v{version} created for easy revert.  ")
        print("==========================================")

def run_beta_promotion():
    print("\n--- 🧪 BUILD & PUBLISH BETA RELEASE ---")
    current_beta_v = get_current_beta_version()
    default_beta_v = get_next_beta_version()
    
    user_v = input(f"Enter Beta Version name [{default_beta_v}] (current: v{current_beta_v}): ").strip()
    beta_version = user_v if user_v else default_beta_v

    beta_notes = input("Enter Beta release notes (or press enter to skip): ").strip()

    with open("VERSION_BETA", "w", encoding="utf-8") as f:
        f.write(beta_version)
        if beta_notes:
            f.write(f"\n{beta_notes}")
    print(f"-> VERSION_BETA file updated to v{beta_version}")

    # 1. Compilation for Beta target (_beta.exe) & Packaging Optimization
    opt_flags = prompt_build_optimization()
    print("\n-> Compiling Beta Dashboard (M59Companion_beta.exe)...")
    dynamic_assets = get_git_tracked_assets()
    asset_str = " ".join(dynamic_assets)
    
    generate_version_info(beta_version, is_beta=True)
    
    compile_cmd = f'python -m PyInstaller --clean --onefile --noconsole --icon="imgs/m59comp.ico" --version-file version_info.txt{opt_flags} {asset_str} --name M59Companion_beta m59_dashboard.py'
    
    if not run_command(compile_cmd, "Building Standalone Beta Binary"):
        print("!! BETA COMPILATION FAILED. Pipeline aborted.")
        return

    # 2. File size verification (< 50MB)
    check_file_size_limit("dist/M59Companion_beta.exe", limit_mb=50.0)

    # 3. Update README.md with Beta Download Link
    update_readme_beta_link(beta_version)

    # 4. Verification Pause
    print("\n" + "!"*45)
    print(f" BETA PIPELINE PAUSED: Please test 'dist/M59Companion_beta.exe'")
    print(f" Preserves stable 'dist/M59Companion.exe' untouched.")
    print("!"*45)

    confirm = input("\nReady to push BETA build to GitHub? (yes/no): ").lower()
    if confirm != "yes":
        print("Beta publication aborted. Local files remain intact.")
        return

    commit_msg = input(f"Enter commit message [Beta Build v{beta_version}]: ").strip()
    if not commit_msg:
        commit_msg = f"Beta Build v{beta_version}"
    if beta_notes:
        commit_msg += f"\n\nBeta Notes: {beta_notes}"

    # Push to repository while keeping current stable release tag intact
    extra_files = ["dist/M59Companion_beta.exe"]
    if os.path.exists("dist/M59Companion.exe"):
        extra_files.append("dist/M59Companion.exe")

    if perform_git_sync(do_full_sync=False, commit_msg=commit_msg, version=None, tag_msg=None, extra_files_to_force_add=extra_files):
        print("\n==========================================")
        print(f"   SUCCESS: Beta {beta_version} is PUBLISHED! ")
        print(f"   Stable binary & tags remain intact.       ")
        print(f"   README.md updated with Beta download link.")
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
