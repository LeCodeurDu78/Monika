import os
import platform
import psutil
import subprocess
from pathlib import Path
from datetime import datetime


def open_application(app_name: str) -> str:
    """Open an application detached from the terminal."""
    try:
        app_clean = app_name.lower().strip()
        if platform.system() == "Windows":
            subprocess.Popen(f"start {app_clean}", shell=True)
        else:
            subprocess.Popen(
                [app_clean],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        return f"Application '{app_name}' lancée avec succès."
    except Exception as e:
        return f"Erreur lors du lancement de {app_name} : {str(e)}"


def system_control(action: str, value: int = 5, filename: str = None) -> str:
    """Manage volume, media and screenshots in a cross-platform way."""
    try:
        is_windows = platform.system() == "Windows"

        if action in ("volume_up", "volume_down"):
            if not is_windows:
                cmd = ["pamixer", "-i" if action == "volume_up" else "-d", str(value)]
                subprocess.run(cmd, check=True)
                return f"Volume modifié de {value}%."
            return "Contrôle du volume non supporté nativement sous Windows via ce script."

        elif action == "screenshot":
            images_dir = Path.home() / "Pictures" / "Screenshots"
            images_dir.mkdir(parents=True, exist_ok=True)

            if filename:
                clean_name = os.path.basename(filename.strip())
                if not clean_name.endswith(".png"):
                    clean_name += ".png"
            else:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                clean_name = f"screenshot_{timestamp}.png"

            path = images_dir / clean_name

            if is_windows:
                from PIL import ImageGrab

                img = ImageGrab.grab()
                img.save(path)
            else:
                subprocess.run(["grim", str(path)], check=True)

            return f"Capture d'écran enregistrée sous '{clean_name}' dans {images_dir}."

        elif action == "media_toggle":
            if not is_windows:
                subprocess.run(["playerctl", "play-pause"], check=True)
                return "Lecture/Pause basculée."
            return "Contrôle média non supporté nativement sous Windows via ce script."

        return "Action système non reconnue."
    except Exception as e:
        return f"Erreur lors de l'exécution : {str(e)}"


def get_system_stats() -> str:
    """Get real-time system usage metrics (CPU, RAM, disk, battery)."""
    try:
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage(str(Path.home()))

        stats = [
            f"CPU : {cpu}%",
            f"RAM : {mem.percent}% utilisée ({mem.used // (1024**2)} Mo / {mem.total // (1024**2)} Mo)",
            f"Disque : {disk.percent}% utilisé ({disk.used // (1024**3)} Go / {disk.total // (1024**3)} Go)",
        ]

        battery = psutil.sensors_battery()
        if battery is not None:
            etat = "en charge" if battery.power_plugged else "sur batterie"
            stats.append(f"Batterie : {battery.percent}% ({etat})")

        return " | ".join(stats)
    except Exception as e:
        return f"Erreur lors de la récupération des statistiques système : {str(e)}"
