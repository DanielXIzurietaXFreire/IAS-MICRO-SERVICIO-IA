# -*- coding: utf-8 -*-
"""
Subida de la evidencia visual a Supabase Storage.

Se habla con la API REST de Storage mediante requests, sin el cliente oficial:
una dependencia menos y una prueba mas facil. La codificacion a JPEG se puede
inyectar, de modo que las pruebas no necesitan OpenCV.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)


def codificar_jpeg(fotograma, calidad: int = 90) -> bytes:
    """Convierte el fotograma en bytes JPEG."""
    import cv2      # noqa: PLC0415

    correcto, buffer = cv2.imencode(".jpg", fotograma, [cv2.IMWRITE_JPEG_QUALITY, calidad])
    if not correcto:
        raise RuntimeError("No se pudo codificar el fotograma como JPEG")
    return buffer.tobytes()


class StorageService:
    """Sube imagenes al bucket y devuelve su URL publica."""

    def __init__(
        self,
        url_supabase: str,
        clave_servicio: str,
        bucket: str = "camaras-imagenes",
        sesion=None,
        codificador=codificar_jpeg,
        tiempo_maximo: int = 15,
    ):
        self.url = url_supabase.rstrip("/")
        self.clave = clave_servicio
        self.bucket = bucket
        self.sesion = sesion or requests.Session()
        self.codificador = codificador
        self.tiempo_maximo = tiempo_maximo

    def nombre_archivo(self, camara_id: int, momento: datetime | None = None) -> str:
        momento = momento or datetime.now(timezone.utc)
        return f"camara_{camara_id}/{momento:%Y%m%d_%H%M%S_%f}.jpg"

    def url_publica(self, ruta: str) -> str:
        return f"{self.url}/storage/v1/object/public/{self.bucket}/{ruta}"

    def subir_imagen(self, fotograma, camara_id: int, momento: datetime | None = None) -> str | None:
        """Sube el fotograma anotado. Devuelve su URL publica, o None si falla."""
        ruta = self.nombre_archivo(camara_id, momento)
        try:
            contenido = self.codificador(fotograma)
            respuesta = self.sesion.post(
                f"{self.url}/storage/v1/object/{self.bucket}/{ruta}",
                data=contenido,
                headers={
                    "apikey": self.clave,
                    "Authorization": f"Bearer {self.clave}",
                    "Content-Type": "image/jpeg",
                    "x-upsert": "true",
                },
                timeout=self.tiempo_maximo,
            )
        except Exception as error:                 # noqa: BLE001
            log.error("Camara %s: fallo al subir la imagen: %s", camara_id, error)
            return None

        if respuesta.status_code not in (200, 201):
            log.error("Camara %s: Storage respondio %s %s",
                      camara_id, respuesta.status_code, getattr(respuesta, "text", "")[:200])
            return None
        return self.url_publica(ruta)
