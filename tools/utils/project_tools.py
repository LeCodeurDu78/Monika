"""Creation of complete projects (local folder + GitHub repository + Obsidian vault)."""

import os
import subprocess
from datetime import datetime
from core.settings import settings

CODE_BASE_DIR = str(settings.CODE_BASE_DIR)
OBSIDIAN_BASE_DIR = str(settings.OBSIDIAN_BASE_DIR)


def create_full_project(project_name: str, private: bool = True) -> str:
    """Create a complete project: local folder with an initialised git repository, GitHub repository
    (private by default) synchronised through `gh`, and an associated Obsidian note in the vault.
    """
    clean_name = project_name.strip().replace(" ", "-").lower()
    project_path = os.path.join(CODE_BASE_DIR, clean_name)
    obsidian_path = os.path.join(OBSIDIAN_BASE_DIR, clean_name)

    results = []

    try:
        if os.path.exists(project_path):
            return f"Le dossier '{project_path}' existe déjà."

        os.makedirs(project_path, exist_ok=True)
        results.append(f"📁 Dossier local créé : `{project_path}`")

        readme_path = os.path.join(project_path, "README.md")
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(f"# {project_name}\n\nProjet créé automatiquement par Monika.")

        subprocess.run(["git", "init"], cwd=project_path, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "add", "."], cwd=project_path, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit par Monika"],
            cwd=project_path,
            check=True,
            stdout=subprocess.DEVNULL,
        )

        visibility_flag = "--private" if private else "--public"
        cmd = ["gh", "repo", "create", clean_name, visibility_flag, "--source", project_path, "--push"]

        gh_result = subprocess.run(cmd, capture_output=True, text=True)
        if gh_result.returncode == 0:
            results.append(f"🐙 Dépôt GitHub créé et synchronisé ({'Privé' if private else 'Public'}).")
        else:
            results.append(f"⚠️ Erreur lors de la création du repo GitHub : {gh_result.stderr.strip()}")

        os.makedirs(obsidian_path, exist_ok=True)
        vault_note = os.path.join(obsidian_path, f"{clean_name}-Notes.md")
        with open(vault_note, "w", encoding="utf-8") as f:
            f.write(
                f"# Notes de projet : {project_name}\n\n- **Chemin du code** : `{project_path}`\n- **Date de création** : {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n## Idées & Todos\n- [ ] "
            )

        results.append(f"📓 Vault Obsidian initialisé : `{obsidian_path}`")

        return "\n".join(results)

    except Exception as e:
        return f"Erreur lors de la création du projet : {str(e)}"
