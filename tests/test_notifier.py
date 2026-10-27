# -*- coding: utf-8 -*-
"""Pruebas del cliente del backend, con una sesion HTTP falsa: no tocan la red."""
from datetime import datetime, timezone

from src.notifier import BackendClient


class RespuestaFalsa:
    def __init__(self, status_code=200, datos=None, text=""):
        self.status_code = status_code
        self._datos = datos if datos is not None else {}
        self.text = text

    def json(self):
        return self._datos


class SesionFalsa:
    """Guarda cada llamada y devuelve las respuestas que se le den."""

    def __init__(self, respuestas=None):
        self.llamadas = []
        self.respuestas = list(respuestas or [])

    def _siguiente(self, metodo, url, **kwargs):
        self.llamadas.append({"metodo": metodo, "url": url, **kwargs})
        return self.respuestas.pop(0) if self.respuestas else RespuestaFalsa()

    def post(self, url, **kwargs):
        return self._siguiente("POST", url, **kwargs)

    def get(self, url, **kwargs):
        return self._siguiente("GET", url, **kwargs)

    def request(self, metodo, url, **kwargs):
        return self._siguiente(metodo, url, **kwargs)


def cliente(respuestas=None, email="ia@sisa.fin.ec", password="clave"):
    sesion = SesionFalsa(respuestas)
    return BackendClient("http://localhost:5014", email, password, sesion=sesion), sesion


# ------------------------------------------------------------------- sesion
def test_login_lee_el_campo_token_que_devuelve_el_backend():
    """El backend responde {"token": ...}; el campo accessToken no existe."""
    c, sesion = cliente([RespuestaFalsa(200, {"token": "abc.123", "usuario": {"id": "1"}})])
    assert c.iniciar_sesion() == "abc.123"
    assert c.token == "abc.123"
    assert sesion.llamadas[0]["url"] == "http://localhost:5014/api/auth/login"
    assert sesion.llamadas[0]["json"] == {"email": "ia@sisa.fin.ec", "password": "clave"}


def test_login_acepta_tambien_accessToken():
    c, _ = cliente([RespuestaFalsa(200, {"accessToken": "xyz"})])
    assert c.iniciar_sesion() == "xyz"


def test_login_rechazado_no_deja_token():
    c, _ = cliente([RespuestaFalsa(401)])
    assert c.iniciar_sesion() is None
    assert c.token is None


def test_sin_credenciales_no_intenta_iniciar_sesion():
    c, sesion = cliente(email="", password="")
    assert c.iniciar_sesion() is None
    assert sesion.llamadas == []


# ------------------------------------------------------------------ camaras
def test_obtener_camaras_descarta_las_que_no_tienen_stream():
    c, _ = cliente([RespuestaFalsa(200, [
        {"id": 1, "nombre": "Caja", "stream": "rtsp://uno"},
        {"id": 2, "nombre": "Sin flujo", "stream": ""},
    ])])
    camaras = c.obtener_camaras()
    assert [x["id"] for x in camaras] == [1]


def test_camaras_con_error_devuelve_lista_vacia():
    c, _ = cliente([RespuestaFalsa(500)])
    assert c.obtener_camaras() == []


def test_consultar_camaras_distingue_el_fallo_de_la_lista_vacia():
    # None = el backend no responde; [] = responde, pero no hay camaras.
    c, _ = cliente([RespuestaFalsa(500)])
    assert c.consultar_camaras() is None
    c, _ = cliente([RespuestaFalsa(200, [])])
    assert c.consultar_camaras() == []


# ------------------------------------------------------------ notificacion
def test_crear_notificacion_envia_el_cuerpo_que_espera_el_backend():
    c, sesion = cliente([RespuestaFalsa(201, {"id": 77, "estado": "pendiente"})])
    c.token = "tok"
    momento = datetime(2026, 9, 26, 15, 30, tzinfo=timezone.utc)
    creada = c.crear_notificacion("Arma detectada", "https://img/1.jpg", 3, momento)

    assert creada["id"] == 77
    enviado = sesion.llamadas[0]["json"]
    assert enviado == {
        "mensaje": "Arma detectada",
        "timestamp": "2026-09-26T15:30:00Z",
        "imagenUrl": "https://img/1.jpg",
        "estado": "pendiente",
        "camaraId": 3,
    }
    assert sesion.llamadas[0]["headers"]["Authorization"] == "Bearer tok"


def test_notificacion_sin_imagen_va_igual():
    c, sesion = cliente([RespuestaFalsa(201, {"id": 1})])
    assert c.crear_notificacion("Rostro cubierto", None, 1) is not None
    assert sesion.llamadas[0]["json"]["imagenUrl"] is None


def test_si_el_backend_devuelve_401_renueva_el_token_y_reintenta():
    c, sesion = cliente([
        RespuestaFalsa(401),                                  # primer intento
        RespuestaFalsa(200, {"token": "nuevo"}),              # login
        RespuestaFalsa(201, {"id": 9}),                       # reintento
    ])
    c.token = "viejo"
    creada = c.crear_notificacion("Arma detectada", None, 1)
    assert creada["id"] == 9
    assert c.token == "nuevo"
    assert [l["metodo"] for l in sesion.llamadas] == ["POST", "POST", "POST"]
    assert sesion.llamadas[2]["headers"]["Authorization"] == "Bearer nuevo"


def test_error_del_backend_no_revienta_el_bucle():
    c, _ = cliente([RespuestaFalsa(400, text="camara inexistente")])
    assert c.crear_notificacion("Arma detectada", None, 999) is None


def test_caida_de_red_no_revienta_el_bucle():
    class SesionQueFalla(SesionFalsa):
        def request(self, *args, **kwargs):
            raise ConnectionError("backend caido")

    c = BackendClient("http://localhost:5014", sesion=SesionQueFalla())
    assert c.crear_notificacion("Arma detectada", None, 1) is None
