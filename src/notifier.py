# -*- coding: utf-8 -*-
"""
Cliente del backend ASP.NET Core.

Hace tres cosas:
  - iniciar sesion en POST /api/auth/login y guardar el token,
  - leer las camaras registradas en GET /api/camaras,
  - crear la notificacion en POST /api/notificaciones.

El backend devuelve el token en el campo "token" (asi lo lee el frontend
Angular). Se acepta tambien "accessToken" por si cambiara el contrato.
Si una llamada protegida responde 401, se vuelve a iniciar sesion una vez.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)


class BackendClient:
    """Todo lo que el microservicio necesita del backend."""

    def __init__(
        self,
        url_backend: str,
        email: str = "",
        password: str = "",
        sesion=None,
        tiempo_maximo: int = 15,
    ):
        self.url = url_backend.rstrip("/")
        self.email = email
        self.password = password
        self.sesion = sesion or requests.Session()
        self.tiempo_maximo = tiempo_maximo
        self.token: str | None = None

    # ---------------------------------------------------------------- sesion
    def iniciar_sesion(self) -> str | None:
        """
        Obtiene el token del microservicio.

        Desde el 08/10/2026 las credenciales son OBLIGATORIAS: el backend quito el
        [AllowAnonymous] de POST /api/notificaciones y Supabase ya no tiene politicas
        para el rol anonimo. Sin usuario y contrasena el microservicio NO puede
        registrar ninguna alerta, asi que se avisa como error y no como informacion.
        """
        if not (self.email and self.password):
            log.error("FALTAN CREDENCIALES: rellena BACKEND_EMAIL y BACKEND_PASSWORD en el "
                      ".env. Sin ellas el backend rechaza las alertas con 401 y el sistema "
                      "no avisa de nada.")
            return None
        try:
            respuesta = self.sesion.post(
                f"{self.url}/api/auth/login",
                json={"email": self.email, "password": self.password},
                timeout=self.tiempo_maximo,
            )
        except Exception as error:                 # noqa: BLE001
            log.error("No se pudo contactar con el backend para iniciar sesion: %s", error)
            return None

        if respuesta.status_code != 200:
            log.error("Inicio de sesion rechazado: %s", respuesta.status_code)
            return None

        datos = respuesta.json()
        self.token = datos.get("token") or datos.get("accessToken")
        if not self.token:
            log.error("La respuesta del login no trae token: %s", list(datos))
        return self.token

    def _cabeceras(self) -> dict:
        cabeceras = {"Content-Type": "application/json"}
        if self.token:
            cabeceras["Authorization"] = f"Bearer {self.token}"
        return cabeceras

    def _con_reintento(self, metodo: str, ruta: str, **kwargs):
        """Lanza la peticion y, si responde 401, renueva el token y repite una vez."""
        respuesta = self.sesion.request(
            metodo, f"{self.url}{ruta}", headers=self._cabeceras(),
            timeout=self.tiempo_maximo, **kwargs)
        if respuesta.status_code == 401 and self.email:
            log.info("Token caducado: se renueva la sesion")
            self.iniciar_sesion()
            respuesta = self.sesion.request(
                metodo, f"{self.url}{ruta}", headers=self._cabeceras(),
                timeout=self.tiempo_maximo, **kwargs)
        return respuesta

    # --------------------------------------------------------------- camaras
    def consultar_camaras(self) -> list[dict] | None:
        """
        Camaras registradas en el sistema, o None si el backend no responde.

        None y lista vacia no son lo mismo: None es «no se sabe» (se mantiene lo que
        haya en marcha) y [] es «no hay camaras registradas».
        """
        try:
            respuesta = self._con_reintento("GET", "/api/camaras")
        except Exception as error:                 # noqa: BLE001
            log.error("No se pudieron leer las camaras: %s", error)
            return None
        if respuesta.status_code != 200:
            log.error("GET /api/camaras respondio %s", respuesta.status_code)
            return None
        camaras = respuesta.json()
        return [c for c in camaras if c.get("stream")]

    def obtener_camaras(self) -> list[dict]:
        """Como consultar_camaras, pero con lista vacia si no se pueden leer."""
        return self.consultar_camaras() or []

    # ---------------------------------------------------------- notificacion
    def crear_notificacion(
        self,
        mensaje: str,
        imagen_url: str | None,
        camara_id: int,
        momento: datetime | None = None,
    ) -> dict | None:
        """Registra la alerta. El backend la guarda y la emite por SignalR."""
        momento = momento or datetime.now(timezone.utc)
        cuerpo = {
            "mensaje": mensaje,
            "timestamp": momento.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "imagenUrl": imagen_url,
            "estado": "pendiente",
            "camaraId": camara_id,
        }
        try:
            respuesta = self._con_reintento("POST", "/api/notificaciones", json=cuerpo)
        except Exception as error:                 # noqa: BLE001
            log.error("No se pudo crear la notificacion: %s", error)
            return None

        if respuesta.status_code not in (200, 201):
            log.error("POST /api/notificaciones respondio %s %s",
                      respuesta.status_code, getattr(respuesta, "text", "")[:200])
            return None

        creada = respuesta.json()
        log.info("Notificacion %s creada para la camara %s", creada.get("id"), camara_id)
        return creada
