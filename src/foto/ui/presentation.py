"""Reglas de presentación puras de la interfaz de `foto`.

Aquí viven, sin Qt, las reglas con las que la ventana presenta el catálogo:
ordenación y grupos, el documento de búsqueda, el objetivo de la navegación
vertical, las teclas de los límites de grupo y la aritmética del token de
color atenuado ``fg.muted``. La ventana (``window.py``) las consume; este
módulo se prueba sin importar Qt (``tests/test_presentation.py``).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace

from foto.core.geocoding import Place
from foto.storage.tags import FAVORITE_TAG, Tag, normalize_tag_name
from foto.ui.models import Photo, PhotoTag

# ---------------------------------------------------------------------------
# Etiquetas (ADR 0006)
# ---------------------------------------------------------------------------
#: Estados de asignación que cuentan para buscar, filtrar y mostrar en el
#: panel. ``rejected`` nunca aparece ni filtra.
TAG_VISIBLE_STATUSES = ("confirmed", "suggested")


def with_favorite_tag(photo: Photo, favorite: bool) -> Photo:
    """``Photo`` con la etiqueta de sistema ``fav`` aplicada o retirada.

    Conserva el resto de etiquetas y reconstruye ``fav`` como
    ``manual``/``confirmed``: el conmutado del visor es una decisión humana;
    la escritura real la ejecuta el catálogo (ADR 0006, Decisión 8).
    """
    tags = tuple(tag for tag in photo.tags if tag.kind != "system")
    if favorite:
        tags += (
            PhotoTag(
                name=FAVORITE_TAG,
                kind="system",
                status="confirmed",
                source="manual",
            ),
        )
    return replace(photo, tags=tags)


def photo_matches_tag_name(photo: Photo, name: str) -> bool:
    """Coincidencia del filtro ``:tag`` sobre nombres normalizados.

    Normaliza el nombre tecleado (idempotente: el filtro ya guardado
    también viene normalizado). Solo cuentan los estados visibles
    (``TAG_VISIBLE_STATUSES``); el ``rejected`` jamás filtra. Taxonomía
    plana: sin jerarquía que expandir.
    """
    target = normalize_tag_name(name)
    return any(
        normalize_tag_name(tag.name) == target
        for tag in photo.tags
        if tag.status in TAG_VISIBLE_STATUSES
    )


def find_tag_in_catalog(name: str, catalog: Sequence[Tag]) -> Tag | None:
    """Resuelve el nombre tecleado contra el vocabulario del catálogo.

    La comparación es por nombre normalizado exacto; ``None`` cuando el
    vocabulario no lo conoce (el filtro no crea etiquetas nuevas).
    """
    normalized = normalize_tag_name(name)
    for tag in catalog:
        if normalize_tag_name(tag.name) == normalized:
            return tag
    return None


def tag_chips(photo: Photo) -> tuple[tuple[str, str, bool], ...]:
    """Chips de la sección «Etiquetas» del panel (ADR 0006, Decisión 13).

    Cada chip es ``(texto, tooltip, es_favorito)``; el texto vacío pinta el
    chip carmesí con corazón (el favorito va primero, como cualquier otra
    etiqueta visible). El detalle —el tipo cuando no es libre, «sugerida»
    cuando el estado no es ``confirmed`` y «machine learning» cuando el
    origen es ML— viaja en el tooltip. Las ``rejected`` no aparecen.
    """
    favorite: tuple[str, str, bool] | None = None
    chips: list[tuple[str, str, bool]] = []
    for tag in photo.tags:
        if tag.status not in TAG_VISIBLE_STATUSES:
            continue
        parts: list[str] = []
        if tag.kind != "free":
            parts.append(tag.kind)
        if tag.status != "confirmed":
            parts.append("sugerida")
        if tag.source == "ml":
            parts.append("machine learning")
        detail = " · ".join(parts)
        if tag.kind == "system":
            tooltip = "favorita" if tag.status == "confirmed" else f"favorita · {detail}"
            favorite = ("", tooltip, True)
            continue
        chips.append(
            (
                tag.name,
                f"{tag.name} · {detail}" if detail else tag.name,
                False,
            )
        )
    if favorite is not None:
        return (favorite, *chips)
    return tuple(chips)


# ---------------------------------------------------------------------------
# Nombre accesible y búsqueda
# ---------------------------------------------------------------------------
def photo_accessible_name(photo: Photo) -> str:
    parts = [photo.name]
    if photo.favorite:
        parts.append("favorito")
    return ", ".join(parts)


def photo_place(photo: Photo) -> Place:
    """Lugar resuelto por el indexador como ``Place`` normalizado.

    Los partes que el catálogo no resolvió llegan como ``None``; las cadenas
    vacías se tratan como ausentes para que nunca se inventen nombres.
    """
    return Place(
        country=photo.place_country or None,
        country_code=photo.place_country_code or None,
        state=photo.place_state or None,
        city=photo.place_city or None,
        municipality=photo.place_municipality or None,
    )


def photo_location_display(photo: Photo) -> str:
    """Formato de visualización del lugar: ``Municipio, País`` con lo disponible.

    ``""`` cuando la foto no tiene lugar resuelto: ni grupo ni campo en el
    panel, igual que con los metadatos ausentes.
    """
    return photo_place(photo).display() or ""


def photo_place_details(photo: Photo) -> tuple[tuple[str, str], ...]:
    """Filas de detalle del lugar para el panel, con todo lo que llegó.

    Extrae del ``address`` completo guardado por el indexador las partes
    útiles que no cubre ``Lugar`` (calle, comarca, código postal) más la
    cadena completa que renderizó el servicio. Sin detalle guardado —fotos
    indexadas antes de la revisión— devuelve solo la dirección, si existe.
    """
    rows: list[tuple[str, str]] = []
    if photo.place_address_json:
        try:
            address = json.loads(photo.place_address_json)
        except ValueError:
            address = {}
        if isinstance(address, dict):
            for label, key in (
                ("Calle", "road"),
                ("Comarca", "county"),
                ("Código postal", "postcode"),
            ):
                value = address.get(key)
                if isinstance(value, str) and value.strip():
                    rows.append((label, value))
    if photo.place_display_name:
        rows.append(("Dirección", photo.place_display_name))
    return tuple(rows)


def photo_search_text(photo: Photo) -> str:
    """Documento de búsqueda normalizado, una entrada por foto (V03).

    Examina nombre, ruta relativa (carpeta y ubicación canónica), réplicas,
    cámara, objetivo, tipo, fecha, el lugar resuelto (municipio, ciudad,
    región y país), los nombres de las etiquetas visibles (confirmed +
    suggested, jamás rejected) y el resto de campos indexados que el
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
        photo.place_municipality or "",
        photo.place_city or "",
        photo.place_state or "",
        photo.place_country or "",
        photo.location,
        photo.canonical_rel_path or "",
        " ".join(photo.other_locations),
        photo.photo_hash or "",
        " ".join(
            tag.name for tag in photo.tags if tag.status in TAG_VISIBLE_STATUSES
        ),
    )
    return " ".join(part.casefold() for part in parts if part)


# ---------------------------------------------------------------------------
# Orden y grupos
# ---------------------------------------------------------------------------
DEFAULT_SORT = "date"
SORT_KEYS = ("date", "camera", "location")

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


def _location_parts(place: Place) -> tuple[str, str]:
    """Partes del lugar para ordenar y agrupar: país y municipio.

    Casefolded y con ``""`` donde falta la parte. El municipio es el campo
    ``municipality`` del servicio y, si no llegó, la localidad (``city``) o
    la región —la misma cadena de preferencia que ``display()``—, para que
    las fotos de una misma entidad administrativa compartan grupo (ADR 0005,
    revisión).
    """
    return (
        (place.country or "").casefold(),
        (place.municipality or place.city or place.state or "").casefold(),
    )


def photo_sort_value(photo: Photo, key: str) -> tuple[bool, object]:
    """Clave de ordenación; las fotos sin dato quedan siempre al final.

    ``location`` ordena país → municipio (casefold, ascendente) y el segundo
    elemento es una tupla con las partes del lugar; las fotos sin lugar
    alguno quedan al final por el primer elemento.
    """
    if key == "camera":
        camera = photo.camera.strip().casefold()
        return (not camera, camera)
    if key == "location":
        place = photo_place(photo)
        empty = ("", "")
        if place.display() is None:
            return (True, empty)
        return (False, _location_parts(place))
    taken = photo.taken.strip()
    # Con reverse=True, True ordena antes: las fotos con fecha van primero.
    return (bool(taken), taken)


def sorted_photos(photos: list[Photo], key: str) -> list[Photo]:
    """Ordena por fecha (más nueva primero), cámara o lugar; desempata por nombre.

    Con cámara o lugar, dentro de cada grupo las fotos van de más nueva a
    más antigua —la primera foto del grupo es la última que se tomó— y las
    sin fecha, al final del grupo: el orden base es la fecha descendente y
    el orden por grupo es estable sobre él.
    """
    ordered = sorted(photos, key=lambda photo: photo.name.casefold())
    ordered.sort(key=lambda photo: photo_sort_value(photo, "date"), reverse=True)
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

    if key == "location":
        place = photo_place(photo)
        label = place.display()
        if label is None:
            return "", "Sin ubicación"
        # El grupo es país + municipio; la etiqueta muestra la pareja
        # «Municipio, País» con la parte que haya.
        return "\u001f".join(_location_parts(place)), label

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
