"""Vocabulario de la línea de comandos ``:``.

Puro, sin Qt. Lo ejecuta ``PhotoWindow._run_command`` y lo completa la barra
de estado; vive aparte para que el módulo de paneles no dependa de la ventana.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    name: str
    description: str


COMMANDS = (
    Command("q", "salir de la aplicación"),
    Command("quit", "salir de la aplicación"),
    Command("sort date", "ordenar por fecha"),
    Command("sort camera", "ordenar por cámara"),
    Command("sort location", "ordenar por ubicación"),
    Command("tag", "filtrar por etiqueta (:tag <nombre>)"),
    Command("help", "mostrar la ayuda"),
)
