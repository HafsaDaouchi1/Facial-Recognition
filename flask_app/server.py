# import base64
# import requests
###############
from pathlib import Path
from secrets import token_urlsafe
from threading import Lock
from time import perf_counter, monotonic

import cv2
import numpy as np
from flask import Flask, jsonify, render_template, request


BASE = Path(__file__).resolve().parents[1]

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024

SEUIL = 0.50
VALIDATIONS = 5
TAILLE_MIN = 100
NETTETE_MIN = 60
DUREE_SESSION = 1800

detecteur = cv2.FaceDetectorYN.create(
    str(BASE / "models/yunet.onnx"), "", (320, 320)
)
reconnaisseur = cv2.FaceRecognizerSF.create(
    str(BASE / "models/sface.onnx"), ""
)

verrou = Lock()
# verrou_vlm = Lock()
###############
sessions = {}


def nettoyer_sessions():
    maintenant = monotonic()
    for token in list(sessions):
        if maintenant - sessions[token]["dernier"] > DUREE_SESSION:
            del sessions[token]


def lire_image(fichier):
    if fichier is None:
        raise ValueError("Image manquante.")

    image = cv2.imdecode(
        np.frombuffer(fichier.read(), dtype=np.uint8),
        cv2.IMREAD_COLOR,
    )
    if image is None:
        raise ValueError("Image illisible.")

    h, w = image.shape[:2]
    facteur = min(1.0, 640 / max(h, w))
    if facteur < 1:
        image = cv2.resize(
            image, None, fx=facteur, fy=facteur,
            interpolation=cv2.INTER_AREA,
        )
    return image


def detecter(image):
    h, w = image.shape[:2]
    detecteur.setInputSize((w, h))
    _, visages = detecteur.detect(image)
    return visages


def extraire(image, visage):
    aligne = reconnaisseur.alignCrop(image, visage)
    embedding = reconnaisseur.feature(aligne).copy()
    if not np.isfinite(embedding).all():
        raise ValueError("Embedding invalide.")
    return aligne, embedding


@app.get("/")
def accueil():
    return render_template("index.html")


@app.post("/api/reference")
def reference():
    image = lire_image(request.files.get("image"))

    with verrou:
        nettoyer_sessions()

        visages = detecter(image)
        if visages is None or len(visages) != 1:
            raise ValueError("Choisissez une référence avec un seul visage.")

        _, embedding = extraire(image, visages[0])

        # Remplacer la précédente référence de ce navigateur.
        ancien = request.form.get("token")
        if ancien:
            sessions.pop(ancien, None)

        if len(sessions) >= 16:
            return jsonify(error="Trop de sessions actives."), 503

        token = token_urlsafe(24)
        sessions[token] = {
            "embedding": embedding,
            "compteur": 0,
            "dernier": monotonic(),
        }

    return jsonify(token=token)


@app.post("/api/reset")
def reset():
    with verrou:
        session = sessions.get(request.form.get("token"))
        if session:
            session["compteur"] = 0
    return jsonify(ok=True)


@app.post("/api/analyze")
def analyser():
    image = lire_image(request.files.get("image"))
    debut = perf_counter()

    with verrou:
        nettoyer_sessions()
        session = sessions.get(request.form.get("token"))

        if session is None:
            return jsonify(error="Réimportez la référence : session expirée."), 400

        maintenant = monotonic()
        if maintenant - session["dernier"] > 2:
            session["compteur"] = 0
        session["dernier"] = maintenant

        resultat = {
            "status": "denied",
            "message": "Aucun visage détecté",
            "score": None,
            "box": None,
            "count": 0,
            "required": VALIDATIONS,
            "threshold": SEUIL,
        }

        visages = detecter(image)

        if visages is None or len(visages) == 0:
            session["compteur"] = 0

        elif len(visages) > 1:
            session["compteur"] = 0
            resultat["message"] = "Plusieurs visages détectés"

        else:
            visage = visages[0]
            x, y, w, h = map(float, visage[:4])
            ih, iw = image.shape[:2]

            # Coordonnées normalisées pour le cadre dans le navigateur.
            resultat["box"] = [x / iw, y / ih, w / iw, h / ih]

            if min(w, h) < TAILLE_MIN:
                session["compteur"] = 0
                resultat.update(
                    status="waiting",
                    message="Come closer to the camera",
                )
            else:
                aligne, embedding = extraire(image, visage)
                gris = cv2.cvtColor(aligne, cv2.COLOR_BGR2GRAY)
                nettete = float(cv2.Laplacian(gris, cv2.CV_64F).var())

                if nettete < NETTETE_MIN:
                    session["compteur"] = 0
                    resultat.update(
                        status="waiting",
                        message="Image floue : stabilisez votre visage",
                    )
                else:
                    score = float(reconnaisseur.match(
                        session["embedding"].copy(),
                        embedding,
                        cv2.FaceRecognizerSF_FR_COSINE,
                    ))
                    if not np.isfinite(score):
                        raise ValueError("Score invalide.")

                    session["compteur"] = (
                        min(session["compteur"] + 1, VALIDATIONS)
                        if score >= SEUIL else 0
                    )

                    accepte = session["compteur"] >= VALIDATIONS
                    resultat.update(
                        score=score,
                        status=(
                            "granted" if accepte
                            else "waiting" if score >= SEUIL
                            else "denied"
                        ),
                        message=(
                            "Face matched" if accepte
                            else "Checking your face…" if score >= SEUIL
                            else "No match found"
                        ),
                    )
                    
            

        resultat["count"] = session["compteur"]
        resultat["latency_ms"] = round((perf_counter() - debut) * 1000)

    return jsonify(resultat)


@app.errorhandler(ValueError)
def erreur_entree(erreur):
    return jsonify(error=str(erreur)), 400


@app.errorhandler(cv2.error)
def erreur_opencv(erreur):
    app.logger.exception("Erreur OpenCV")
    return jsonify(error="Erreur d’analyse de l’image."), 500


@app.errorhandler(413)
def fichier_trop_grand(erreur):
    return jsonify(error="Image trop volumineuse : maximum 8 Mo."), 413

###########################
# @app.post("/api/describe")
# def decrire_scene():
#     image = lire_image(request.files.get("image"))

#     # Éviter plusieurs générations lourdes en parallèle.
#     if not verrou_vlm.acquire(blocking=False):
#         return jsonify(error="Visual assistant is busy. Try again shortly."), 429

#     try:
#         succes, contenu = cv2.imencode(".jpg", image)
#         if not succes:
#             raise ValueError("Unable to prepare image.")

#         image_base64 = base64.b64encode(contenu.tobytes()).decode("ascii")
#         debut = perf_counter()

#         reponse = requests.post(
#             "http://127.0.0.1:11434/api/generate",
#             json={
#                 "model": "moondream",
#                 "prompt": (
#     "Describe the main visible object in one short sentence. "
#     "Do not guess brand names or unreadable text."
# ),
                
                
#                 "images": [image_base64],
#                 "stream": False,
#                 "options": {
#                     "temperature": 0,
#                     "num_predict": 120,
#                 },
#             },
#             timeout=(5, 180),
#         )
#         reponse.raise_for_status()
#         donnees = reponse.json()
#         app.logger.info("Ollama response: %s", donnees)

#         if donnees.get("error"):
#             return jsonify(error="The visual model could not process the image."), 502

#         description = donnees.get("response", "").strip()
#         if not description:
#             return jsonify(error="The model returned an empty description."), 502

#         return jsonify(
#             description=description,
#             duration_s=round(perf_counter() - debut, 1),
#             model="moondream",
#         )

#     except requests.exceptions.ConnectionError:
#         return jsonify(
#             error="Ollama is unavailable. Open Ollama and try again."
#         ), 503

#     except requests.exceptions.Timeout:
#         return jsonify(
#             error="Analysis timed out. Try again with a smaller image."
#         ), 504

#     except (requests.exceptions.RequestException, ValueError) as erreur:
#         app.logger.exception("VLM request failed")

#         if isinstance(erreur, requests.exceptions.HTTPError):
#             app.logger.error(
#               "Ollama response: %s",
#                erreur.response.text,
#             )

#         return jsonify(
#             error="Visual analysis failed. Check the Flask terminal."
#         ), 502

#     finally:
#         verrou_vlm.release()
###########################
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
    