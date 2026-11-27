# Prompt — Ejecutar {{FASE O PASO}}: {{TÍTULO}}

> **Plantilla de ejecución autónoma.** Cópiala a
> `prompts/ejecutar-{{slug}}.md`, sustituye todos los `{{...}}` y borra este
> bloque. Debe quedar **autocontenido**: el agente no necesita preguntar nada
> para empezar ni interpretar un informe por su cuenta. Leer `AGENT.md` antes
> de escribir código.

> Prompt autocontenido y ejecutable por un agente. Cubre **solo** las tareas
> **{{ID1}}–{{IDn}}** del informe
> [`{{informe}}.md`]({{informe}}.md) (§ {{secciones citadas}}). Parte de que
> {{precondiciones ya cerradas, con enlaces a los prompts que las ejecutaron y sección donde están las cifras}}.
> **No reescribas ese contrato:** este prompt lo consume y lo extiende.
>
> **Seguimiento.** Este fichero es el registro de tareas. Al terminar cada una:
> márcala `[x]` con la fecha y, si hubo desviaciones del enunciado, añade debajo
> una línea `> Nota (fecha): …`. No reordenes ni borres tareas; si aparece una
> pendiente nueva, añádela al final con el mismo formato. Una tarea solo está
> cerrada con sus tests en verde y con `uv run pytest` + `uv run ruff check .`
> completos.

## Resumen de estado

| Tarea | Alcance | Estado |
| --- | --- | --- |
| S1 | {{ID}}: {{alcance}} | Pendiente |
| S2 | {{ID}}: {{alcance}} | Pendiente |
| S{{n}} | Verificación, bench y reporte | Pendiente |

---

## 0. Misión

{{Estado medido del que se parte, en una tabla: operación, coste y dónde ocurre hoy.}}

| Operación | Coste medido | Dónde ocurre hoy |
| --- | ---: | --- |
| {{operación}} | {{ms}} | {{módulo:función}} |

**Objetivo de este prompt:** {{qué debe quedar hecho, en una frase, sin cambiar el comportamiento visible: mismos textos, orden, filtros, recuentos y teclas}}.

**Cifras de partida para comparar** ({{comando del bench}}, {{banco}}, {{repeticiones}}): {{cifras medidas}}. El informe estimaba {{estimación}}; la medición real dejó {{cifra real}}. **Compara contra las cifras medidas, no contra la estimación del informe.**

---

## 1. Lectura obligatoria antes de tocar código

1. `AGENT.md` — reglas no negociables: originales intactos, sin Qt en `core/`/`storage/`, sin `git commit`/`push` sin permiso, sin dependencias nuevas, identificadores en inglés, textos y docstrings en español.
2. `README.md` — alcance, arquitectura y roadmap. Para cambios de UI, también `docs/diseno-ui.md`.
3. `prompts/{{informe}}.md` — secciones {{lista de secciones}}.
4. {{prompts de ejecución anteriores y sección del contrato que consumen}}.
5. `docs/adr/{{NNNN}}-{{slug}}.md` — {{qué documenta}}. **No se modifica** en este prompt; documenta cualquier extensión del contrato en los docstrings del código.
6. Código real, localizando por **nombre de función** (las líneas son una foto y pueden desplazarse):
   - `src/foto/{{modulo}}.py`: `{{funcion}}` (~{{línea}}), `{{funcion2}}` (~{{línea}}).
7. Tests de referencia: {{lista de ficheros de tests}} y factorías comunes.

Antes de modificar nada, comprueba el estado del repositorio y ejecuta la verificación disponible (`uv run pytest -q` y `uv run ruff check .`). No hagas `git commit`, `git push` ni cambios de configuración de git sin permiso explícito.

---

## 2. API que debe quedar al terminar

```python
# {{modulo.py}} — {{módulo puro, sin Qt / extensión opcional}}
@dataclass(frozen=True)
class {{Tipo}}:
    {{campo}}: {{tipo}}   # {{qué contiene}}

def {{funcion}}({{argumentos}}) -> {{Tipo}}: ...

# {{otro_modulo.py}} — parámetros nuevos, opcionales; None conserva el camino actual
def {{funcion}}(..., {{parametro}}: {{Tipo}} | None = None) -> None: ...
```

{{Invariantes de la API: dónde vive cada tipo para evitar ciclos de importación, qué significa `None`, qué señales/firmas cambian y cuáles se conservan, y qué decisiones previas no se tocan.}}

---

## 3. Tareas

### [ ] S1 — {{ID del informe}}: {{título}}

1. En `src/foto/{{modulo}}.py`, `{{funcion}}`: {{paso concreto y verificable}}.
2. {{paso concreto: qué NO cambiar, qué contrato se conserva.}}
3. Añade {{test}} en `tests/{{fichero}}.py` que fije {{qué propiedad, carácter a carácter / con qué parámetros}}.
4. Verificación de rendimiento {{fuera del repositorio / con el bench del repo}}: {{comando o procedimiento}}; esperado {{umbral}}. Adjunta las cifras al reporte.

> Nota: {{restricción transversal que el agente suele incumplir; p. ej. «no reordines globalmente en el arranque», «no llames a la pasada final innecesaria»}}.

### [ ] S2 — {{ID del informe}}: {{título}}

{{Mismo formato: pasos numerados, fichero:función, tests con nombre, verificación.}}

### [ ] S{{n}} — Verificación, bench `{{flag}}` y reporte

1. `scripts/bench_arranque.py` (o el script que corresponda) gana `{{flag}}` sin dependencias nuevas: {{qué mide, qué envuelve, qué imprime, qué restaura en `finally`}}. Sin el flag, la salida de los modos existentes no cambia ni una línea.
2. Ejecuta, con la moderación que pide el tamaño del banco:

   ```sh
   {{comando de benchmark}}
   uv run pytest -q
   uv run ruff check .
   ```

3. Compara contra la línea base de § 0: {{umbrales y veredictos esperados}}. Si aparece el fallo conocido {{descripción}}, {{cómo sortearlo sin añadir scripts al repo}}.
4. Rellena el § 6 con: tests nuevos, suite completa, cifras antes/después de cada sub-tarea medida, contadores del bench y desviaciones. **No hagas commit ni push.**

---

## 4. Fuera del alcance de este prompt

- {{Fases o pasos anteriores: no reescribas sus funciones ni su semántica; solo extiendes su contrato con la pieza nueva}}.
- {{Fase condicionada que requiere autorización explícita}}.
- Cambios de esquema, migraciones, ADRs, `README.md` ni `docs/diseno-ui.md`.
- Cambios de teclas, comandos `:`, textos visibles o apariencia (salvo {{excepción invisible para el usuario}}).
- {{Subsistemas vecinos que no se tocan y por qué}}.
- Nuevas dependencias, commits, pushes o escritura en los originales de la fototeca del dueño.

---

## 5. Definición de done

- [ ] {{Pieza nueva}}, sin Qt, con paridad demostrada por test.
- [ ] {{Comportamiento del worker / pipeline}}; la UI no recalcula durante {{arranque u operación}} (contador del bench a 0).
- [ ] {{Señales/firmas nuevas}}; una sola emisión final; hilo detenido y cancelación intactas.
- [ ] {{Cambio principal}} con salida idéntica y umbral de rendimiento cumplido.
- [ ] Suite completa en verde ({{tests clave}} incluidos) y `uv run ruff check .` limpio.
- [ ] Bench `{{flag}}` ejecutado con cifras y veredictos adjuntos al reporte; sin commits/pushes y sin archivos nuevos en el repo aparte de los indicados.

---

## 6. Reporte de ejecución (rellenar al terminar)

**Tests nuevos:** {{n}} (`tests/{{fichero}}.py`: {{qué cubre}}; …). **Suite completa:** {{n}} passed (los {{m}} previos siguen en verde). **`uv run ruff check .`:** {{limpio / incidencias}}.

**Cifras antes/después** ({{banco}}, {{repeticiones}}, offscreen; mediana con mínimo entre paréntesis):

| Métrica | Antes | Después | Veredicto |
| --- | ---: | ---: | --- |
| {{métrica}} | {{ms}} | {{ms}} ({{ms}}) | OK / FALLA: {{motivo}} |

{{Resultado del modo con contadores y de los benches auxiliares; códigos de salida y problemas conocidos.}}

**Desviaciones:** las registradas en las notas `[x]` de S1–S{{n}}. {{Resumen de las principales}}. **Sin commits ni pushes.**
