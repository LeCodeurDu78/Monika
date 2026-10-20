"""Management and organisation of local files (listing, creating a folder, moving)."""

import os


def manage_files(action: str, path: str, target_folder: str = "") -> str:
    """Manage and organise local files."""
    try:
        path = os.path.expanduser(path)

        if action == "list":
            files = sorted(os.listdir(path))
            names = [f + "/" if os.path.isdir(os.path.join(path, f)) else f for f in files[:200]]
            more = f" (+{len(files) - 200} autres)" if len(files) > 200 else ""
            return f"Contenu de '{path}' : {', '.join(names)}{more}"

        elif action == "create_dir":
            os.makedirs(path, exist_ok=True)
            return f"Dossier '{path}' créé avec succès."

        elif action == "move" and target_folder:
            target_folder = os.path.expanduser(target_folder)
            os.makedirs(target_folder, exist_ok=True)
            file_name = os.path.basename(path)
            destination = os.path.join(target_folder, file_name)
            os.rename(path, destination)
            return f"Fichier déplacé de '{path}' vers '{destination}'."

        return "Action non reconnue ou paramètres manquants."
    except Exception as e:
        return f"Erreur gestion de fichiers : {str(e)}"
