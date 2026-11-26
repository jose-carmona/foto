# Prompt — Ejecutar A.1 y A.2: filtrar favoritas en SQL antes de materializar

> Prompt autocontenido y ejecutable por un agente. Cubre **solo** las tareas
> **A.1** y **A.2** del informe
> [`mejora-arranque-operativo.md`](mejora-arranque-operativo.md). No toca la UI,
> el worker ni el bench: esas son A.3 en adelante. Al terminar, la API que se
> define aquí queda disponible para que el siguiente prompt la consuma.
>
> **Seguimiento.** Marca cada tarea `[x]` con la fecha al cerrarla. Si hay
> desviaciones del enunciado, añade debajo `> Nota (fecha): …`. No reordenes
> ni borres tareas. Una tarea solo está cerrada con sus tests en verde y con
> `uv run pytest` + `uv run ruff check .` completos.

## Resumen de estado

| Tarea | Alcance | Estado |
|---|---|---|
| S1 | Filtro por tag de sistema en `storage/catalog.py` | Cerrada 2026-11-26 |
| S2 | Filtro antes de materializar en `core/library.py` | Cerrada 2026-11-26 |
| S3 | Tests de paridad, estados y cancelación | Cerrada 2026-11-26 |
| S4 | Verificación de rendimiento y cierre | Cerrada 2026-11-26 |

---

## 0. Misión

Hoy `_on_library_loaded` decide arrancar con `fav` **después** de que
`load_from_catalog()` haya leído y materializado las ~29 500 fotos. El filtro
solo marca ítems como ocultos en la rejilla: no ahorra la lectura SQL ni la
creación de objetos. Medido con 25 000 fotos:

- `list_present_files()` (`fetchall` del JOIN completo): **301 ms**;
- `load_from_catalog()` completo: **2914–2965 ms**;
- populate con `:tag fav`: **1277 ms** (frente a 1175 ms sin filtro);
- consulta SQL con `EXISTS` por tag de sistema: **61 ms** para 1 250 filas;
- carga optimizada solo de favoritas: **410 ms** (filtrando en Python tras
  el `fetchall`; con el filtro en SQL el objetivo es ~150 ms).

En la biblioteca real hay **4 favoritas de ~29 567 fotos**: hoy se leen y se
construyen 29 567 objetos para mostrar 4.

**Objetivo de este prompt:** `storage.catalog.list_present_files()` y
`core.library.load_from_catalog()` acepten un parámetro opcional `system_tag`
que filtre **en la consulta SQL** y **antes de construir los objetos**, con
resultados idénticos a los que se obtendrían filtrando la lista completa por
`.favorite`. Sin cambios de comportamiento cuando el parámetro es `None`.

---

## 1. Lectura obligatoria antes de tocar código

1. `AGENT.md` — reglas no negociables (nunca tocar originales, sin Qt en
   `core/`/`storage/`, sin `git commit`/`push` sin permiso, sin dependencias
   nuevas, identificadores en inglés, textos en español).
2. `README.md` — alcance y arquitectura.
3. `prompts/mejora-arranque-operativo.md` — secciones 2, 3 (A.1 y A.2) y 6.
4. `docs/adr/0006-etiquetas.md` — `fav` se resuelve por `kind='system'`,
   nunca por `id`; estados `confirmed`/`suggested` cuentan, `rejected` no.
5. Código a modificar:
   - `src/foto/storage/catalog.py` — `list_present_files()` (línea ~1565),
     imports ya disponibles de `foto.storage.tags` (`normalize_tag_name`,
     `favorite_tag`), índices `tags_system_singleton_idx`,
     `photo_tags_photo_idx`, `photo_tags_tag_idx`.
   - `src/foto/core/library.py` — `load_from_catalog()` (línea ~187),
     `CatalogPhoto.favorite` (línea ~71).
6. Tests de referencia: `tests/test_multi_location.py` (carga desde catálogo,
   cancelación, multi-ubicación) y `tests/test_tags.py` (semillas de
   favoritos y estados de asignación).

---

## 2. API que debe quedar al terminar

```python
# storage/catalog.py
def list_present_files(self, system_tag: str | None = None) -> list[sqlite3.Row]: ...
def has_system_tag(self, system_tag: str) -> bool: ...

# core/library.py
def load_from_catalog[T](
    catalog: Catalog,
    factory: Callable[..., T] = CatalogPhoto,
    should_continue: Callable[[], bool] | None = None,
    *,
    system_tag: str | None = None,
) -> list[T]: ...
```

`system_tag` es el **nombre normalizado** de una etiqueta de sistema. El
llamador real pasará `FAVORITE_TAG` (`"fav"`) en A.3. `None` significa
«sin filtro» y conserva el comportamiento actual al 100 %.

---

## 3. Tareas

### [x] S1 — Filtro por tag de sistema en `storage/catalog.py` (2026-11-26)

> Nota (2026-11-26): `EXPLAIN QUERY PLAN` elige
> `photo_tags_assign_unique_idx` para la subconsulta (empieza por
> `photo_hash`), no `photo_tags_photo_idx`; el enunciado admite ambos. No hay
> scan completo de `photo_tags`, así que no se añadió ningún índice ni se
> tocó el esquema.

1. Extrae la sentencia `SELECT … FROM files f JOIN locations …` de
   `list_present_files()` a una constante de módulo (p. ej.
   `_PRESENT_FILES_SQL`) para **no duplicar la lista de 28 columnas**. La
   consulta sin filtro debe quedar byte a byte equivalente a la actual.
2. Añade el parámetro opcional `system_tag: str | None = None`. Cuando no sea
   `None`, normalízalo con `normalize_tag_name(system_tag)` (ya importado) y
   añade esta cláusula al `WHERE` antes del `ORDER BY f.rel_path`:

   ```sql
   AND EXISTS (
       SELECT 1
         FROM photo_tags pt
         JOIN tags t ON t.id = pt.tag_id
        WHERE pt.photo_hash = p.hash
          AND pt.status IN ('confirmed', 'suggested')
          AND t.kind = 'system'
          AND t.name_normalized = ?
   )
   ```

   Pasa el nombre normalizado como parámetro ligado (`?`), nunca por
   interpolación de string. Mantén `WHERE f.status = 'present' AND
   loc.state = 'active'` y el `ORDER BY` tal cual. Devuelve `list[sqlite3.Row]`
   con **las mismas columnas y el mismo orden** que hoy.
3. Añade `has_system_tag(system_tag: str) -> bool` con el mismo predicado y
   `SELECT 1 … LIMIT 1`: es la comprobación barata que A.3 usará para decidir
   si hay favoritas sin leer las 25 000 filas. Debe devolver `False` si la
   etiqueta existe pero no tiene asignaciones vigentes, y `True` si al menos
   una foto la tiene en `confirmed` o `suggested`. Usa `self._scalar(...)`
   si encaja, o una consulta directa; documenta cuál.
4. Actualiza la docstring de `list_present_files()` en español explicando el
   parámetro nuevo y que el filtro es **por hash de foto**, no por aparición:
   todas las apariciones presentes de una foto favorita siguen viajando, para
   que la elección canónica por `tier` y `other_locations` no cambien.
5. Verifica con `EXPLAIN QUERY PLAN` que la subconsulta usa
   `photo_tags_photo_idx` (o `photo_tags_assign_unique_idx`). **No añadas
   índices ni cambies el esquema** salvo que el plan demuestre un scan completo
   de `photo_tags`; si añades algo, justifícalo aquí y añade el test que lo
   cubre.

### [x] S2 — Filtrar antes de materializar en `core/library.py` (2026-11-26)

1. Añade el parámetro keyword-only `system_tag: str | None = None` a
   `load_from_catalog()` (ver firma en §2). Documenta en la docstring que:
   - filtra en SQL vía `catalog.list_present_files(system_tag=...)`;
   - recorta el diccionario `grouped` **antes** del bucle que llama a
     `factory`, de modo que no se construyan objetos para las fotos que no
     cumplen;
   - con `None` el comportamiento es el actual.
2. Tras `assignments = catalog.tag_assignments()`, y antes del bucle de
   materialización, aplica el recorte cuando `system_tag is not None`.
   Normaliza el nombre una sola vez y usa exactamente este predicado, que
   es el mismo de `CatalogPhoto.favorite` más la comprobación de nombre:

   ```python
   normalized = normalize_tag_name(system_tag)
   grouped = {
       photo_hash: items
       for photo_hash, items in grouped.items()
       if any(
           tag.kind == "system"
           and tag.status in ("confirmed", "suggested")
           and normalize_tag_name(tag.tag_name) == normalized
           for tag in assignments.get(photo_hash, ())
       )
   }
   ```

   Importa `normalize_tag_name` de `foto.storage.tags`. Aunque hoy `fav` es
   la única etiqueta de sistema (lo garantiza el esquema), comprobar el
   nombre mantiene la paridad con la cláusula SQL si algún día aparece otra.
3. **No toques** el cálculo de `states`, la elección canónica por `tier`,
   `resolved_parents`, `other_locations`, el `factory`, la guardia
   `should_continue` ni el `photos.sort(...)` final. El filtro reduce el
   número de fotos emitidas; por tanto reduce también el número de
   comprobaciones de `should_continue` (una por foto emitida), que es lo que
   deben esperar los tests.
4. Añade un helpers privado si hace falta para no duplicar el predicado entre
   S1 y S2, pero **sin crear un módulo nuevo ni importar Qt**. Si decides
   extraerlo a `storage/tags.py`, hazlo público, con type hints y docstring,
   y úsalo desde ambos sitios; en ese caso el test de paridad debe cubrirlo
   directamente.

### [x] S3 — Tests (2026-11-26)

> Nota (2026-11-26): `Photo.from_catalog` sí se pudo usar (`ui/models.py` no
> importa Qt). En el test 9 el factory inyectado devuelve `SimpleNamespace`
> en vez de un dict: el `photos.sort(...)` final de `load_from_catalog` exige
> atributos y S2.3 prohíbe tocarlo. Se compara con `vars(...)`.

Crea `tests/test_catalog_system_tag_filter.py` (sin Qt). Usa
`Catalog.open_in_memory()`, `write_image` y `run_index` de `tests/conftest.py`,
y `toggle_favorite` / `favorite_tag` de `foto.storage.tags` para sembrar.
Patrón mínimo: indexar 3 imágenes en `tmp_path`, tomar los hashes de
`catalog._conn.execute("SELECT hash FROM photos")`, marcar uno con
`toggle_favorite(catalog._conn, hash, now="2026-11-26T12:00:00+00:00")` y
cerrar el catálogo en `finally`.

Tests obligatorios (nombres descriptivos en inglés, docstrings en español):

1. `test_list_present_files_filters_by_system_tag` — sin filtro devuelve 3
   filas; con `system_tag=FAVORITE_TAG` solo la del hash favorito; las columnas
   de esa fila son iguales en ambas llamadas.
2. `test_load_from_catalog_filters_before_materialization` —
   `load_from_catalog(catalog, system_tag=FAVORITE_TAG)` devuelve
   exactamente la foto favorita y coincide con
   `[p for p in load_from_catalog(catalog) if p.favorite]`. Usa `CatalogPhoto`
   por defecto y también `factory=Photo.from_catalog` **solo si el test puede
   importar UI sin Qt en el módulo**; si no, usa un factory lambda que
   devuelva los kwargs y comprueba `kwargs["tags"]`.
3. `test_rejected_system_tag_does_not_count` — cambia la asignación a
   `status='rejected'` con SQL directo y comprueba que desaparece tanto de la
   consulta como de la carga. Añade otra foto con `status='suggested'` y
   comprueba que **sí** entra.
4. `test_free_tag_with_same_name_does_not_count` — crea una etiqueta libre
   (`kind='free'`) asignada a una foto y verifica que el filtro de sistema no
   la devuelve. ( usa un nombre distinto de `fav` para no chocar con el
   índice único de `name_normalized`).
5. `test_no_assignments_returns_empty_and_has_system_tag_is_false` — catálogo
   sin favoritas: lista vacía, sin excepción, y `has_system_tag` es `False`.
6. `test_filter_keeps_canonical_choice_and_other_locations` — con dos
   ubicaciones (`fast`/`archive`) como en `tests/test_multi_location.py`, la
   carga filtrada elige la misma ruta canónica y el mismo `other_locations`
   que la carga completa.
7. `test_cancellation_still_checks_every_emitted_photo` — con
   `should_continue=lambda: False` se lanza `LibraryLoadCancelled`; con un
   contador, el número de comprobaciones es igual al número de favoritas.
8. `test_normalization_and_invalid_name` — `system_tag="FAV"` funciona igual
   que `FAVORITE_TAG`; `system_tag="   "` lanza `TagError`.
9. `test_factory_is_respected_with_filter` — el `factory` inyectado recibe
   los mismos kwargs (en particular `tags` con la asignación favorita) que
   sin filtro.

No hace falta tocar `tests/test_ui_progressive.py`: la UI todavía no usa el
parámetro nuevo.

### [x] S4 — Verificación y cierre (2026-11-26)

1. `uv run pytest tests/test_catalog_system_tag_filter.py -q`
2. `uv run pytest -q` (suite completa, sin pantalla)
3. `uv run ruff check .`
4. Medición temporal **fuera del repositorio** (no añadas scripts al repo):
   reutiliza `scripts.bench_arranque._construir_biblioteca` desde un script en
   el directorio temporal aprobado por el entorno, con 25 000 fotos y una
   favorita cada 20, y compara 3 repeticiones de:
   `catalog.list_present_files()` vs
   `catalog.list_present_files(system_tag=FAVORITE_TAG)` y
   `load_from_catalog(catalog)` vs
   `load_from_catalog(catalog, system_tag=FAVORITE_TAG)`.
   Esperado: consulta ≤ 100 ms (vs ~301 ms) y carga filtrada al menos 3× más
   rápida que la completa (prototipo: 410 ms vs 2896 ms filtrando en Python;
   con SQL debe ser aún mejor). Termina el script con `os._exit(0)` si el
   teardown de PySide6/Python 3.15 da el exit 139 conocido (T2.9).
5. Deja constancia en el reporte de ejecución de: número de tests nuevos,
   cifras antes/después y cualquier desviación. **No hagas commit ni push.**

---

## 4. Fuera del alcance de este prompt

- `src/foto/ui/app.py`, `_photos_from()`, `LibraryLoadWorker`,
  `_on_library_loaded()` y cualquier señal del worker: son A.3.
- `src/foto/ui/window.py`, `set_photos()`, `_populate_grid()` y
  `_apply_filter()`: son A.4.
- Cambios en `scripts/bench_arranque.py` ni modo `--favoritas-primero`: es A.3.
- `build_search_index`, `sorted_photos`, `_format_taken`, fuentes ni overlays:
   son Fase B.
- Cambios de esquema, migraciones, `docs/adr/`, `README.md` ni
  `docs/diseno-ui.md`.
- Nuevas dependencias, commits, pushes o escritura en originales.

## 5. Definición de done

- [x] APIs de §2 implementadas con type hints y docstrings en español.
- [x] `None` no cambia el comportamiento actual: suite existente en verde.
- [x] Paridad demostrada por test: filtrado en SQL == filtrar en Python por
      `.favorite` sobre la lista completa.
- [x] `rejected` excluido, `suggested` incluido, etiquetas libres excluidas,
      cancelación y multi-ubicación intactas.
- [x] `uv run pytest` y `uv run ruff check .` completos.
- [x] Medición de rendimiento adjunta al reporte de ejecución.
- [x] Sin commits/pushes y sin archivos nuevos en el repo aparte de
      `tests/test_catalog_system_tag_filter.py` y los dos módulos modificados.

## 6. Reporte de ejecución (2026-11-26)

**Tests nuevos:** 9 en `tests/test_catalog_system_tag_filter.py` (uno por
caso obligatorio de S3). Suite completa: **1193 passed**; los 1184 previos
siguen en verde con `system_tag=None`. `uv run ruff check .`: limpio.

**Cifras antes/después** (25 000 fotos, 1 250 favoritas —una cada 20—,
3 repeticiones, script desechable fuera del repo reutilizando
`scripts.bench_arranque._construir_biblioteca`):

| Operación | Sin filtro | Con `system_tag='fav'` | Relación |
|---|---:|---:|---:|
| `list_present_files()` | 314,8 ms | **66,2 ms** | 4,8× |
| `load_from_catalog()` | 2686,0 ms | **196,9 ms** | 13,6× |

Muestras de la consulta: 306,4 · 317,0 · 314,8 (sin filtro) y 65,1 · 68,2 ·
66,2 ms (filtrada). Objetos materializados: 25 000 frente a 1 250. Se cumple
el objetivo: consulta ≤ 100 ms (vs ~301 ms de línea base) y carga filtrada
> 3× más rápida. El script terminó con `os._exit(0)` para esquivar el exit
139 conocido del teardown de PySide6/Python 3.15 (T2.9).

**Plan:** la subconsulta usa `photo_tags_assign_unique_idx` (aceptado) y
`tags_system_singleton_idx`; sin índices ni esquema nuevos. Ver nota en S1.

**Desviaciones:** las registradas en S1 (índice distinto del nombre sugerido
pero permitido) y S3 (`SimpleNamespace` en el factory del test 9). No se
extrajo helper a `storage/tags.py`: el predicado Python se dejó inline en
`load_from_catalog` tal como prescribe S2.2, y la cláusula SQL no puede
compartirlo. Sin commits ni pushes.
