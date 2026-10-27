# -*- coding: utf-8 -*-
"""
Deteccion con YOLOv8.

El modelo se carga solo cuando hace falta (importacion tardia de ultralytics),
para que las pruebas y las herramientas puedan importar este modulo sin tener
instalados PyTorch ni Ultralytics.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

CLASES_POR_DEFECTO = {0: "persona", 1: "arma", 2: "rostro_cubierto", 3: "guardia"}


class Detector:
    """Envuelve el modelo YOLOv8 entrenado del proyecto."""

    def __init__(
        self,
        ruta_modelo: str | Path = "models/best.pt",
        confianza: float = 0.5,
        clases: dict[int, str] | None = None,
        modelo=None,
    ):
        self.ruta_modelo = Path(ruta_modelo)
        self.confianza = confianza
        self.clases = clases or dict(CLASES_POR_DEFECTO)
        self._modelo = modelo          # se puede inyectar en las pruebas

    @property
    def modelo(self):
        """Carga perezosa: la primera deteccion es la que trae el modelo a memoria."""
        if self._modelo is None:
            if not self.ruta_modelo.exists():
                raise FileNotFoundError(
                    f"No esta el modelo entrenado en {self.ruta_modelo}. "
                    "Copia ahi el best.pt que produce el entrenamiento."
                )
            try:
                from ultralytics import YOLO      # noqa: PLC0415  (importacion tardia a proposito)
            except ImportError as error:          # pragma: no cover - depende del entorno
                raise RuntimeError(
                    "Falta Ultralytics para cargar el modelo. Instalalo con: "
                    "pip install -r requirements.txt"
                ) from error

            log.info("Cargando el modelo %s", self.ruta_modelo)
            self._modelo = YOLO(str(self.ruta_modelo))
        return self._modelo

    def detectar(self, fotograma):
        """
        Devuelve (detecciones, fotograma_anotado).

        Cada deteccion es un diccionario:
            {"clase_id": 1, "clase": "arma", "confianza": 0.87, "bbox": [x1, y1, x2, y2]}
        """
        resultados = self.modelo(fotograma, conf=self.confianza, verbose=False)
        if not resultados:
            return [], fotograma
        primero = resultados[0]
        detecciones = []
        for caja in primero.boxes:
            clase_id = int(caja.cls[0])
            detecciones.append({
                "clase_id": clase_id,
                "clase": self.clases.get(clase_id, str(clase_id)),
                "confianza": round(float(caja.conf[0]), 4),
                "bbox": [float(v) for v in caja.xyxy[0].tolist()],
            })
        anotado = primero.plot() if detecciones else fotograma
        return detecciones, anotado
