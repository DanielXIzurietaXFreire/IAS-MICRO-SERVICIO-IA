# -*- coding: utf-8 -*-
"""
Pruebas del aviso de la clave de Storage.

Con la clave ANONIMA puesta en SUPABASE_SERVICE_KEY, el microservicio arrancaba sin
quejarse y **las alertas llegaban SIN IMAGEN**: Storage rechaza la subida, la notificacion
se crea igual y el operador ve el aviso sin la evidencia. Paso el 13/10/2026 con el .env
del autor, asi que ahora se comprueba al arrancar.
"""
import base64
import json
import logging

from src import main as principal
from src.config import Config, rol_de_la_clave


def clave_con_rol(rol: str) -> str:
    """Una clave con la forma de las de Supabase: tres partes y la carga en base64."""
    carga = base64.urlsafe_b64encode(json.dumps({"role": rol}).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJIUzI1NiJ9.{carga}.firma"


# --------------------------------------------------------------- leer el rol
def test_reconoce_la_clave_de_servicio():
    assert rol_de_la_clave(clave_con_rol("service_role")) == "service_role"


def test_reconoce_la_clave_anonima():
    assert rol_de_la_clave(clave_con_rol("anon")) == "anon"


def test_lo_que_no_es_una_clave_devuelve_none():
    for valor in ("", "una-clave-cualquiera", "eyJ.solo.dos", "eyJhbGciOiJIUzI1NiJ9.(((.firma"):
        assert rol_de_la_clave(valor) is None


def test_la_clave_no_aparece_en_el_rol():
    clave = clave_con_rol("anon")
    assert clave not in str(rol_de_la_clave(clave))


# ------------------------------------------------------------ aviso al arrancar
def configuracion(clave: str) -> Config:
    return Config(supabase_url="https://proyecto.supabase.co", supabase_service_key=clave)


def test_con_la_clave_de_servicio_no_avisa_de_nada(caplog):
    with caplog.at_level(logging.WARNING):
        rol = principal.avisar_si_la_clave_no_es_de_servicio(
            configuracion(clave_con_rol("service_role")))

    assert rol == "service_role"
    assert caplog.records == []


def test_con_la_clave_anonima_avisa_como_ERROR_y_dice_que_faltara_la_imagen(caplog):
    with caplog.at_level(logging.WARNING):
        rol = principal.avisar_si_la_clave_no_es_de_servicio(configuracion(clave_con_rol("anon")))

    assert rol == "anon"
    registro = caplog.records[0]
    assert registro.levelno == logging.ERROR
    mensaje = registro.getMessage()
    assert "SIN IMAGEN" in mensaje
    assert "anon" in mensaje


def test_el_aviso_nunca_escribe_la_clave(caplog):
    clave = clave_con_rol("anon")
    with caplog.at_level(logging.WARNING):
        principal.avisar_si_la_clave_no_es_de_servicio(configuracion(clave))

    assert clave not in caplog.text


def test_con_algo_que_no_es_un_jwt_avisa_pero_mas_flojo(caplog):
    with caplog.at_level(logging.WARNING):
        rol = principal.avisar_si_la_clave_no_es_de_servicio(configuracion("clave-de-prueba"))

    assert rol is None
    assert caplog.records[0].levelno == logging.WARNING
    assert "SIN IMAGEN" in caplog.records[0].getMessage()
