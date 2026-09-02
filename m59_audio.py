# -*- coding: utf-8 -*-
"""
M59 Audio & Sound Alert Module
Provides sound generation for missing sound files and asynchronous
audio playback for PK warnings, direct message chimes, and alerts.
"""

import os
import sys

def ensure_default_sounds():
    """Generates default alert WAV files if missing."""
    import wave, math, struct
    os.makedirs("sound", exist_ok=True)
    
    pk_path = os.path.join("sound", "alert.wav")
    if not os.path.exists(pk_path):
        try:
            sample_rate = 22050
            duration = 0.4
            n_samples = int(sample_rate * duration)
            with wave.open(pk_path, 'wb') as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(sample_rate)
                for i in range(n_samples):
                    freq = 880 if (i / sample_rate) < 0.2 else 440
                    t = i / sample_rate
                    val = int(16000 * math.sin(2 * math.pi * freq * t))
                    f.writeframesraw(struct.pack('<h', val))
        except Exception as e:
            print(f"[M59-SOUND] Could not generate alert.wav: {e}", flush=True)

    tell_path = os.path.join("sound", "dm_chime.wav")
    if not os.path.exists(tell_path) and not os.path.exists(os.path.join("sound", "dm_chime.mp3")):
        try:
            sample_rate = 22050
            duration = 0.35
            n_samples = int(sample_rate * duration)
            with wave.open(tell_path, 'wb') as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(sample_rate)
                for i in range(n_samples):
                    t = i / sample_rate
                    if t < 0.1: freq = 523.25
                    elif t < 0.2: freq = 659.25
                    else: freq = 783.99
                    val = int(15000 * math.sin(2 * math.pi * freq * t) * (1.0 - t/duration))
                    f.writeframesraw(struct.pack('<h', val))
        except Exception as e:
            print(f"[M59-SOUND] Could not generate dm_chime.wav: {e}", flush=True)

def play_audio_file(filepath):
    """Plays audio file or Windows system sound alias asynchronously."""
    try:
        if not filepath:
            return
        if filepath.startswith("System"):
            if sys.platform == 'win32':
                try:
                    import winsound
                    winsound.PlaySound(filepath, winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                    return
                except Exception:
                    pass
        
        target = filepath
        if not os.path.isabs(target):
            cwd_p = os.path.join(os.getcwd(), target)
            if os.path.exists(cwd_p):
                target = cwd_p
            else:
                alt_p = os.path.join("sound", os.path.basename(target))
                if os.path.exists(alt_p):
                    target = alt_p

        if sys.platform == 'win32':
            import ctypes
            try:
                ctypes.windll.winmm.mciSendStringW('close m59_audio', None, 0, None)
                res = ctypes.windll.winmm.mciSendStringW(f'open "{target}" alias m59_audio', None, 0, None)
                if res != 0:
                    import winsound
                    winsound.PlaySound(target, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                else:
                    ctypes.windll.winmm.mciSendStringW('play m59_audio', None, 0, None)
                return
            except Exception:
                pass

            try:
                import winsound
                winsound.PlaySound(target, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            except Exception:
                pass
    except Exception as ex:
        print(f"[M59-SOUND] Audio playback error: {ex}", flush=True)

