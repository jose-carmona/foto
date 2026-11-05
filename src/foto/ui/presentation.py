"""Reglas de presentación puras de la interfaz de `foto`.

Aquí viven, sin Qt, las reglas con las que la ventana presenta el catálogo:
ordenación y grupos, el documento de búsqueda, el objetivo de la navegación
vertical, las teclas de los límites de grupo y la aritmética del token de
color atenuado ``fg.muted``. La ventana (``window.py``) las consume; este
módulo se prueba sin importar Qt (``tests/test_presentation.py``).
"""

from __future__ import annotations

from collections.abc import Sequence

from foto.ui.models import Photo


# ---------------------------------------------------------------------------
# Nombre accesible y búsqueda
# ---------------------------------------------------------------------------
def photo_accessible_name(photo: Photo) -> str:
    parts = [photo.name]
    if photo.favorite:
        parts.append("favorito")
    return ", ".join(parts)


def photo_search_text(photo: Photo) -> str:
    """Documento de búsqueda normalizado, una entrada por foto (V03).

    Examina nombre, ruta relativa (carpeta y ubicación canónica), réplicas,
    cámara, objetivo, tipo, fecha y el resto de campos indexados que el
    catálogo aporta al modelo de UI. El filtrado es en memoria, contra este
    documento, sin volver a recorrer el filesystem (ADR 0003).
    """
    parts = (
        photo.name,
        photo.taken,
        photo.camera,
        photo.lens,
        photo.focal,
        photo.aperture,
        photo.shutter,
        photo.iso,
        photo.folder,
        photo.fmt,
        photo.location,
        photo.canonical_rel_path or "",
        " ".join(photo.other_locations),
        photo.photo_hash or "",
    )
    return " ".join(part.casefold() for part in parts if part)


# ---------------------------------------------------------------------------
# Orden y grupos
# ---------------------------------------------------------------------------
DEFAULT_SORT = "date"
SORT_KEYS = ("date", "camera")

MONTH_NAMES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def photo_sort_value(photo: Photo, key: str) -> tuple[bool, str]:
    """Clave de ordenación; las fotos sin dato quedan siempre al final."""
    if key == "camera":
        camera = photo.camera.strip().casefold()
        return (not camera, camera)
    taken = photo.taken.strip()
    # Con reverse=True, True ordena antes: las fotos con fecha van primero.
    return (bool(taken), taken)


def sorted_photos(photos: list[Photo], key: str) -> list[Photo]:
    """Ordena por fecha (más nueva primero) o cámara; desempata por nombre."""
    ordered = sorted(photos, key=lambda photo: photo.name.casefold())
    ordered.sort(
        key=lambda photo: photo_sort_value(photo, key),
        reverse=(key == "date"),
    )
    return ordered


def photo_group(photo: Photo, key: str) -> tuple[str, str]:
    """Devuelve la clave y la etiqueta del grupo de una foto."""
    if key == "camera":
        camera = photo.camera.strip()
        if not camera:
            return "", "Sin cámara"
        return camera.casefold(), camera

    taken = photo.taken.strip()
    if not taken:
        return "", "Sin fecha"
    date = taken.split(" ", 1)[0]
    parts = date.split("-")
    if len(parts) >= 2:
        try:
            year = int(parts[0])
            month = int(parts[1])
        except ValueError:
            return date, date
        if 1 <= month <= 12:
            return f"{year:04d}-{month:02d}", f"{MONTH_NAMES[month - 1]} de {year}"
    return date, date


# ---------------------------------------------------------------------------
# Navegación vertical
# ---------------------------------------------------------------------------
def vertical_row_target(
    action: str,
    rows: Sequence[tuple[int, tuple[int, ...]]],
    *,
    row_index: int,
    step_top: int,
) -> int:
    """Índice de línea visual destino para un movimiento vertical.

    ``rows`` describe la rejilla renderizada: una entrada ``(top, centros_x)``
    por línea visual con fotos; las cabeceras de grupo no aparecen, de modo
    que ``j``/``k`` las saltan. Con la agrupación por fecha o cámara las
    líneas pueden ser parciales, así que el destino se deduce de la geometría
    (altura de línea) y no de un número fijo de elementos por línea.
    """
    if action == "j":
        return min(row_index + 1, len(rows) - 1)
    if action == "k":
        return max(row_index - 1, 0)
    if action in ("pagedown", "pageup", "halfdown", "halfup"):
        top = rows[row_index][0]
        if action in ("pagedown", "halfdown"):
            threshold = top + step_top
            for index in range(row_index + 1, len(rows)):
                if rows[index][0] >= threshold:
                    return index
            return len(rows) - 1
        threshold = top - step_top
        for index in range(row_index - 1, -1, -1):
            if rows[index][0] <= threshold:
                return index
        return 0
    raise ValueError(f"acción vertical desconocida: {action}")


# ---------------------------------------------------------------------------
# Teclas de grupo
# ---------------------------------------------------------------------------
# Códigos de tecla tal como los entrega QKeyEvent.key(): Qt::Key_0 es 0x30,
# Qt::Key_Dollar 0x24, Qt::Key_4 0x34 y Qt::Key_5 0x35 (coinciden con ASCII).
# Duplicados aquí como enteros para no importar Qt desde la capa pura.
KEY_0 = 0x30
KEY_DOLLAR = 0x24
KEY_4 = 0x34
KEY_5 = 0x35


def is_group_start(key: int, shift: bool) -> bool:
    """Detecta ``0``: primera foto del grupo.

    Se saca ``0`` del rango de dígitos reservado para que no caiga como acción
    suelta sin consumidor.
    """
    return key == KEY_0 and not shift


def is_group_end(key: int, shift: bool) -> bool:
    """Detecta ``$``: última foto del grupo.

    El ``$`` vive en su propia tecla en algunas distribuciones y en ``Shift+4``
    o ``Shift+5`` en otras; se aceptan ambas hasta que llegue el nuevo uso de
    esos dígitos.
    """
    return key == KEY_DOLLAR or (shift and key in (KEY_4, KEY_5))


# ---------------------------------------------------------------------------
# Color atenuado (token fg.muted), parte pura
# ---------------------------------------------------------------------------
def muted_rgb(
    foreground: tuple[int, int, int], background: tuple[int, int, int]
) -> tuple[int, int, int]:
    """Token ``fg.muted``: texto atenuado con contraste suficiente.

    Interpola canal a canal el color de texto sobre el de fondo: ratio 0.62
    sobre fondo claro y 0.72 sobre fondo oscuro. La claridad del fondo es la
    luminosidad HSL de ``QColor.lightness()`` en escala 0–255 —``(máx + mín) / 2``
    sin redondear—; al no redondear, la comparación coincide con la de Qt
    también en el caso ,5 (Qt redondea 128,5 hacia arriba a 129; ambos dan
    ``> 128`` verdadero). Los adaptadores Qt (``_muted_from_palette`` y
    ``_help_muted``) viven en ``window.py``.
    """
    lightness = (max(background) + min(background)) / 2
    ratio = 0.62 if lightness > 128 else 0.72
    return (
        round(background[0] + (foreground[0] - background[0]) * ratio),
        round(background[1] + (foreground[1] - background[1]) * ratio),
        round(background[2] + (foreground[2] - background[2]) * ratio),
    )
