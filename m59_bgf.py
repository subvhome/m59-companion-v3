import struct
import zlib
import os
import urllib.request
try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    Image = None
    ImageTk = None

class BGFManager:
    def __init__(self, resource_dir=None):
        self.resource_dir = resource_dir
        self.palette = self._load_palette()
        self._cache = {}  # filepath -> ImageTk.PhotoImage / composite list
        self._headers = {}  # filepath -> header dict

    def get_bgf_header(self, filepath):
        """Returns header metadata for a given BGF path if cached or loaded."""
        if not filepath:
            return None
        first_path = filepath.split("|")[0]
        if first_path in self._headers:
            return self._headers[first_path]
        # Try loading raw frames to populate header
        self._load_raw_frames(first_path)
        return self._headers.get(first_path)

    def _load_palette(self):
        palette = []
        pal_path = "blakston.pal"
        
        if not os.path.exists(pal_path):
            try:
                url = "https://raw.githubusercontent.com/Meridian59/Meridian59/master/blakston.pal"
                urllib.request.urlretrieve(url, pal_path)
            except Exception as e:
                print("Failed to download blakston.pal:", e)
                
        try:
            with open(pal_path, "r") as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) >= 3:
                        r, g, b = int(parts[0]), int(parts[1]), int(parts[2])
                        palette.extend([r, g, b])
        except Exception as e:
            for i in range(256):
                palette.extend([i, i, i])
                
        if len(palette) < 768:
            palette.extend([0] * (768 - len(palette)))
        elif len(palette) > 768:
            palette = palette[:768]
            
        return palette

    def _load_raw_frames(self, filepath):
        if not HAS_PIL: return None
        if not os.path.exists(filepath): return None
        try:
            frames = []
            with open(filepath, "rb") as f:
                magic = f.read(4)
                if magic != b"BGF\x11": return None
                
                version = struct.unpack("<I", f.read(4))[0]
                name = f.read(32).decode("ascii", "ignore").strip('\x00')
                num_bitmaps = struct.unpack("<I", f.read(4))[0]
                num_groups = struct.unpack("<I", f.read(4))[0]
                max_indices = struct.unpack("<I", f.read(4))[0]
                shrink = struct.unpack("<I", f.read(4))[0]
                
                self._headers[filepath] = {
                    "version": version,
                    "name": name,
                    "num_bitmaps": num_bitmaps,
                    "num_groups": num_groups,
                    "max_indices": max_indices,
                    "shrink": shrink
                }
                
                if num_bitmaps == 0: return None

                for i in range(num_bitmaps):
                    width, height = struct.unpack("<II", f.read(8))
                    x_off, y_off = struct.unpack("<ii", f.read(8))
                    
                    num_hotspots = struct.unpack("B", f.read(1))[0]
                    hotspots = {}
                    for _ in range(num_hotspots):
                        hn = struct.unpack("b", f.read(1))[0]
                        hx, hy = struct.unpack("<ii", f.read(8))
                        hotspots[hn] = (hx, hy)
                        hotspots[abs(hn)] = (hx, hy)
                        
                    is_comp = struct.unpack("B", f.read(1))[0]
                    if is_comp == 1:
                        comp_len = struct.unpack("<I", f.read(4))[0]
                        comp_data = f.read(comp_len)
                        try: data = zlib.decompress(comp_data)
                        except: data = b'\x00' * (width * height)
                    else:
                        _ = struct.unpack("<I", f.read(4))[0]
                        data = f.read(width * height)
                        
                    img = Image.new("P", (width, height))
                    img.putpalette(self.palette)
                    img.frombytes(data[:width*height])
                    
                    rgba_img = img.convert("RGBA")
                    datas = rgba_img.getdata()
                    trans_color = tuple(self.palette[254*3:254*3+3])
                    
                    new_data = []
                    for item in datas:
                        if item[0] == trans_color[0] and item[1] == trans_color[1] and item[2] == trans_color[2]:
                            new_data.append((255, 255, 255, 0))
                        else:
                            new_data.append(item)
                    rgba_img.putdata(new_data)
                    
                    frames.append({
                        "image": rgba_img,
                        "x_off": x_off,
                        "y_off": y_off,
                        "hotspots": hotspots
                    })
            return frames
        except Exception as e:
            print(f"Error loading BGF {filepath}: {e}")
            return None

    def load_bgf_frames(self, filepath):
        if filepath in self._cache:
            return self._cache[filepath]
        
        paths = filepath.split("|")
        all_layers_frames = []
        for p in paths:
            frames = self._load_raw_frames(p)
            if frames:
                all_layers_frames.append((p, frames))
                
        if not all_layers_frames:
            return None
            
        ROLE_HOTSPOTS = {
            "head": 1,         # HS_HEAD = 1
            "legs": 41,        # HS_LEGS = 41
            "left_arm": 31,    # HS_LEFT_HAND = 31
            "right_arm": 21,   # HS_RIGHT_HAND = 21
            "weapon": 22,      # HS_RIGHT_WEAPON = 22
            "eyes": 11,        # HS_EYES = 11
            "mouth": 12,       # HS_MOUTH = 12
            "hair": 13,        # HS_TOUPEE = 13
            "nose": 14,        # HS_NOSE = 14
        }
        
        ROLE_PARENTS = {
            "eyes": "head",
            "mouth": "head",
            "hair": "head",
            "nose": "head",
        }
        
        def get_layer_role(path):
            fn = os.path.basename(path).lower()
            if "hed" in fn or "head" in fn or "phax" in fn or "phkx" in fn or "herhead" in fn:
                return "head"
            elif "legs" in fn or "feet" in fn or "bfa" in fn or "bfb" in fn or "bfc" in fn or "bfd" in fn or "bfg" in fn or "iceper_feet" in fn:
                return "legs"
            elif "leftarm" in fn or "larm" in fn or "bla" in fn or "blb" in fn or "blg" in fn:
                return "left_arm"
            elif "rightarm" in fn or "rarm" in fn or "bra" in fn or "brb" in fn or "brg" in fn or "iceper_rightarm" in fn:
                return "right_arm"
            elif "sword" in fn or "weapon" in fn or "mace" in fn or "scimitar" in fn or "shswd" in fn or "hamr" in fn or "axe" in fn or "iceper_sword" in fn:
                return "weapon"
            elif "eyes" in fn or "peax" in fn or "pekx" in fn or "pebx" in fn or "pecx" in fn or "pedx" in fn:
                return "eyes"
            elif "mouth" in fn or "pmax" in fn or "pmkx" in fn or "pmbx" in fn or "pmcx" in fn:
                return "mouth"
            elif "nose" in fn or "pnax" in fn or "pnkx" in fn or "pnbx" in fn or "pncx" in fn:
                return "nose"
            elif "hair" in fn or "toupee" in fn or "ptac" in fn or "ptcd" in fn or "ptba" in fn or "ptad" in fn or "ptbb" in fn or "ptxa" in fn or "ptbc" in fn or "ptca" in fn or "ptdb" in fn or "ptq" in fn:
                return "hair"
            elif "torso" in fn or "body" in fn or "bta" in fn or "btb" in fn or "btg" in fn or "iceper_torso" in fn:
                return "base"
            else:
                return "unknown"

        def get_processing_order(role):
            if role == "base": return 0
            elif role == "head": return 1
            elif role in ["legs", "left_arm", "right_arm", "weapon"]: return 2
            elif role in ["eyes", "mouth", "nose", "hair"]: return 3
            else: return 4

        assigned_layers = []
        has_base = False
        for p, f in all_layers_frames:
            role = get_layer_role(p)
            if role == "base":
                has_base = True
            assigned_layers.append((role, f))
            
        if not has_base and assigned_layers:
            first_role, first_frames = assigned_layers[0]
            assigned_layers[0] = ("base", first_frames)

        assigned_layers.sort(key=lambda item: get_processing_order(item[0]))
        
        base_role, base_frames = assigned_layers[0]
        num_frames = len(base_frames)
        final_photos = []
        
        for i in range(num_frames):
            layers_to_paste = []
            world_hotspots = {}
            
            base_f = base_frames[i]
            base_draw_x = base_f['x_off']
            base_draw_y = base_f['y_off']
            layers_to_paste.append((base_f['image'], base_draw_x, base_draw_y))
            
            for hs_id, (hx, hy) in base_f.get('hotspots', {}).items():
                world_hotspots[("base", hs_id)] = (hx, hy)
                
            for layer_role, layer_frames in assigned_layers[1:]:
                f = layer_frames[i % len(layer_frames)]
                child_hotspots = f.get('hotspots', {})
                
                parent_role = ROLE_PARENTS.get(layer_role, "base")
                target_hs = ROLE_HOTSPOTS.get(layer_role)
                
                aligned = False
                if target_hs is not None:
                    parent_hs_key = (parent_role, target_hs)
                    if parent_hs_key in world_hotspots:
                        target_x, target_y = world_hotspots[parent_hs_key]
                        child_origin_x = target_x
                        child_origin_y = target_y
                        child_draw_x = child_origin_x + f['x_off']
                        child_draw_y = child_origin_y + f['y_off']
                        aligned = True
                        
                if not aligned:
                    flat_world_hotspots = {}
                    for (r, h_id), coords in world_hotspots.items():
                        flat_world_hotspots[h_id] = coords
                        
                    common = set(flat_world_hotspots.keys()).intersection(child_hotspots.keys())
                    if common:
                        hs_id = list(common)[0]
                        target_x, target_y = flat_world_hotspots[hs_id]
                        child_origin_x = target_x - child_hotspots[hs_id][0]
                        child_origin_y = target_y - child_hotspots[hs_id][1]
                        child_draw_x = child_origin_x + f['x_off']
                        child_draw_y = child_origin_y + f['y_off']
                        aligned = True
                        
                if not aligned:
                    child_origin_x = 0
                    child_origin_y = 0
                    child_draw_x = f['x_off']
                    child_draw_y = f['y_off']
                    
                layers_to_paste.append((f['image'], child_draw_x, child_draw_y))
                
                for hs_id, (hx, hy) in child_hotspots.items():
                    world_hotspots[(layer_role, hs_id)] = (child_origin_x + hx, child_origin_y + hy)
                    
            min_x = min(item[1] for item in layers_to_paste)
            max_x = max(item[1] + item[0].width for item in layers_to_paste)
            min_y = min(item[2] for item in layers_to_paste)
            max_y = max(item[2] + item[0].height for item in layers_to_paste)
            
            width = max_x - min_x
            height = max_y - min_y
            
            if width <= 0 or height <= 0:
                comp_img = Image.new("RGBA", (1, 1), (255, 255, 255, 0))
            else:
                comp_img = Image.new("RGBA", (width, height), (255, 255, 255, 0))
                for img, x, y in layers_to_paste:
                    comp_img.paste(img, (x - min_x, y - min_y), img)
                    
            bbox = comp_img.getbbox()
            if bbox:
                comp_img = comp_img.crop(bbox)
            final_photos.append(comp_img)
            
        self._cache[filepath] = final_photos
        return final_photos

    def load_bgf_first_frame(self, filepath):
        if filepath in self._cache:
            return self._cache[filepath]

        if not os.path.exists(filepath):
            return None

        try:
            with open(filepath, "rb") as f:
                magic = f.read(4)
                if magic != b"BGF\x11":
                    return None
                    
                version = struct.unpack("<I", f.read(4))[0]
                name = f.read(32).decode("ascii", "ignore").strip('\x00')
                num_bitmaps = struct.unpack("<I", f.read(4))[0]
                num_groups = struct.unpack("<I", f.read(4))[0]
                max_indices = struct.unpack("<I", f.read(4))[0]
                shrink = struct.unpack("<I", f.read(4))[0]
                
                if num_bitmaps == 0:
                    return None

                # Read first bitmap
                width, height = struct.unpack("<II", f.read(8))
                x_off, y_off = struct.unpack("<ii", f.read(8))
                
                num_hotspots = struct.unpack("B", f.read(1))[0]
                for _ in range(num_hotspots):
                    f.read(9) # skip hotspots
                    
                is_comp = struct.unpack("B", f.read(1))[0]
                if is_comp == 1:
                    comp_len = struct.unpack("<I", f.read(4))[0]
                    comp_data = f.read(comp_len)
                    data = zlib.decompress(comp_data)
                else:
                    _ = struct.unpack("<I", f.read(4))[0] # padding
                    data = f.read(width * height)
                    
                img = Image.new("P", (width, height))
                img.putpalette(self.palette)
                img.frombytes(data[:width*height])
                
                rgba_img = img.convert("RGBA")
                datas = rgba_img.getdata()
                trans_color = tuple(self.palette[254*3:254*3+3])
                
                new_data = []
                for item in datas:
                    if item[0] == trans_color[0] and item[1] == trans_color[1] and item[2] == trans_color[2]:
                        new_data.append((255, 255, 255, 0))
                    else:
                        new_data.append(item)
                rgba_img.putdata(new_data)
                
                # Resize if too big for a list icon (e.g., max 32x32)
                rgba_img.thumbnail((32, 32), Image.LANCZOS)
                
                photo = ImageTk.PhotoImage(rgba_img)
                self._cache[filepath] = photo
                return photo
        except Exception as e:
            print(f"Error loading BGF {filepath}: {e}")
            return None

    def find_bgf_for_monster(self, class_name):
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"BGF: Searching for {class_name} in resource_dir={self.resource_dir}")
        
        if "|" in class_name:
            paths = []
            for part in class_name.split("|"):
                p = self._find_single_bgf(part)
                if p:
                    paths.append(p)
            if paths:
                return "|".join(paths)
            return None
        return self._find_single_bgf(class_name)

    def _find_single_bgf(self, class_name):
        import logging
        logger = logging.getLogger(__name__)
        
        search_dirs = []
        if self.resource_dir:
            search_dirs.append(self.resource_dir)
            try:
                search_dirs.append(os.path.join(self.resource_dir, "graphics"))
                search_dirs.append(os.path.join(self.resource_dir, "rooms"))
            except:
                pass
            try:
                parent = os.path.dirname(self.resource_dir)
                if parent:
                    search_dirs.append(parent)
                    search_dirs.append(os.path.join(parent, "graphics"))
                    search_dirs.append(os.path.join(parent, "resource"))
                    search_dirs.append(os.path.join(parent, "resource", "graphics"))
            except:
                pass
                
        # Common installation paths for Steam and Webclient/Non-Steam
        import getpass
        try:
            local_app_data = os.environ.get('LOCALAPPDATA', f"C:\\Users\\{getpass.getuser()}\\AppData\\Local")
            search_dirs.extend([
                os.path.join(local_app_data, "Meridian 59", "resource"),
                os.path.join(local_app_data, "Meridian 59", "resource", "graphics"),
                os.path.join(local_app_data, "Meridian 59", "resource", "rooms"),
                os.path.join(local_app_data, "Meridian 59"),
                "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Meridian 59\\resource",
                "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Meridian 59\\resource\\graphics",
                "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Meridian 59\\resource\\rooms",
                "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Meridian 59"
            ])
        except:
            pass

        # Always add standard fallback paths relative to working directory
        local_base = os.getcwd()
        search_dirs.extend([
            os.path.join(local_base, "m59_codebase", "resource", "graphics"),
            os.path.join(local_base, "m59_codebase", "resource"),
            os.path.join(local_base, "resource", "graphics"),
            os.path.join(local_base, "resource"),
            os.path.join(local_base, "graphics"),
            local_base
        ])
        
        # Clean candidates list
        if class_name.lower().endswith('.bgf'):
            candidates = [class_name, class_name.lower(), class_name.upper()]
        else:
            candidates = [
                f"{class_name}.bgf", f"{class_name.lower()}.bgf", f"{class_name.upper()}.bgf",
                f"{class_name[:8]}.bgf", f"{class_name[:8].lower()}.bgf", f"{class_name[:8].upper()}.bgf"
            ]
            
        for d in search_dirs:
            if not d or not os.path.exists(d): 
                continue
            for c in candidates:
                p = os.path.join(d, c)
                if os.path.exists(p):
                    return p
            # Try a case-insensitive directory scan
            try:
                files = os.listdir(d)
                for f in files:
                    if f.lower() in [c.lower() for c in candidates]:
                        return os.path.join(d, f)
            except:
                pass
                
        return None

    def load_mob_mapping(self, csv_path):
        mapping = {}
        if os.path.exists(csv_path):
            try:
                with open(csv_path, "r", encoding="utf-8") as f:
                    for line in f:
                        parts = line.strip().split(",")
                        if len(parts) >= 2:
                            internal_name = parts[0].split(" is ")[0].strip()
                            display_name = parts[1].strip().lower().rstrip('"')
                            cleaned_name = ''.join(c for c in display_name if c.isalnum() or c.isspace() or c == "'" or c == "-")
                            
                            bgf_name = parts[2].strip() if len(parts) > 2 else ""
                            target_name = bgf_name if bgf_name else internal_name
                            
                            if cleaned_name:
                                mapping[cleaned_name] = target_name
                                no_spaces = cleaned_name.replace(" ", "")
                                if no_spaces not in mapping:
                                    mapping[no_spaces] = target_name
                                    
                                if cleaned_name.startswith("the "):
                                    mapping[cleaned_name[4:]] = target_name
                                    no_spaces_the = cleaned_name[4:].replace(" ", "")
                                    if no_spaces_the not in mapping:
                                        mapping[no_spaces_the] = target_name
                                        
                                if cleaned_name.startswith("a "):
                                    mapping[cleaned_name[2:]] = target_name
                                    no_spaces_a = cleaned_name[2:].replace(" ", "")
                                    if no_spaces_a not in mapping:
                                        mapping[no_spaces_a] = target_name
            except Exception as e:
                print(f"BGF ERROR: Could not read moblist: {e}")
        self.mob_mapping = mapping
        return mapping


def resolve_bgf_frame_index(pose: int, angle: int, num_frames: int, num_groups: int = 1) -> int:
    """
    Deterministically computes the exact BGF frame index given pose and angle.
    Handles:
      1. Direction-Major / Group-Major (e.g. Baby Spider, Spider, Centipede):
         num_groups == 6 or (num_frames % 6 == 0 and num_frames in (12, 18, 24) and num_groups > 1)
         Frame index = (angle * poses_per_angle) + pose
      2. Angle-Major (e.g. Avar, Orc, Skeleton, Humanoid, Zombie):
         num_frames >= 6
         Frame index = (pose * 6) + angle
      3. Non-directional / Props / Single-angle (<6 frames):
         Frame index = angle or pose
    """
    if num_frames <= 0:
        return 0
    if num_frames < 6:
        return min(max(0, max(pose, angle)), num_frames - 1)

    # Direction-Major (6 directional groups)
    if num_groups == 6 or (num_frames in (12, 18, 24) and num_groups > 1):
        poses_per_angle = max(1, num_frames // 6)
        clamped_pose = max(0, min(pose, poses_per_angle - 1))
        clamped_angle = max(0, min(angle, 5))
        idx = (clamped_angle * poses_per_angle) + clamped_pose
        return min(idx, num_frames - 1)

    # Standard Angle-Major (6 angles per pose)
    max_pose = max(0, (num_frames // 6) - 1)
    clamped_pose = max(0, min(pose, max_pose))
    clamped_angle = max(0, min(angle, 5))
    idx = (clamped_pose * 6) + clamped_angle
    return min(idx, num_frames - 1)


def frame_index_to_pose_angle(index: int, num_frames: int, num_groups: int = 1):
    """
    Converts a flat frame index into (pose, angle) based on BGF layout scheme.
    """
    if num_frames <= 0:
        return 0, 0
    if num_frames < 6:
        return 0, min(max(0, index), num_frames - 1)

    clamped_idx = max(0, min(index, num_frames - 1))
    if num_groups == 6 or (num_frames in (12, 18, 24) and num_groups > 1):
        poses_per_angle = max(1, num_frames // 6)
        angle = clamped_idx // poses_per_angle
        pose = clamped_idx % poses_per_angle
        return pose, angle
    else:
        pose = clamped_idx // 6
        angle = clamped_idx % 6
        return pose, angle

