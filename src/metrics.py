# -*- coding: utf-8 -*-
"""
Registro de latencias.

Cada alerta deja una fila en metricas/latencias.csv con el tiempo que tardo cada
tramo: inferencia, subida de la imagen y creacion de la notificacion. De aqui
salen las cifras de latencia que pide el objetivo especifico 3 de la tesis, que
son medidas y no estimadas.
"""
from __future__ import annotations

import csv
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

COLUMNAS = [
    "momento_utc", "camara_id", "clase", "confianza", "regla",
    "ms_inferencia", "ms_subida", "ms_notificacion", "ms_total",
    "notificacion_id", "enviada",
]


@dataclass
class Medicion:
    momento_utc: str = ""
    camara_id: int = 0
    clase: str = ""
    confianza: float = 0.0
    regla: str = ""
    ms_inferencia: float = 0.0
    ms_subida: float = 0.0
    ms_notificacion: float = 0.0
    ms_total: float = 0.0
    notificacion_id: str = ""
    enviada: bool = False

    def __post_init__(self):
        if not self.momento_utc:
            self.momento_utc = datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class RegistroDeLatencias:
    """Escribe las mediciones en un CSV, una fila por alerta."""

    ruta: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent
                       / "metricas" / "latencias.csv")
    _candado: threading.Lock = field(default_factory=threading.Lock)

    def anotar(self, medicion: Medicion) -> None:
        with self._candado:
            self.ruta.parent.mkdir(parents=True, exist_ok=True)
            nuevo = not self.ruta.exists() or self.ruta.stat().st_size == 0
            with self.ruta.open("a", newline="", encoding="utf-8") as f:
                escritor = csv.DictWriter(f, fieldnames=COLUMNAS)
                if nuevo:
                    escritor.writeheader()
                fila = asdict(medicion)
                fila["ms_inferencia"] = round(fila["ms_inferencia"], 1)
                fila["ms_subida"] = round(fila["ms_subida"], 1)
                fila["ms_notificacion"] = round(fila["ms_notificacion"], 1)
                fila["ms_total"] = round(fila["ms_total"], 1)
                escritor.writerow(fila)

    def resumen(self) -> dict:
        """Medias y mediana de la latencia total, para el informe de la tesis."""
        if not self.ruta.exists():
            return {"alertas": 0}
        with self.ruta.open(encoding="utf-8") as f:
            filas = [fila for fila in csv.DictReader(f) if fila.get("ms_total")]
        if not filas:
            return {"alertas": 0}
        totales = sorted(float(fila["ms_total"]) for fila in filas)
        mitad = len(totales) // 2
        mediana = totales[mitad] if len(totales) % 2 else (totales[mitad - 1] + totales[mitad]) / 2
        return {
            "alertas": len(totales),
            "ms_minimo": round(totales[0], 1),
            "ms_mediana": round(mediana, 1),
            "ms_media": round(sum(totales) / len(totales), 1),
            "ms_maximo": round(totales[-1], 1),
        }
