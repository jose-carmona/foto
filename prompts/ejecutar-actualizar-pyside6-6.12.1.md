# Prompt — Ejecutar la actualización a PySide6 6.12.1 y verificar el segfault

> Prompt autocontenido y ejecutable por un agente. Se ejecuta **solo cuando
> PySide6 6.12.1 esté publicado en PyPI**. No modifica código de la aplicación:
> actualiza la restricción de dependencias y el lock, y comprueba con la suite
> completa que el segfault descrito en § 0 ha desaparecido.
>
> **Seguimiento.** Este fichero es el registro de tareas. Al terminar cada una:
> márcala `[x]` con la fecha y, si hubo desviaciones del enunciado, añade debajo
> una línea `> Nota (fecha): …`. No reordenes ni borres tareas; si aparece una
> pendiente nueva, añádela al final con el mismo formato. Una tarea solo está
> cerrada con la verificación correspondiente en verde. **No hagas `git commit`
> ni `git push`:** los cambios quedan en el árbol a la espera de confirmación.

## Resumen de estado

| Tarea | Alcance | Estado |
| --- | --- | --- |
| S1 | Comprobar que 6.12.1 está publicada y trae los arreglos | Pendiente |
| S2 | Subir el mínimo en `pyproject.toml` y actualizar `uv.lock` | Pendiente |
| S3 | Verificar el segfault: 10 corridas normales + 5 con GC agresivo | Pendiente |
| S4 | Reporte y cierre | Pendiente |

---

## 0. Misión (el por qué)

El 2026-11-27 apareció un `Fatal Python error: Segmentation fault` (exit 139)
al ejecutar la suite completa, en un entorno **Python 3.15.1 x86_64 + PySide6
6.12.0 + Qt 6.12.0** en macOS (`QT_QPA_PLATFORM=offscreen`). El fallo es
intermitente: se observó una vez en una corrida normal y una de cada tres con
el recolector de basura forzado; el módulo implicado pasa aislado.

Dos volcados de `faulthandler` coinciden en la causa:

- El hilo que falla es el **hilo de la UI**, dentro de `QApplication.processEvents()`
  (o de `ThumbnailDelegate.paint`, `src/foto/ui/grid.py:127`), y la pila de Python
  dice `Garbage-collecting`.
- La pila C muestra `_Py_HandlePending` → `_make_pending_calls` → el manejador
  de borrado en hilo principal de Shiboken (`mainThreadDeletionHandler`), que
  acaba en el destructor del wrapper de un `QLabel` o en una llamada a puntero
  nulo (`0x0`).
- El `QThread` del worker de carga aparece en el volcado solo porque
  `faulthandler` vuelca todos los hilos; **no es el hilo que falla**, y los tests
  ya aseveran que ese hilo termina correctamente.

El diagnóstico apunta a un bug de la capa Shiboken con Python 3.15, no al código
del proyecto. El changelog de **PySide6 6.12.1** (commit `72777c1`, rama de
desarrollo; aún no publicado el 2026-11-27, cuando PyPI marcaba 6.12.0 como
última versión) incluye correcciones que encajan con los volcados:

- `PYSIDE-3221`: Shiboken6 da soporte a Python 3.15.
- `PYSIDE-3473`: el atributo *delete-in-main-thread* no tenía efecto en clases
  derivadas de Python (como `ThumbnailDelegate`).
- `PYSIDE-3479`: doble borrado de widgets en ciertos layouts.
- `PYSIDE-3515`: relaciones padre/hijo incorrectas de las heurísticas del generador.
- `PYSIDE-3517`: crash en Python 3.15 al pasar un temporal a `QTimer.singleShot`.

**Objetivo:** actualizar a 6.12.1 y demostrar, con 15 corridas limpias de la
suite (10 normales + 5 con GC agresivo), que el fallo ya no se reproduce. Si
aparece aunque sea una corrida con crash, **no se da por resuelto**: se adjunta
el volcado y se detiene el trabajo sin aplicar mitigaciones locales.

---

## 1. Lectura obligatoria antes de tocar código

1. `AGENT.md` — reglas no negociables: originales intactos, sin dependencias
   nuevas sin justificar, sin `git commit`/`push` sin permiso, sin tocar ADRs ni
   documentación de diseño sin autorización.
2. `pyproject.toml` (línea de `dependencies`) y `uv.lock` — estado actual de
   PySide6 y de sus paquetes asociados.
3. Este prompt, § 0 (diagnóstico), § 3 (tareas) y § 4 (fuera de alcance).
4. Antes de empezar, comprueba el estado del repositorio (`git status --short`)
   y ejecuta la verificación disponible (`uv run pytest -q`, `uv run ruff check .`).
   Registra el número real de tests: no asumas que sigue siendo 1254.

No se modifica código fuente, tests ni documentación del proyecto.

---

## 2. Estado de dependencias que debe quedar al terminar

```toml
# pyproject.toml
dependencies = [
    "PySide6>=6.12.1",   # antes: "PySide6>=6.7"
    "platformdirs>=4.2",
]
```

En `uv.lock`, las versiones resueltas de `PySide6`, `PySide6_Essentials`,
`PySide6_Addons` y `shiboken6` deben ser `6.12.1`. La versión importada en
tiempo de ejecución debe confirmarlo (§ 3, S2). El intérprete sigue siendo
Python 3.15.1: **no se cambia de versión de Python** en este prompt.

---

## 3. Tareas

### [ ] S1 — Comprobar que 6.12.1 está publicada y trae los arreglos

1. Consulta PyPI y comprueba tanto la versión latest como la existencia del
   release `6.12.1`:

   ```sh
   python3 -c "import json,urllib.request; d=json.load(urllib.request.urlopen('https://pypi.org/pypi/PySide6/json')); print('latest:', d['info']['version']); print('6.12.1 publicada:', '6.12.1' in d['releases'])"
   ```

2. Comprueba que los paquetes que se resuelven junto con `PySide6` también
   tienen release `6.12.1` (si falta alguno, el lock no podrá completarse):

   ```sh
   python3 -c "import json,urllib.request; ps=['PySide6','PySide6_Essentials','PySide6_Addons','shiboken6']; [print(p, '6.12.1' in json.load(urllib.request.urlopen(f'https://pypi.org/pypi/{p}/json'))['releases']) for p in ps]"
   ```

3. Comprueba el changelog oficial de la versión y que aparecen los cinco IDs
   de § 0:

   ```sh
   curl -fsSL https://raw.githubusercontent.com/qtproject/pyside-pyside-setup/dev/doc/changelogs/changes-6.12.1 \
       | grep -E 'PYSIDE-(3221|3473|3479|3515|3517)'
   ```

   Si la ruta `dev` no existe, prueba el árbol `master` o
   `https://code.qt.io/cgit/pyside/pyside-setup.git/plain/doc/changelogs/changes-6.12.1`.

4. **Condición de parada:** si `6.12.1` no está publicada, si falta algún
   paquete asociado o si el changelog no contiene esos arreglos, **detente
   aquí**: deja el repositorio intacto, registra lo comprobado en § 6 y no
   continúes con S2. No intentes instalar versiones de develop ni wheels de
   Qt proporcionados por terceros.

### [ ] S2 — Subir el mínimo en `pyproject.toml` y actualizar `uv.lock`

1. Cambia en `pyproject.toml` la entrada `"PySide6>=6.7"` por
   `"PySide6>=6.12.1"`. No toques ninguna otra dependencia ni la versión de
   Python requerida.
2. Regenera el lock y sincroniza el entorno:

   ```sh
   uv lock
   uv sync
   ```

3. Confirma las versiones efectivas y que Qt sigue siendo 6.12.x:

   ```sh
   uv run python -c "from importlib.metadata import version; print({p: version(p) for p in ('PySide6','PySide6_Essentials','PySide6_Addons','shiboken6')})"
   uv run python -c "import PySide6, PySide6.QtCore as q; print(PySide6.__version__, q.qVersion())"
   ```

   Las cinco versiones de paquete deben ser `6.12.1`; `qVersion()` debe
   empezar por `6.12.`. Si algo resuelve a otra versión, corrige el lock antes
   de pasar a S3 y anota la desviación.

### [ ] S3 — Verificar que el segfault ha desaparecido

1. Crea el ensayo de estrés de GC **fuera del repositorio** (no añadas
   scripts al repo):

   ```sh
   TMP_GC=$(mktemp -d)
   cat > "$TMP_GC/sitecustomize.py" <<'EOF'
   import gc
   gc.set_threshold(1, 1, 1)
   EOF
   ```

2. Ejecuta **10 corridas completas normales**, desde la raíz del repo, con
   volcado de faulthandler activado y salida a ficheros temporales:

   ```sh
   for i in $(seq 1 10); do
     QT_QPA_PLATFORM=offscreen PYTHONFAULTHANDLER=1 uv run pytest -q \
       > "$TMP_GC/normal_$i.log" 2>&1
     status=$?
     echo "normal $i: exit=$status $(tail -1 "$TMP_GC/normal_$i.log")"
   done
   ```

3. Ejecuta **5 corridas completas con GC agresivo**, anteponiendo el
   `sitecustomize` anterior al `PYTHONPATH`:

   ```sh
   for i in $(seq 1 5); do
     QT_QPA_PLATFORM=offscreen PYTHONFAULTHANDLER=1 PYTHONPATH="$TMP_GC" uv run pytest -q \
       > "$TMP_GC/gc_$i.log" 2>&1
     status=$?
     echo "gc $i: exit=$status $(tail -1 "$TMP_GC/gc_$i.log")"
   done
   ```

4. Comprueba de forma explícita que no hubo crash en ninguna de las 15 corridas:

   ```sh
   grep -l -E 'Segmentation fault|Fatal Python error' "$TMP_GC"/*.log || echo "sin crashes"
   uv run ruff check .
   ```

5. **Criterio de aceptación:** las 15 corridas terminan con exit 0, ningún log
   contiene `Segmentation fault` ni `Fatal Python error`, todos reportan el
   total real de tests en verde y `uv run ruff check .` está limpio. Si una
   sola corrida falla, conserva su log completo, marca la tarea como no cerrada
   y **no continúes**: no apliques mitigaciones locales (retener callables de
   `QTimer.singleShot`, evitar `deleteLater()` ni cambiar delegates) sin
   autorización expresa.

### [ ] S4 — Reporte y cierre

1. Rellena § 6 con: resultado de S1 (URLs consultadas y fecha de publicación),
   diff de `pyproject.toml` y `uv.lock`, versiones antes/después, los 15
   resultados de S3 (exit y última línea de cada log), el resultado del `grep`
   y de `ruff`, y las desviaciones.
2. **No hagas commit ni push.** Deja `pyproject.toml` y `uv.lock` modificados
   y listos para revisión.

---

## 4. Fuera del alcance de este prompt

- Cambios de código fuente, tests, `README.md`, `AGENT.md`, `docs/` ni ADRs.
- Cambio de versión de Python (se mantiene 3.15.1) ni de otras dependencias.
- Instalación de versiones de desarrollo, wheels externos o parches de Shiboken.
- Mitigaciones locales del segfault; solo se aplicarían previa autorización si
  S3 falla con 6.12.1 ya instalada.
- Añadir scripts al repositorio: los logs y el `sitecustomize` viven en `/tmp`.
- `git commit`, `git push` o cambios de configuración de git.

---

## 5. Definición de done

- [ ] S1 confirma que 6.12.1 está publicada en PyPI, que los paquetes asociados
      también lo están y que el changelog contiene `PYSIDE-3221`, `3473`,
      `3479`, `3515` y `3517`. (Si no, el prompt se detuvo aquí.)
- [ ] `pyproject.toml` exige `PySide6>=6.12.1` y `uv.lock` resuelve 6.12.1 en
      los cuatro paquetes; la versión importada y `qVersion()` lo confirman.
- [ ] 10 corridas normales + 5 con GC agresivo, las 15 con exit 0 y sin
      `Segmentation fault` ni `Fatal Python error`.
- [ ] `uv run ruff check .` limpio y suite completa en verde en todas las
      corridas (con el total real de tests registrado).
- [ ] § 6 rellenado, sin commits ni pushes y sin ficheros nuevos en el repo.

---

## 6. Reporte de ejecución (rellenar al terminar)

**Publicación comprobada:** {{fecha}} · PyPI latest `{{versión}}` · release
`6.12.1` `{{sí/no}}` · paquetes asociados `{{resultado}}` · changelog
`{{URL, IDs encontrados}}`.

**Versiones:**

| Paquete | Antes | Después |
| --- | --- | --- |
| PySide6 | 6.12.0 | {{versión}} |
| PySide6_Essentials | {{versión}} | {{versión}} |
| PySide6_Addons | {{versión}} | {{versión}} |
| shiboken6 | {{versión}} | {{versión}} |
| Qt (`qVersion()`) | 6.12.0 | {{versión}} |

**Verificación del segfault** ({{total real}} tests por corrida):

| Ensayo | Corridas | exits 0 | `Segmentation fault` / `Fatal Python error` | Veredicto |
| --- | ---: | ---: | --- | --- |
| Normales | 10 | {{n}} | {{n}} | OK / FALLA |
| GC agresivo (`1,1,1`) | 5 | {{n}} | {{n}} | OK / FALLA |
| `uv run ruff check .` | 1 | {{n}} | — | OK / FALLA |

**Desviaciones:** {{las registradas en las notas `[x]` de S1–S4}}. **Sin commits ni pushes.**
