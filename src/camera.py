# -*- coding: utf-8 -*-
"""
Captura de fotogramas de una camara IP por RTSP.

OpenCV se importa tarde, igual que Ultralytics: asi las pruebas corren sin el.
Si el flujo se corta, la captura se reconecta sola con una espera creciente.
"""
from __future__ import annotations

import logging
import time as _time

log = logging.getLogger(__name__)


class CameraCapture:
    """Lee fotogramas de un flujo RTSP y se reconecta cuando se cae."""

    def __init__(self, url: str, camara_id: int, reintentos: int = 3, captura=None):
        self.url = url
        self.camara_id = camara_id
        self.reintentos = reintentos
        self._cap = captura           # se inyecta en las pruebas
        self._fallos = 0

    def _abrir(self):
        try:
            import cv2      # noqa: PLC0415
        except ImportError as error:              # pragma: no cover - depende del entorno
            raise RuntimeError(
                "Falta OpenCV para leer las camaras. Instalalo con: "
                "pip install -r requirements.txt"
            ) from error

        log.info("Camara %s: conectando a %s", self.camara_id, self.url.split("@")[-1])
        return cv2.VideoCapture(self.url)

    @property
    def captura(self):
        if self._cap is None:
            self._cap = self._abrir()
        return self._cap

    def capturar_frame(self):
        """Devuelve el fotograma, o None si el flujo no responde."""
        leido, fotograma = self.captura.read()
        if leido and fotograma is not None:
            self._fallos = 0
            return fotograma

        self._fallos += 1
        log.warning("Camara %s: fotograma perdido (%s seguidos)", self.camara_id, self._fallos)
        self.liberar()
        if self._fallos <= self.reintentos:
            _time.sleep(min(2 ** self._fallos, 10))
            leido, fotograma = self.captura.read()
            if leido and fotograma is not None:
                self._fallos = 0
                return fotograma
        return None

    def liberar(self):
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:                     # noqa: BLE001
                log.debug("Camara %s: no se pudo liberar la captura", self.camara_id)
            self._cap = None
