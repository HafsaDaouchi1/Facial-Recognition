#!/usr/bin/env bash
set -euo pipefail

# Installer les dépendances.
python -m pip install -r requirements.txt

# Télécharger les deux modèles.
mkdir -p models

curl --fail --location --retry 3 \
  "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx" \
  --output models/yunet.onnx

curl --fail --location --retry 3 \
  "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx" \
  --output models/sface.onnx

# Vérifier leur chargement.
python -c "import cv2; cv2.FaceDetectorYN.create('models/yunet.onnx', '', (320, 320)); cv2.FaceRecognizerSF.create('models/sface.onnx', ''); print('Models loaded successfully')"