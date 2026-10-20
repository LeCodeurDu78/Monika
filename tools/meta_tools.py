"""Lets Monika create new Python tools on the fly, and patch existing files of the project."""

import ast
import io
import os
import shutil
import sys
import traceback

CUSTOM_TOOLS_DIR = os.path.join(os.path.dirname(__file__), "custom")
os.makedirs(CUSTOM_TOOLS_DIR, exist_ok=True)

if CUSTOM_TOOLS_DIR not in sys.path:
    sys.path.append(CUSTOM_TOOLS_DIR)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MAX_SELF_CORRECTION_ATTEMPTS = 3

CORRECTION_SYSTEM_PROMPT = (
    "Tu es un correcteur de code Python expert. On te donne un outil Python "
    "destiné à être appelé automatiquement par un agent IA (Monika), qui a "
    "échoué à la validation, avec le message d'erreur exact obtenu.\n\n"
    "Corrige le code pour que l'erreur disparaisse, en conservant son "
    "objectif fonctionnel et sa signature autant que possible.\n\n"
    "Règles strictes, à respecter impérativement :\n"
    "1. Le code doit définir une fonction Python nommée EXACTEMENT "
    "'{tool_name}' (au niveau module, pas imbriquée).\n"
    "2. Le code doit être autonome (tous les imports nécessaires inclus).\n"
    "3. Réponds UNIQUEMENT avec le code Python corrigé complet. Aucune "
    "explication, aucun texte avant ou après, aucune balise markdown/```."
)

PATCH_SYSTEM_PROMPT = (
    "Tu es un ingénieur logiciel Python expert qui modifie un fichier existant "
    "d'un projet en production (l'agent IA Monika) suite à une instruction précise.\n\n"
    "Règles strictes, à respecter impérativement :\n"
    "1. Réponds UNIQUEMENT avec le contenu INTÉGRAL du fichier corrigé "
    "(pas un diff, pas un extrait) : tout ce qui n'est pas concerné par "
    "l'instruction doit rester identique au fichier d'origine.\n"
    "2. Aucune explication, aucun texte avant ou après, aucune balise "
    "markdown/```.\n"
    "3. Ne casse pas les imports, la signature des fonctions déjà appelées "
    "ailleurs dans le projet, ni le comportement non concerné par la demande.\n"
    "4. Le code doit rester syntaxiquement valide et autonome (imports inclus)."
)

PATCH_CORRECTION_SYSTEM_PROMPT = (
    "Tu es un correcteur de code Python expert. Le patch que tu as proposé pour "
    "un fichier d'un projet en production (l'agent IA Monika) a échoué à la "
    "validation ou aux tests, avec le message d'erreur exact obtenu.\n\n"
    "Corrige le contenu du fichier pour que l'erreur disparaisse, en conservant "
    "l'objectif de l'instruction d'origine et tout ce qui n'est pas concerné.\n\n"
    "Règles strictes, à respecter impérativement :\n"
    "1. Réponds UNIQUEMENT avec le contenu INTÉGRAL du fichier corrigé "
    "(pas un diff, pas un extrait).\n"
    "2. Aucune explication, aucun texte avant ou après, aucune balise "
    "markdown/```."
)


def _strip_code_fences(text: str) -> str:
    """Remove any ```python ... ``` fences the model added despite the instruction."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines)
    return stripped.strip()


def _defines_function(tree: ast.AST, tool_name: str) -> bool:
    """Check that the code defines, at module level, a (sync or async) function named `tool_name`."""
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == tool_name
        for node in ast.iter_child_nodes(tree)
    )


def _pyflakes_check(python_code: str, filename: str) -> str:
    """Optional static analysis (undefined variables, dead imports, etc.), without running the code."""
    try:
        from pyflakes.api import check
        from pyflakes.reporter import Reporter
    except ImportError:
        return ""

    out, err = io.StringIO(), io.StringIO()
    check(python_code, filename, reporter=Reporter(out, err))
    report = (out.getvalue() + err.getvalue()).strip()
    return report


def _validate_tool_code(tool_name: str, python_code: str) -> str | None:
    """Validate the code of a NEW standalone tool without actually running it (the function is not called)."""
    filename = f"{tool_name}.py"

    try:
        tree = ast.parse(python_code, filename=filename)
    except SyntaxError as e:
        return f"Erreur de syntaxe : {e.msg} (ligne {e.lineno}, colonne {e.offset})"

    if not _defines_function(tree, tool_name):
        return (
            f"Le code ne définit aucune fonction nommée exactement '{tool_name}' "
            "au niveau du module (vérifie l'orthographe et l'indentation)."
        )

    flake_report = _pyflakes_check(python_code, filename)
    if flake_report:
        return f"Analyse statique (pyflakes) a détecté des problèmes :\n{flake_report}"

    module_namespace = {"__name__": f"tools.custom._validate_{tool_name}", "__file__": filename}
    try:
        code_obj = compile(python_code, filename, "exec")
        exec(code_obj, module_namespace)
    except Exception:
        tb = traceback.format_exc(limit=5)
        return f"Erreur au chargement du module :\n{tb}"

    func = module_namespace.get(tool_name)
    if not callable(func):
        return f"'{tool_name}' est défini mais n'est pas une fonction appelable."

    return None


def _validate_file_patch(abs_path: str, python_code: str) -> str | None:
    """Validate the content of a patched file."""
    filename = os.path.basename(abs_path)

    try:
        ast.parse(python_code, filename=filename)
    except SyntaxError as e:
        return f"Erreur de syntaxe : {e.msg} (ligne {e.lineno}, colonne {e.offset})"

    flake_report = _pyflakes_check(python_code, filename)
    if flake_report:
        return f"Analyse statique (pyflakes) a détecté des problèmes :\n{flake_report}"

    return None


def _self_correct(system_prompt: str, user_context: str, code: str, error: str) -> str | None:
    """Ask the model to fix faulty code."""
    from config import client, MODEL_NAME

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        f"{user_context}\n\n"
                        f"Code actuel (défaillant) :\n{code}\n\n"
                        f"Erreur obtenue :\n{error}"
                    ),
                },
            ],
        )
        corrected = response.choices[0].message.content or ""
        corrected = _strip_code_fences(corrected)
        return corrected or None
    except Exception:
        return None


def create_custom_tool(tool_name: str, python_code: str, description: str) -> str:
    """Create and register a new reusable Python tool for Monika."""
    from tools.registry import TOOLS_SCHEMA, sync_custom_tools

    current_code = python_code
    attempts_log = []

    for attempt in range(1, MAX_SELF_CORRECTION_ATTEMPTS + 1):
        error = _validate_tool_code(tool_name, current_code)

        if error is None:
            try:
                file_path = os.path.join(CUSTOM_TOOLS_DIR, f"{tool_name}.py")
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(f'"""\n{description}\n"""\n\n')
                    f.write(current_code)

                sync_custom_tools(TOOLS_SCHEMA)

                if attempt == 1:
                    return f"✅ Outil '{tool_name}' créé, enregistré et immédiatement disponible !"
                return (
                    f"✅ Outil '{tool_name}' créé et enregistré après auto-correction "
                    f"({attempt - 1} correction(s) automatique(s) suite à : "
                    f"{attempts_log[-1].splitlines()[0]})."
                )
            except Exception as e:
                return f"❌ Échec de l'écriture de l'outil validé : {str(e)}"

        attempts_log.append(error)
        print(
            f"🔧 [Auto-correction] Tentative {attempt}/{MAX_SELF_CORRECTION_ATTEMPTS} pour '{tool_name}' — erreur détectée :\n{error}"
        )

        if attempt == MAX_SELF_CORRECTION_ATTEMPTS:
            break

        corrected = _self_correct(
            CORRECTION_SYSTEM_PROMPT.format(tool_name=tool_name),
            f"Description de l'outil : {description}",
            current_code,
            error,
        )
        if not corrected:
            print(f"⚠️ [Auto-correction] Échec de l'appel de correction pour '{tool_name}', arrêt anticipé.")
            break
        current_code = corrected

    return (
        f"❌ Échec de la création de l'outil '{tool_name}' après {len(attempts_log)} tentative(s) "
        f"d'auto-correction. Rien n'a été enregistré (pour éviter un outil cassé). "
        f"Dernière erreur :\n{attempts_log[-1] if attempts_log else 'inconnue'}"
    )


def _resolve_project_path(file_path: str) -> str | None:
    """Resolve `file_path` to an absolute path, restricted to the project and to .py files."""
    abs_path = os.path.abspath(os.path.expanduser(file_path))
    if not abs_path.startswith(PROJECT_ROOT + os.sep):
        return None
    if not abs_path.endswith(".py"):
        return None
    if not os.path.isfile(abs_path):
        return None
    return abs_path


def _module_dotted_path(abs_path: str) -> str:
    """Convert the absolute path of a project .py file into an importable module path."""
    rel = os.path.relpath(abs_path, PROJECT_ROOT)
    without_ext = rel[:-3] if rel.endswith(".py") else rel
    return without_ext.replace(os.sep, ".")


def _find_existing_test(abs_path: str) -> str | None:
    """Look for a test file already associated with the patched file."""
    directory = os.path.dirname(abs_path)
    basename = os.path.splitext(os.path.basename(abs_path))[0]

    candidates = [
        os.path.join(directory, f"test_{basename}.py"),
        os.path.join(directory, "tests", f"test_{basename}.py"),
        os.path.join(PROJECT_ROOT, "tests", f"test_{basename}.py"),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


def _command_succeeded(run_script_output: str) -> bool:
    """Decide whether a command succeeded from run_script's text output."""
    return "Code de sortie : 0" in run_script_output


def _run_tests_for_file(abs_path: str) -> tuple[bool, str]:
    """Test the patched file before considering it valid."""
    from tools.system.terminal_tools import run_script

    existing_test = _find_existing_test(abs_path)
    if existing_test:
        output = run_script(f"python3 -m pytest '{existing_test}' -q", workdir=PROJECT_ROOT)
        return _command_succeeded(output), f"Tests existants ({existing_test}) :\n{output}"

    module_dotted = _module_dotted_path(abs_path)
    smoke_cmd = (
        "python3 -c \""
        f"import sys; sys.path.insert(0, r'{PROJECT_ROOT}'); "
        "import importlib, py_compile; "
        f"py_compile.compile(r'{abs_path}', doraise=True); "
        f"importlib.import_module('{module_dotted}'); "
        "print('SMOKE_TEST_OK')\""
    )
    output = run_script(smoke_cmd, workdir=PROJECT_ROOT)
    success = _command_succeeded(output) and "SMOKE_TEST_OK" in output
    note = (
        "⚠️ Aucun test dédié trouvé : smoke-test généré automatiquement "
        "(compilation + import du module dans un process isolé). Ce test "
        "ne couvre PAS la logique métier du fichier, seulement sa validité "
        "syntaxique et son importabilité.\n"
    )
    return success, note + output


def _generate_patch(abs_path: str, instruction: str, original_code: str) -> str | None:
    """Ask the model for a first draft of the patched file from the instruction."""
    from config import client, MODEL_NAME

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": PATCH_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Fichier à modifier : {abs_path}\n\n"
                        f"Instruction : {instruction}\n\n"
                        f"Contenu actuel du fichier :\n{original_code}"
                    ),
                },
            ],
        )
        content = response.choices[0].message.content or ""
        return _strip_code_fences(content) or None
    except Exception:
        return None


def patch_existing_file(file_path: str, instruction: str) -> str:
    """Patch an existing file of the project."""
    abs_path = _resolve_project_path(file_path)
    if abs_path is None:
        return (
            f"❌ Chemin refusé : '{file_path}' doit être un fichier .py existant "
            f"à l'intérieur du projet ({PROJECT_ROOT})."
        )

    with open(abs_path, "r", encoding="utf-8") as f:
        original_code = f.read()

    backup_path = abs_path + ".bak"
    shutil.copyfile(abs_path, backup_path)

    def _restore_backup():
        shutil.copyfile(backup_path, abs_path)

    current_code = _generate_patch(abs_path, instruction, original_code)
    if current_code is None:
        os.remove(backup_path)
        return "❌ Échec de la génération du patch (appel au modèle indisponible)."

    attempts_log = []

    for attempt in range(1, MAX_SELF_CORRECTION_ATTEMPTS + 1):
        error = _validate_file_patch(abs_path, current_code)

        if error is None:
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(current_code)

            tests_ok, tests_report = _run_tests_for_file(abs_path)

            if tests_ok:
                os.remove(backup_path)
                if attempt == 1:
                    return (
                        f"✅ Fichier '{os.path.relpath(abs_path, PROJECT_ROOT)}' patché et testé avec succès.\n"
                        f"{tests_report}"
                    )
                return (
                    f"✅ Fichier '{os.path.relpath(abs_path, PROJECT_ROOT)}' patché et testé avec succès "
                    f"après auto-correction ({attempt - 1} correction(s) automatique(s)).\n{tests_report}"
                )

            _restore_backup()
            error = f"Échec des tests après application du patch :\n{tests_report}"

        attempts_log.append(error)
        print(
            f"🔧 [Auto-correction] Tentative {attempt}/{MAX_SELF_CORRECTION_ATTEMPTS} pour "
            f"'{abs_path}' — erreur détectée :\n{error}"
        )

        if attempt == MAX_SELF_CORRECTION_ATTEMPTS:
            break

        corrected = _self_correct(
            PATCH_CORRECTION_SYSTEM_PROMPT,
            f"Fichier : {abs_path}\nInstruction d'origine : {instruction}",
            current_code,
            error,
        )
        if not corrected:
            print("⚠️ [Auto-correction] Échec de l'appel de correction, arrêt anticipé.")
            break
        current_code = corrected

    _restore_backup()
    os.remove(backup_path)

    return (
        f"❌ Échec du patch de '{os.path.relpath(abs_path, PROJECT_ROOT)}' après {len(attempts_log)} "
        f"tentative(s) d'auto-correction. Fichier original restauré, rien n'a été laissé cassé sur disque. "
        f"Dernière erreur :\n{attempts_log[-1] if attempts_log else 'inconnue'}"
    )
