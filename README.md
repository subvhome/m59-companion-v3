# Meridian 59 Companion

A modern Qt companion dashboard for Meridian 59 featuring real-time chat overlays, quick macros, kill/vault trackers, map viewers, spell calculators, and in-game alerts.

---

### 📥 Downloads & Releases

- **🌟 Stable Release**: [Download M59Companion.exe (Latest)](https://github.com/Substance-V/m59companion/raw/main/dist/M59Companion.exe)
- **🧪 Beta Preview**: [Download M59Companion_beta.exe (v3.0b)](https://github.com/Substance-V/m59companion/raw/main/dist/M59Companion_beta.exe)

---

### 🚀 Key Features
- **In-Game Overlay Anchoring**: Floating macros, teleport buttons, and floating chatbox attached to the Meridian 59 game client.
- **PK / PvP Red Alert Box**: Flashes an in-game red boundary indicator during attacks or PK alerts.
- **Real-Time GPS & Map Viewer**: Room detection, pathfinding, and interactive world map.
- **Inventory & Vault Manager**: Comprehensive vault, bank, and inventory item logs.
- **Spell & Combat Analytics**: Combat logs, spellbook calculator, and stats analyzer.

---

### 🛠️ Building & Releasing
Run the integrated pipeline promoter:
```bash
python release_promoter.py
```
Options available:
1. **Promote New Release (Build & Tag)**: Builds `M59Companion.exe` and tags the official release.
2. **Build & Publish Beta Release (_beta.exe)**: Compiles `M59Companion_beta.exe`, updates download links in `README.md`, and pushes without overriding stable tags.
3. **Quick Sync**: Syncs code without triggering a binary build.
4. **Restore Version**: Reverts files to any historical release tag.
