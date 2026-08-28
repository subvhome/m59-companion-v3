import os
import shutil

def main():
    print("=" * 71)
    print("         M59Companion Project File Reorganization Tool")
    print("=" * 71)
    print("This tool will move static application datasets to 'data/' and")
    print("standalone utility scripts to 'scripts/', keeping 'settings/' clean.")
    print("You will be prompted (Y/N) for each file before moving.")
    print("=" * 71 + "\n")

    os.makedirs("data", exist_ok=True)
    os.makedirs("scripts", exist_ok=True)
    os.makedirs("settings", exist_ok=True)

    # 1. Datasets -> data/
    dataset_files = [
        "items.json",
        "m59_data.json",
        "meridian_rooms_dataset.json",
        "moblist.csv",
        "morphable_monsters.csv",
        "morph_creatures.csv",
        "spells.csv",
        "travel_times.json",
        "blakston.pal",
        "m59_layout_config.json"
    ]

    print("[CATEGORY 1] Move Static Datasets to 'data/'")
    print("-" * 71)
    for fname in dataset_files:
        # Check root
        if os.path.exists(fname) and os.path.isfile(fname):
            dst = os.path.join("data", fname)
            ans = input(f"Move '{fname}' -> '{dst}'? (Y/N): ").strip().lower()
            if ans == 'y':
                try:
                    shutil.move(fname, dst)
                    print(f"  [SUCCESS] Moved '{fname}' to '{dst}'")
                except Exception as e:
                    print(f"  [ERROR] Could not move '{fname}': {e}")
            else:
                print(f"  [SKIPPED] '{fname}'")

        # Check legacy settings/
        legacy_path = os.path.join("settings", fname)
        if os.path.exists(legacy_path) and os.path.isfile(legacy_path):
            dst = os.path.join("data", fname)
            ans = input(f"Move '{legacy_path}' -> '{dst}'? (Y/N): ").strip().lower()
            if ans == 'y':
                try:
                    shutil.move(legacy_path, dst)
                    print(f"  [SUCCESS] Moved '{legacy_path}' to '{dst}'")
                except Exception as e:
                    print(f"  [ERROR] Could not move '{legacy_path}': {e}")
            else:
                print(f"  [SKIPPED] '{legacy_path}'")

    # 2. Standalone Scripts -> scripts/
    script_files = [
        "dev_pipeline.py",
        "dev_pipeline.txt",
        "release_promoter.py",
        "m59_vault-orig.py",
        "m59_layout_preview.py",
        "m59_bgf_viewer.py",
        "m59_gps-cli.py",
        "fix_try.py",
        "refactor.py",
        "unify_cards.py",
        "update_qss.py",
        "tree.txt"
    ]

    print("\n[CATEGORY 2] Move Standalone & Development Scripts to 'scripts/'")
    print("-" * 71)
    for fname in script_files:
        if os.path.exists(fname) and os.path.isfile(fname):
            dst = os.path.join("scripts", fname)
            ans = input(f"Move '{fname}' -> '{dst}'? (Y/N): ").strip().lower()
            if ans == 'y':
                try:
                    shutil.move(fname, dst)
                    print(f"  [SUCCESS] Moved '{fname}' to '{dst}'")
                except Exception as e:
                    print(f"  [ERROR] Could not move '{fname}': {e}")
            else:
                print(f"  [SKIPPED] '{fname}'")

    print("\n" + "=" * 71)
    print("File reorganization complete!")
    print("=" * 71)

if __name__ == "__main__":
    main()
