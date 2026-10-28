import cv2
import time
import os
import threading
from collections import deque
import numpy as np
import uuid
import logging
import copy
from datetime import datetime
from PIL import Image, ImageFilter

try:
    import winsound
    HAS_WINSOUND = True
except ImportError:
    HAS_WINSOUND = False

from meme_manager import MemeManager
from upload_manager import UploadManager
from filter_engine import apply_filter, get_filter_name, FILTER_COUNT


OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "riwayat")
CAMERA_IDLE_SECONDS = 120

# GIF memakai frame yang memang sudah dibaca saat countdown, tanpa tambahan
# waktu capture. Frame diperkecil untuk menjaga penggunaan memori tetap rendah.
PRE_ROLL_FRAMES = 16
PRE_ROLL_INTERVAL = 3
MOTION_FRAME_MS = 80
MOTION_MAX_WIDTH = 640
MOTION_MAX_HEIGHT = 360


def _crop_to_slot(photo, width, height):
    """Isi slot penuh dengan crop tengah; gerakan memakai bingkai penuh."""
    target_ratio = width / height
    image_ratio = photo.width / photo.height
    if image_ratio > target_ratio:
        crop_width = max(1, int(photo.height * target_ratio))
        offset = (photo.width - crop_width) // 2
        photo = photo.crop((offset, 0, offset + crop_width, photo.height))
    else:
        crop_height = max(1, int(photo.width / target_ratio))
        offset = (photo.height - crop_height) // 2
        photo = photo.crop((0, offset, photo.width, offset + crop_height))
    return photo.resize((width, height), Image.LANCZOS)


def _shrink_motion_frame(frame):
    height, width = frame.shape[:2]
    scale = min(1.0, MOTION_MAX_WIDTH / width, MOTION_MAX_HEIGHT / height)
    if scale >= 1.0:
        return frame.copy()
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return cv2.resize(frame, size, interpolation=cv2.INTER_AREA)


def create_output_path(extension=".jpg"):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    unique_id = uuid.uuid4().hex[:8]
    return os.path.join(OUTPUT_DIR, f"hasil_photobooth_{timestamp}_{unique_id}{extension}")


def get_group_guide_box(frame_w, frame_h, margin_ratio=0.035):
    """Gunakan hampir seluruh bidang kamera agar foto ramai tetap masuk."""
    margin_x = int(frame_w * margin_ratio)
    margin_y = int(frame_h * margin_ratio)
    return margin_x, margin_y, frame_w - margin_x, frame_h - margin_y


def fit_group_photo(photo, width, height):
    """Masukkan seluruh foto ke slot dengan latar blur sebagai pengisi rasio."""
    background = photo.copy()
    target_ratio = width / height
    image_ratio = background.width / background.height

    if image_ratio > target_ratio:
        crop_width = int(background.height * target_ratio)
        offset = (background.width - crop_width) // 2
        background = background.crop((offset, 0, offset + crop_width, background.height))
    else:
        crop_height = int(background.width / target_ratio)
        offset = (background.height - crop_height) // 2
        background = background.crop((0, offset, background.width, offset + crop_height))

    background = background.resize((width, height), Image.LANCZOS)
    background = background.filter(ImageFilter.GaussianBlur(radius=max(8, min(width, height) // 35)))

    foreground = photo.copy()
    foreground.thumbnail((width, height), Image.LANCZOS)
    paste_x = (width - foreground.width) // 2
    paste_y = (height - foreground.height) // 2
    background.paste(foreground, (paste_x, paste_y))
    return background


def list_available_cameras(max_tests=5):
    available_cameras = []
    for i in range(max_tests):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if cap.isOpened():
            available_cameras.append(f"Camera {i}")
        cap.release()
    if not available_cameras:
        available_cameras.append("Camera 0 (Default)")
    return available_cameras


def _beep(freq=880, duration_ms=120):
    """Beep via winsound jika tersedia, jika tidak silent."""
    if HAS_WINSOUND:
        try:
            winsound.Beep(freq, duration_ms)
        except Exception:
            pass


class PhotoBoothEngine:
    def __init__(self, config, camera_index=0, output_filename=None, cancel_event=None):
        self.config = config
        self.camera_index = camera_index
        self.output_filename = output_filename or create_output_path()
        os.makedirs(os.path.dirname(os.path.abspath(self.output_filename)), exist_ok=True)
        self.photos = []
        self.photo_boxes = []
        self.motion_clips = []
        self.cancel_event = cancel_event or threading.Event()
        self.meme_manager = MemeManager()
        self.upload_manager = UploadManager()

        # Filter state
        self.filter_idx = 0  # FILTER_NORMAL

        # Ref variables (computed in capture)
        self._x1 = self._y1 = self._x2 = self._y2 = 0
        self._frame_w = self._frame_h = 0

    # ─────────────────────────────────────────────────────────────
    # CAPTURE
    # ─────────────────────────────────────────────────────────────

    def capture(self, photo_index=None):
        if photo_index is not None and not 0 <= photo_index < len(self.photos):
            raise IndexError("Nomor foto yang akan diulang tidak valid.")
        cap = None
        if photo_index is None:
            self.photos = []
            self.photo_boxes = []
            self.motion_clips = []
        try:
            if self.cancel_event.is_set():
                return False
            cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap.release()
                cap = cv2.VideoCapture(self.camera_index)
            if not cap.isOpened():
                raise RuntimeError("Kamera tidak dapat diakses. Periksa koneksi kamera.")
            return self._capture(cap, photo_index=photo_index)
        finally:
            if cap is not None:
                cap.release()
            cv2.destroyAllWindows()

    def _capture(self, cap, photo_index=None):
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

        photo_count = self.config["photo_count"] if photo_index is None else 1

        print("Tekan SPASI untuk mulai | F = filter | M = sticker mode | ESC = batal")
        waiting_since = time.monotonic()

        # ── WAITING LOOP ──────────────────────────────────────────
        while True:
            if self.cancel_event.is_set():
                return False
            if time.monotonic() - waiting_since >= CAMERA_IDLE_SECONDS:
                logging.info("Sesi kamera dibatalkan setelah %s detik tidak digunakan", CAMERA_IDLE_SECONDS)
                return False
            ret, frame = cap.read()
            if not ret or frame is None or frame.size == 0:
                logging.warning("Kamera terputus saat menunggu; kembali ke halaman awal")
                return False

            frame = cv2.flip(frame, 1)
            h, w = frame.shape[:2]
            self._frame_w, self._frame_h = w, h

            # Guide lebar untuk foto grup. Seluruh isi kotak akan masuk hasil.
            x1, y1, x2, y2 = get_group_guide_box(w, h)
            self._x1, self._y1, self._x2, self._y2 = x1, y1, x2, y2

            # Apply sticker + filter
            display_frame = self.meme_manager.apply_meme(frame.copy())
            display_frame = apply_filter(display_frame, self.filter_idx)

            # Overlay guide
            cv2.rectangle(display_frame, (x1, y1), (x2, y2), (255, 255, 255), 2)
            cv2.putText(display_frame, "Area Foto Lebar", (x1, max(28, y1 - 10)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

            # HUD
            filter_name = get_filter_name(self.filter_idx)
            sticker_status = self.meme_manager.current_sticker_name() if self.meme_manager.meme_mode else "OFF"
            hud = f"SPASI=Mulai | F=Filter:[{filter_name}] | M=Sticker:{sticker_status} | ESC=Batal"
            if photo_index is not None:
                cv2.putText(display_frame, f"Ulang Foto {photo_index + 1} - SPASI=Mulai",
                            (14, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 100), 2)
            cv2.putText(display_frame, hud, (14, h - 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.52, (200, 255, 200), 1)

            cv2.imshow("Photo Booth", display_frame)

            key = cv2.waitKey(1) & 0xFF
            if key != 255:
                waiting_since = time.monotonic()
            if key == 32:  # SPACE
                break
            elif key == 27:  # ESC
                return False
            elif key in (ord('m'), ord('M')):
                self.meme_manager.toggle_mode()
            elif key in (ord('f'), ord('F')):
                self.filter_idx = (self.filter_idx + 1) % FILTER_COUNT
                print(f"Filter: {get_filter_name(self.filter_idx)}")
            elif key in (ord('s'), ord('S')):
                self.meme_manager.cycle_sticker()

        # ── PHOTO SESSION ─────────────────────────────────────────
        captured_photos = []
        captured_boxes = []
        captured_motion = []
        x1, y1, x2, y2 = self._x1, self._y1, self._x2, self._y2

        for i in range(photo_count):
            # Countdown
            delay = 6
            start_time = time.monotonic()
            pre_roll = deque(maxlen=PRE_ROLL_FRAMES)
            frame_number = 0
            while time.monotonic() - start_time < delay:
                if self.cancel_event.is_set():
                    return False
                ret, frame = cap.read()
                if not ret or frame is None or frame.size == 0:
                    raise RuntimeError("Koneksi kamera terputus saat mengambil foto. Silakan coba lagi.")
                frame = cv2.flip(frame, 1)
                h, w = frame.shape[:2]
                display_frame = self.meme_manager.apply_meme(frame.copy())
                display_frame = apply_filter(display_frame, self.filter_idx)
                if frame_number % PRE_ROLL_INTERVAL == 0:
                    pre_roll.append(_shrink_motion_frame(display_frame))
                frame_number += 1

                cv2.rectangle(display_frame, (x1, y1), (x2, y2), (255, 255, 255), 2)

                # Countdown number — pulse animation
                elapsed = time.monotonic() - start_time
                remaining_int = int(delay - elapsed) + 1
                pulse = 3.0 + 1.5 * abs(np.sin(elapsed * np.pi))  # oscillates 3→4.5→3
                font_scale = pulse
                thickness = max(4, int(pulse * 1.2))
                text = str(remaining_int)
                (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)
                tx = (w - tw) // 2
                ty = (h + th) // 2

                # Shadow
                cv2.putText(display_frame, text, (tx + 3, ty + 3),
                            cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 0), thickness + 2)
                cv2.putText(display_frame, text, (tx, ty),
                            cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 80, 255), thickness)

                # Photo counter
                counter_txt = f"Foto {i + 1}/{photo_count}" if photo_index is None else f"Ulang Foto {photo_index + 1}"
                cv2.putText(display_frame, counter_txt, (14, 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 100), 2)

                cv2.imshow("Photo Booth", display_frame)
                if cv2.waitKey(1) & 0xFF == 27:
                    return False

                # Beep on last 3 ticks (once per second)
                if elapsed < 0.05 or (elapsed > 1.0 and elapsed < 1.05) or (elapsed > 2.0 and elapsed < 2.05):
                    threading.Thread(target=_beep, daemon=True).start()

            # Ambil frame
            if self.cancel_event.is_set():
                return False
            ret, frame = cap.read()
            if not ret or frame is None or frame.size == 0:
                raise RuntimeError("Kamera gagal mengambil foto. Silakan coba lagi.")
            if ret:
                frame = cv2.flip(frame, 1)

                # Apply sticker to captured photo
                if self.meme_manager.meme_mode:
                    frame = self.meme_manager.apply_meme(frame)

                # Apply filter to captured photo
                frame = apply_filter(frame, self.filter_idx)
                captured_photos.append(frame)
                captured_boxes.append(get_group_guide_box(frame.shape[1], frame.shape[0]))

                # Flash effect
                flash = np.ones_like(frame) * 255
                cv2.imshow("Photo Booth", flash)
                cv2.waitKey(80)

                # Beep capture
                threading.Thread(target=lambda: _beep(1200, 80), daemon=True).start()

                # Show captured frame briefly
                cv2.imshow("Photo Booth", frame)
                cv2.waitKey(400)

                captured_motion.append(self._collect_motion_frames(pre_roll, frame))

        if len(captured_photos) != photo_count:
            return False
        if photo_index is None:
            self.photos = captured_photos
            self.photo_boxes = captured_boxes
            self.motion_clips = captured_motion
        else:
            self.photos[photo_index] = captured_photos[0]
            self.photo_boxes[photo_index] = captured_boxes[0]
            self.motion_clips[photo_index] = captured_motion[0]
        return True

    def _collect_motion_frames(self, pre_roll, shutter_frame):
        """Gabungkan gerakan sebelum jepretan dengan foto hasil jepretan."""
        clip = list(pre_roll) + [_shrink_motion_frame(shutter_frame)]
        return clip

    def retake_photo(self, photo_index):
        """Ambil satu foto pada salinan sesi; hasil lama tetap tersedia saat batal."""
        if not 0 <= photo_index < len(self.photos):
            raise IndexError("Nomor foto yang akan diulang tidak valid.")
        candidate = copy.copy(self)
        candidate.photos = self.photos.copy()
        candidate.photo_boxes = [self._photo_box(i) for i in range(len(self.photos))]
        candidate.motion_clips = [list(clip) for clip in getattr(self, "motion_clips", [])]
        # Sesi lama atau klip yang belum terekam diisi slot kosong agar retake tetap valid.
        candidate.motion_clips.extend([] for _ in range(len(self.photos) - len(candidate.motion_clips)))
        candidate.meme_manager = copy.copy(self.meme_manager)
        candidate.output_filename = create_output_path()
        if candidate.capture(photo_index=photo_index):
            return candidate
        return None

    def _photo_box(self, index):
        boxes = getattr(self, "photo_boxes", [])
        if index < len(boxes):
            return boxes[index]
        return self._x1, self._y1, self._x2, self._y2

    def get_photo_preview(self, index):
        """Foto tanpa bingkai, dengan area crop yang sesuai saat pengambilan."""
        photo = Image.fromarray(cv2.cvtColor(self.photos[index], cv2.COLOR_BGR2RGB))
        x1, y1, x2, y2 = self._photo_box(index)
        return photo.crop((max(0, x1), max(0, y1), min(photo.width, x2), min(photo.height, y2)))

    # ─────────────────────────────────────────────────────────────
    # PROCESS (composite + GIF + upload)
    # ─────────────────────────────────────────────────────────────

    def process(self, on_complete=None):
        """Simpan JPG + GIF otomatis sebelum preview atau pilihan pengguna."""
        if not self.photos:
            return

        canvas_w = self.config["width"]
        canvas_h = self.config["height"]
        layout = self.config["layout"]

        pil_photos = []
        for i in range(min(len(self.photos), len(layout))):
            pil_photos.append(self.get_photo_preview(i))

        # Overlay
        overlay = None
        overlay_path = self.config.get("overlay_full_path")
        if overlay_path and os.path.exists(overlay_path):
            with Image.open(overlay_path) as source:
                overlay = source.convert("RGBA")
            if overlay.size != (canvas_w, canvas_h):
                overlay = overlay.resize((canvas_w, canvas_h), Image.LANCZOS)

        # Save JPG
        final_image = self._compose_photos(pil_photos, overlay)
        final_image.save(self.output_filename, quality=95, subsampling=0)
        print(f"Komposit disimpan: {self.output_filename}")

        # Save GIF/Boomerang
        gif_path = None
        if pil_photos:
            gif_path = self._save_motion_gif(overlay) or self._save_gif(pil_photos, overlay=overlay)

        return self.output_filename, gif_path

    def _motion_clips_for_gif(self):
        """Klip gerakan yang layak dipakai; None kalau tidak ada."""
        clips = [clip for clip in getattr(self, "motion_clips", []) if clip]
        if not clips or max(len(clip) for clip in clips) < 2:
            return None
        return clips

    def _save_motion_gif(self, overlay=None):
        """GIF dari gerakan kamera sesaat sebelum jepretan, satu klip per slot."""
        clips = self._motion_clips_for_gif()
        if not clips:
            return None

        gif_path = os.path.splitext(self.output_filename)[0] + ".gif"
        size = self._get_gif_size()

        try:
            frame_count = max(len(clip) for clip in clips)
            layout = self.config["layout"]
            frames = [
                self._compose_motion_frame(clips, index, layout, size, overlay)
                for index in range(frame_count)
            ]
            boomerang = frames + list(reversed(frames))[1:]
            boomerang[0].save(
                gif_path,
                save_all=True,
                append_images=boomerang[1:],
                duration=MOTION_FRAME_MS,
                loop=0,
                optimize=False,
                disposal=2,
            )
            print(f"GIF gerakan disimpan: {gif_path} ({len(boomerang)} frame)")
            return gif_path
        except Exception as e:
            logging.exception("GIF gerakan gagal disimpan")
            print(f"GIF gerakan gagal disimpan: {e}")
            return None

    def _compose_motion_frame(self, clips, index, layout, size, overlay):
        """Satu bingkai: setiap slot memakai frame gerakan dari klipnya sendiri."""
        canvas_w, canvas_h = self.config["width"], self.config["height"]
        scale = min(1, size[0] / canvas_w, size[1] / canvas_h)
        canvas_size = (max(1, round(canvas_w * scale)), max(1, round(canvas_h * scale)))
        sx, sy = canvas_size[0] / canvas_w, canvas_size[1] / canvas_h

        image = Image.new("RGB", canvas_size, "white")
        for slot, (x, y, w, h) in enumerate(layout):
            if slot >= len(clips):
                break
            clip = clips[slot]
            frame = clip[min(index, len(clip) - 1)]
            left, top = round(x * sx), round(y * sy)
            width = max(1, round((x + w) * sx) - left)
            height = max(1, round((y + h) * sy) - top)
            photo = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            image.paste(_crop_to_slot(photo, width, height), (left, top))

        if overlay is not None:
            frame_overlay = overlay if overlay.size == canvas_size else overlay.resize(canvas_size, Image.LANCZOS)
            image.paste(frame_overlay, (0, 0), frame_overlay)
        return image

    def _compose_photos(self, photos, overlay=None, max_size=None):
        """Susun foto pada slot template, lalu pasang overlay di atasnya."""
        canvas_w, canvas_h = self.config["width"], self.config["height"]
        scale = min(1, max_size[0] / canvas_w, max_size[1] / canvas_h) if max_size else 1
        size = (max(1, round(canvas_w * scale)), max(1, round(canvas_h * scale)))
        image = Image.new("RGB", size, "white")
        sx, sy = size[0] / canvas_w, size[1] / canvas_h
        for photo, (x, y, w, h) in zip(photos, self.config["layout"]):
            left, top = round(x * sx), round(y * sy)
            width = max(1, round((x + w) * sx) - left)
            height = max(1, round((y + h) * sy) - top)
            image.paste(fit_group_photo(photo, width, height), (left, top))
        if overlay is not None:
            frame = overlay if overlay.size == size else overlay.resize(size, Image.LANCZOS)
            image.paste(frame, (0, 0), frame)
        return image

    def _get_gif_size(self, thumb_size=None):
        if thumb_size is not None:
            return thumb_size
        configured_size = self.config.get("gif_size")
        if isinstance(configured_size, (list, tuple)) and len(configured_size) == 2:
            width, height = configured_size
            if all(isinstance(value, int) and not isinstance(value, bool) and value > 0
                   for value in (width, height)):
                return width, height
        return 480, 720

    def _save_gif(self, pil_photos, thumb_size=None, overlay=None):
        """Buat GIF boomerang dengan seluruh bingkai dan layout template."""
        gif_path = os.path.splitext(self.output_filename)[0] + ".gif"
        thumb_size = self._get_gif_size(thumb_size)

        try:
            # Setiap slot berganti foto; ukuran slot boleh berbeda-beda.
            frames = [
                self._compose_photos(pil_photos[i:] + pil_photos[:i], overlay, thumb_size)
                for i in range(len(pil_photos))
            ]
            boomerang = frames + list(reversed(frames))
            boomerang[0].save(
                gif_path,
                save_all=True,
                append_images=boomerang[1:],
                duration=220,
                loop=0,
                optimize=False,
                disposal=2,
            )
            print(f"GIF disimpan: {gif_path}")
            return gif_path
        except Exception as e:
            print(f"GIF gagal disimpan: {e}")
            return None

    # ─────────────────────────────────────────────────────────────
    # UPLOAD (thread terpisah)
    # ─────────────────────────────────────────────────────────────

    def upload_async(self, filepath, on_done=None, gif_path=None):
        """Upload file, atau ZIP foto + GIF jika gif_path diberikan."""
        def _worker():
            link = None
            try:
                upload_path = self.upload_manager.create_bundle(filepath, gif_path) if gif_path else filepath
                link = self.upload_manager.upload_file(upload_path)
            except Exception:
                logging.exception("Gagal menyiapkan atau mengupload hasil foto")
            if on_done:
                on_done(link)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        return t
