# Prompt: aplicar las recomendaciones P0 del informe de UI

> Documento para revisar antes de ejecutar. Tras la revisión, se enviará tal
> cual al agente que aplique los cambios.

## Objetivo

Aplicar exclusivamente las recomendaciones **P0** de `docs/informe-ui.md` (§4):

1. Alinear la documentación con el comportamiento real: los tests de UI se
   ejecutan **sin pantalla**, no “sin Qt”, y el acceso directo a disco para
   derivados debe quedar documentado como excepción acotada.
2. Añadir tests de regresión que detecten comandos o atajos documentados pero no
   implementados.
3. Ampliar la suite offscreen con rutas críticas de `PhotoWindow`: carga y fallo
   de imagen, prompts de búsqueda y comando, y gestión de foco.

No es un refactor. No se implementan funcionalidades nuevas ni se cambia la
arquitectura, la navegación o el vocabulario de comandos.

---

## Decisiones vinculantes y fuera de alcance

Estas decisiones ya están tomadas. No las reabras durante la ejecución:

- **Favoritos en XMP:** la decisión de alcance queda **pospuesta al final de la
  Fase 1** del roadmap y **no se acomete ahora**. No cambies el comportamiento de
  favoritos, no decidas si son MVP o fase posterior, y no alteres la entrada del
  roadmap más allá de comprobar que sigue marcada como pendiente.
- **Menú contextual de ratón:** está **aplazado** al final de la Fase 1
  (`README.md:72`, `docs/diseno-ui.md:260-262`). No lo implementes. Mantén la
  nota de pendiente y no lo presentes como función disponible.
- **Funciones retiradas:** no reintroduzcas nada de lo cubierto por
  `tests/test_ui_retired_features.py`. Ese test es la fuente canónica de los
  identificadores prohibidos.
- **Sin dependencias nuevas.** No añadas `pytest-qt` ni librerías de espera.
  Usa `QApplication`, `QT_QPA_PLATFORM=offscreen`, `QSignalSpy` y
  `processEvents` como ya hacen los tests existentes.
- **Sin cambios de producto no pedidos.** Nada de rediseños, refactors de
  `window.py` ni cambios de atajos.

---

## Lectura obligatoria antes de tocar nada

Lee completos y contrasta con el código:

1. `AGENT.md` — reglas no negociables, stack, tests y flujo de trabajo.
2. `README.md` — alcance, roadmap y descripción de la suite.
3. `docs/diseno-ui.md` — atajos, comandos, feedback y lista de verificación.
4. `docs/informe-ui.md` — §3.2.I, §4 (P0), §5 y §6.
5. `src/foto/ui/window.py` — `COMMANDS`, `_run_command`, `keyPressEvent`,
   `StatusBar`, `PromptEdit`, `PhotoCanvas` e `ImageLoadTask`.
6. `src/foto/ui/app.py` y `src/foto/ui/thumbnails.py` — composition root,
   arranque y caché.
7. `tests/test_ui_*.py` — harness offscreen, fixtures y convenciones.
8. `tests/test_ui_retired_features.py` — límites que no deben romperse.

Antes de modificar, comprueba el estado del repositorio y ejecuta la suite de UI.
No hagas `git commit`, `git push` ni cambios de configuración de git.

---

## Estado de partida

- La suite de UI pasa con **67 tests**:
  `uv run pytest -q tests/test_ui_*.py`.
- `uv run ruff check src/foto/ui tests` pasa sin avisos.
- `README.md` y `AGENT.md` todavía describen la suite como “sin Qt”.
- `docs/diseno-ui.md` afirma en la lista de verificación que la UI no tiene
  acceso directo a disco, sin matizar la decodificación de originales ni la
  caché de miniaturas.
- No existe un test que garantice que todos los comandos y atajos documentados
  están implementados.
- La cobertura offscreen no ejercita carga/fallo de imagen ni los flujos
  completos de búsqueda y comando.

---

## Tarea 1 — Alinear la documentación con el comportamiento real

### 1.1. “Sin Qt” → “sin pantalla”

Corrige solo las afirmaciones que se refieren a **toda la suite**:

- `README.md`: el comentario del árbol `tests/ # suite pytest sin Qt` debe decir
  que la suite se ejecuta sin pantalla.
- `AGENT.md`: la frase “`tests/` contiene una suite pytest sin Qt ni pantalla”
  debe distinguir:
  - tests de `core/` y `storage/`, que no necesitan Qt;
  - tests de UI, que importan PySide6 y usan `QT_QPA_PLATFORM=offscreen`.
- `AGENT.md`: el comentario del árbol `tests/ # suite pytest sin Qt` debe decir
  “sin pantalla”.

No cambies estas afirmaciones, que sí son correctas:

- `README.md`: el indexador es un ejecutable sin Qt.
- `AGENT.md`: la geometría y los nombres de la caché de miniaturas se prueban
  sin Qt.

Al terminar, `grep -rn "sin Qt" README.md AGENT.md` no debe devolver
afirmaciones sobre toda la suite.

### 1.2. Documentar la excepción de acceso a disco

En `docs/diseno-ui.md`, sustituye la casilla genérica:

```markdown
- [ ] La UI no contiene lógica de negocio ni acceso directo a disco.
```

por una formulación que conserve la regla y documente la excepción real:

```markdown
- [ ] La UI no contiene lógica de negocio. El acceso directo a disco se limita a
      la decodificación de originales del visor y a la caché de miniaturas.
```

Añade, en la sección más adecuada (`Rendimiento percibido` o `Pendiente`), una
nota breve que explique que ese acceso vive confinado en `ImageLoadTask` y
`ui/thumbnails.py`, no repartido por la ventana. No dupliques la misma idea en
varios sitios.

### 1.3. No tocar favoritos ni el menú contextual

- No modifiques el código de `_toggle_favorite` ni la documentación de XMP.
- No cambies la entrada de favoritos del roadmap salvo para comprobar que sigue
  pendiente.
- No implementes el menú contextual ni retires su nota de aplazamiento.

---

## Tarea 2 — Tests de regresión de documentación

Crea `tests/test_ui_documented_features.py` (o un nombre equivalente claro) con
dos contratos: comandos y atajos.

### 2.1. Comandos

Debe comprobar, como mínimo:

1. Todo comando documentado en `docs/diseno-ui.md` (§Línea de comandos) y en
   `src/foto/resources/ayuda.md` (§Comandos) existe en `COMMANDS` o está
   aceptado por `_run_command`.
2. Todo comando de `COMMANDS` aparece en la ayuda integrada, tal como exige
   `docs/diseno-ui.md:219`.
3. Los comandos se ejecutan de verdad, no solo existen en una tabla:
   - `sort date` y `sort camera` cambian `_sort_key` y reordenan;
   - `help` abre/cierra la ayuda;
   - un comando desconocido muestra error y no lanza excepción;
   - un comando vacío no hace nada.

Normaliza las celdas de documentación (`:q`, `:quit`, `:sort date`,
`:sort camera`, `:help`) antes de compararlas. No inventes comandos ni amplíes
`COMMANDS` para que el test pase: si la documentación y el código no coinciden,
corrige la documentación o el código según la decisión de producto ya tomada.

### 2.2. Atajos

Usa una **tabla explícita** en el test, derivada de `ayuda.md` y de
`docs/diseno-ui.md`. No intentes inferir atajos del Markdown con heurísticas
frágiles. Para cada atajo básico, comprueba el comportamiento real, no solo que
el texto aparezca en el código.

Cobertura mínima:

- Globales: `?`/`F1`, `:`, `/`, `i`, `Tab`/`Shift+Tab`, `Esc`.
- Rejilla: `j`/`k`/`h`/`l` y flechas, `0`, `$`, `g g`, `G`, `Home`, `End`,
  `PgDn`/`PgUp`, `Ctrl+d`/`Ctrl+u`, `Enter`/`o`, `f`.
- Visor: navegación, zoom, paneo, cierre con `q`/`Esc`.
- Prompts: `Tab` completa comandos, `↑`/`↓` recorren historial, `Enter` ejecuta
  o confirma, `Esc` cancela.

Para el visor puedes usar `viewer_key_action` con eventos sintéticos. Para la
ventana y los prompts, usa el harness offscreen existente y los métodos públicos
o semipúblicos que ya emplean los tests actuales (`_open_viewer`, `_run_command`,
`status.open_command`, `status.open_search`, `_apply_filter`, etc.). Si algún
atajo no se puede comprobar sin acoplarse a detalles internos, documenta el
límite en el propio test y cubre la parte observable.

El test debe fallar si se retira o se renombra una tecla documentada, y también
si se documenta una tecla nueva sin implementación.

---

## Tarea 3 — Ampliar la suite offscreen

Añade pruebas de interacción en `tests/test_ui_interaction.py` o amplía los
ficheros existentes si encaja mejor. Cubre estas rutas:

1. **Carga de imagen correcta**
   - `ImageLoadTask.run()` con una imagen temporal válida emite `loaded` con la
     clave y una `QImage` no nula.
   - `PhotoWindow` abre el visor y acaba mostrando la imagen cargada, sin
     bloquear el hilo de UI.
2. **Fallo de imagen**
   - Un fichero inexistente o inválido emite `failed`.
   - La ventana no se rompe, mantiene la miniatura o el estado vacío y no
     reintenta indefinidamente.
3. **Prompt de búsqueda**
   - `/` abre el prompt con el filtro actual.
   - Escribir filtra la rejilla.
   - `Enter` confirma y cierra; `Esc` cancela y restaura el estado anterior.
4. **Prompt de comando**
   - `:` abre el prompt.
   - `sort date` y `sort camera` cambian el orden.
   - `help` abre la ayuda.
   - Un comando desconocido muestra error.
   - `Esc` cierra sin ejecutar.
5. **Foco**
   - Abrir y cerrar el visor devuelve el foco a la vista correcta.
   - `Tab`/`Shift+Tab` mueven el foco entre rejilla y panel de metadatos cuando
     ambos están visibles.
   - Abrir y cerrar un prompt devuelve el foco a la rejilla.

Evita `sleep`. Usa `qapp.processEvents()`, `QSignalSpy.wait(...)` y fixtures
deterministas con `tmp_path`.

---

## Criterios de aceptación

- `uv run pytest -q tests/test_ui_*.py` pasa y el número de tests aumenta por
  encima de 67.
- `uv run pytest -q` pasa completo.
- `uv run ruff check src/foto/ui tests` pasa.
- `tests/test_ui_retired_features.py` sigue pasando.
- Los nuevos tests fallan si se elimina un comando o un atajo documentado.
- `README.md` y `AGENT.md` ya no dicen que toda la suite es “sin Qt”.
- `docs/diseno-ui.md` documenta la excepción de acceso a disco sin debilitar la
  regla de “sin lógica de negocio”.
- No se ha tocado el comportamiento ni la decisión pendiente de favoritos.
- No se ha implementado el menú contextual.
- No se han reintroducido las funciones retiradas.
- No hay dependencias nuevas ni cambios de arquitectura.

---

## Actualización del informe

Al terminar, actualiza `docs/informe-ui.md` de forma acotada:

- Retira o marca como resueltos los hallazgos que hayas corregido en §3.2.I y en
  §5.
- Mantén como pendientes los favoritos (decisión pospuesta al final de la Fase 1)
  y el menú contextual (aplazado).
- No reescribas el informe completo ni cambies sus conclusiones de arquitectura.

---

## Entregables

- Cambios de documentación en `README.md`, `AGENT.md` y `docs/diseno-ui.md`.
- Nuevo test de contrato de comandos y atajos.
- Nuevas pruebas offscreen de carga/fallo de imagen, prompts y foco.
- `docs/informe-ui.md` actualizado solo en los puntos resueltos.
- Resumen final con los comandos ejecutados y su resultado.
