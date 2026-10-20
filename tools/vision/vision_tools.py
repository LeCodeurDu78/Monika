"""Image analysis for Monika through a multimodal vision model."""

import base64
import os
import traceback
from config import client_vision, VISION_MODEL_NAME


def _encode_image(image_source: "str | bytes") -> str:
    """Encode an image in base64."""
    if isinstance(image_source, (bytes, bytearray)):
        return base64.b64encode(bytes(image_source)).decode("utf-8")
    with open(image_source, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")


def analyze_image(
    image_path: "str | bytes",
    prompt: str = "Décris cette image en détail et explique ce qu'elle contient.",
    mime_type: str = None,
) -> str:
    """Analyse a local image."""
    try:
        if isinstance(image_path, (bytes, bytearray)):
            base64_image = _encode_image(image_path)
            mime_type = mime_type or "image/png"
        else:
            path = os.path.expanduser(image_path)
            if not os.path.exists(path):
                return f"Erreur : Le fichier image '{path}' est introuvable."

            ext = os.path.splitext(path)[1].lower().replace(".", "")
            mime_type = mime_type or (
                "image/png" if ext == "png" else f"image/{ext if ext in ['jpeg', 'jpg', 'webp'] else 'png'}"
            )
            base64_image = _encode_image(path)

        response = client_vision.chat.completions.create(
            model=VISION_MODEL_NAME,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{base64_image}"},
                        },
                    ],
                }
            ],
        )

        analysis = response.choices[0].message.content

        if not analysis or not analysis.strip():
            return "Attention : Le modèle Vision a renvoyé une réponse vide (possiblement bloqué par les filtres de sécurité ou un problème de format Base64)."

        return f"🔍 [Analyse Vision par {VISION_MODEL_NAME}] :\n{analysis}"

    except Exception as e:
        print(f"\n❌ [DEBUG VISION ERROR] : {e}")
        traceback.print_exc()
        return f"Erreur lors de l'analyse d'image : {str(e)}"
