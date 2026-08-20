# Meridian 59 Companion v3.0

A modern PySide6/Qt companion dashboard for Meridian 59 featuring real-time client overlay controls, automated map pathfinding & room detection, combat analytics, inventory & vault logging, and spell calculators.

---

### 📥 Direct Download

- **🚀 Latest Release**: [Download M59Companion.exe (v3.1.3)](https://github.com/subvhome/m59-companion-v3/raw/main/dist/M59Companion.exe)

---

### ✨ Key Features

- **In-Game Client Overlay**: Attached macro bar, teleport buttons, and floating client overlays.
- **PK / PvP Danger Indicator**: Visual boundary flash and audio alerts during PvP attacks or player threats.
- **Interactive Map & GPS**: Real-time room detection, pathfinding routes, and interactive world map.
- **Inventory, Vault & Bank Tracker**: Complete item tracking, vault storage logs, and bank balance history.
- **Combat & Spell Analyzer**: Live combat metrics, spellbook calculator, and stats analyzer.

---

### 🛠️ Build & Development

Run the release promoter script to compile and push updates:
```bash
python release_promoter.py
```
Options available:
1. **Promote New Release**: Compiles `M59Companion.exe`, generates metadata, updates `README.md`, and tags the release on GitHub.
2. **Quick Sync**: Syncs code updates directly without triggering a binary build.
3. **Restore Version**: Reverts working directory to a previous release tag.

