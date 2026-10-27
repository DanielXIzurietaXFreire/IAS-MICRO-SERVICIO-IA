# -*- coding: utf-8 -*-
"""
Configuracion del microservicio de IA.

Todo lo que cambia entre equipos (claves, URL del backend, camaras, horario)
se lee del archivo .env y de zonas.local.json (con zonas.json como plantilla de respaldo).
Nada de credenciales en el codigo.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _cargar_env(ruta: Path | None = None) -> None:
    """Lee el .env sin depender de python-dotenv si no esta instalado."""
    ruta = ruta or (RAIZ / ".env")
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


def _hora(texto: str, por_defecto: time) -> time:
    try:
        h, m = texto.split(":")
        return time(int(h), int(m))
    except (ValueError, AttributeError):
        return por_defecto


@dataclass(frozen=True)
class Config:
    """Configuracion efectiva del microservicio."""

    # Supabase Storage
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_bucket: str = "camaras-imagenes"

    # Backend .NET
    backend_url: str = "http://localhost:5014"
    backend_email: str = ""
    backend_password: str = ""

    # Modelo
    modelo: str = "models/best.pt"
    confianza_minima: float = 0.5
    clases: dict[int, str] = field(default_factory=lambda: {
        0: "persona", 1: "arma", 2: "rostro_cubierto", 3: "guardia"})

    # Operacion
    segundos_entre_fotogramas: float = 0.2
    segundos_de_espera_por_alerta: int = 60      # evita repetir la misma alerta
    segundos_entre_revisiones_de_camaras: int = 60   # vuelve a leer las camaras sin reiniciar
    hora_inicio_laboral: time = time(8, 0)
    hora_fin_laboral: time = time(18, 0)
    dias_laborales: tuple[int, ...] = (0, 1, 2, 3, 4)   # lunes a viernes

    # Zonas restringidas: {id_camara: [(x, y), ...]} en coordenadas normalizadas 0-1
    zonas_restringidas: dict[int, list[tuple[float, float]]] = field(default_factory=dict)

    # Camaras de respaldo, si el backend no responde: [{"id": 1, "stream": "rtsp://..."}]
    camaras_locales: list[dict] = field(default_factory=list)

    @property
    def ruta_modelo(self) -> Path:
        ruta = Path(self.modelo)
        return ruta if ruta.is_absolute() else RAIZ / ruta

    def hay_backend(self) -> bool:
        return bool(self.backend_url)

    def hay_storage(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_key)

    def rol_de_la_clave_de_storage(self) -> str | None:
        """
        Rol que declara la clave de Storage: «service_role», «anon» o None si no es un JWT.
        Nunca devuelve ni registra la clave.
        """
        return rol_de_la_clave(self.supabase_service_key)


def rol_de_la_clave(clave: str) -> str | None:
    """
    Lee el rol de una clave de Supabase sin mostrarla.

    Importa porque **con la clave anonima en SUPABASE_SERVICE_KEY el microservicio arranca
    sin quejarse y las alertas llegan SIN IMAGEN**: Storage rechaza la subida, el aviso se
    crea igual y el operador ve una alerta sin la evidencia. Paso el 13/10/2026 con el .env
    del autor. Misma comprobacion que en tesis/paso5/datos_de_prueba.py.
    """
    import base64

    partes = (clave or "").split(".")
    if len(partes) != 3 or not clave.startswith("eyJ"):
        return None
    try:
        carga = partes[1] + "=" * (-len(partes[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(carga)).get("role")
    except Exception:                               # noqa: BLE001
        return None


def cargar_zonas(ruta: Path | None = None) -> tuple[dict[int, list[tuple[float, float]]], list[dict]]:
    """Lee la configuracion local de zonas y camaras, o la plantilla si no existe."""
    if ruta is None:
        ruta_local = RAIZ / "zonas.local.json"
        ruta = ruta_local if ruta_local.exists() else RAIZ / "zonas.json"
    if not ruta.exists():
        return {}, []
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    zonas = {
        int(camara): [(float(x), float(y)) for x, y in puntos]
        for camara, puntos in datos.get("zonas_restringidas", {}).items()
    }
    return zonas, list(datos.get("camaras", []))


def cargar(ruta_env: Path | None = None, ruta_zonas: Path | None = None) -> Config:
    """Construye la configuracion desde .env y el archivo local de zonas."""
    _cargar_env(ruta_env)
    zonas, camaras = cargar_zonas(ruta_zonas)
    return Config(
        supabase_url=os.getenv("SUPABASE_URL", "").rstrip("/"),
        supabase_service_key=os.getenv("SUPABASE_SERVICE_KEY", ""),
        supabase_bucket=os.getenv("SUPABASE_BUCKET", "camaras-imagenes"),
        backend_url=os.getenv("BACKEND_URL", "http://localhost:5014").rstrip("/"),
        backend_email=os.getenv("BACKEND_EMAIL", ""),
        backend_password=os.getenv("BACKEND_PASSWORD", ""),
        modelo=os.getenv("MODELO", "models/best.pt"),
        confianza_minima=float(os.getenv("CONFIANZA_MINIMA", "0.5")),
        segundos_entre_fotogramas=float(os.getenv("SEGUNDOS_ENTRE_FOTOGRAMAS", "0.2")),
        segundos_de_espera_por_alerta=int(os.getenv("SEGUNDOS_DE_ESPERA_POR_ALERTA", "60")),
        segundos_entre_revisiones_de_camaras=int(os.getenv("SEGUNDOS_ENTRE_REVISIONES_DE_CAMARAS", "60")),
        hora_inicio_laboral=_hora(os.getenv("HORA_INICIO_LABORAL", "08:00"), time(8, 0)),
        hora_fin_laboral=_hora(os.getenv("HORA_FIN_LABORAL", "18:00"), time(18, 0)),
        zonas_restringidas=zonas,
        camaras_locales=camaras,
    )
