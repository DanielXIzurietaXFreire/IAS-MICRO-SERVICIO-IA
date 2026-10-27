# -*- coding: utf-8 -*-
"""Pruebas de la subida a Supabase Storage, sin red y sin OpenCV."""
from datetime import datetime, timezone

from src.storage import StorageService


class RespuestaFalsa:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class SesionFalsa:
    def __init__(self, respuesta=None):
        self.llamadas = []
        self.respuesta = respuesta or RespuestaFalsa(200)

    def post(self, url, **kwargs):
        self.llamadas.append({"url": url, **kwargs})
        return self.respuesta


def servicio(respuesta=None):
    sesion = SesionFalsa(respuesta)
    s = StorageService("https://proyecto.supabase.co", "clave-servicio",
                       sesion=sesion, codificador=lambda f, calidad=90: b"jpeg-falso")
    return s, sesion


MOMENTO = datetime(2026, 9, 26, 15, 30, 45, 123456, tzinfo=timezone.utc)


def test_el_nombre_ordena_por_camara_y_momento():
    s, _ = servicio()
    assert s.nombre_archivo(3, MOMENTO) == "camara_3/20260926_153045_123456.jpg"


def test_la_url_publica_apunta_al_bucket():
    s, _ = servicio()
    assert s.url_publica("camara_3/x.jpg") == (
        "https://proyecto.supabase.co/storage/v1/object/public/camaras-imagenes/camara_3/x.jpg")


def test_subir_imagen_devuelve_la_url_y_usa_las_cabeceras_correctas():
    s, sesion = servicio()
    url = s.subir_imagen("fotograma", 3, MOMENTO)
    assert url.endswith("camara_3/20260926_153045_123456.jpg")
    llamada = sesion.llamadas[0]
    assert llamada["data"] == b"jpeg-falso"
    assert llamada["headers"]["Authorization"] == "Bearer clave-servicio"
    assert llamada["headers"]["apikey"] == "clave-servicio"
    assert llamada["headers"]["Content-Type"] == "image/jpeg"


def test_si_storage_falla_devuelve_none_y_la_alerta_sigue():
    s, _ = servicio(RespuestaFalsa(403, "sin permisos"))
    assert s.subir_imagen("fotograma", 1, MOMENTO) is None


def test_si_la_codificacion_falla_devuelve_none():
    sesion = SesionFalsa()
    def codificador_roto(fotograma, calidad=90):
        raise RuntimeError("fotograma corrupto")
    s = StorageService("https://x.supabase.co", "k", sesion=sesion, codificador=codificador_roto)
    assert s.subir_imagen("fotograma", 1) is None
    assert sesion.llamadas == []
