# Informe — {{NOMBRE DEL PROBLEMA O LA FEATURE}}

> **Plantilla de investigación y planificación.** Cópiala a
> `prompts/{{slug}}.md`, sustituye todos los `{{...}}` y borra este bloque.
> Este documento **no ejecuta código**: produce evidencia, decisiones y un plan
> concreto que un prompt de ejecución (`_plantilla-ejecucion.md`) convertirá en
> tareas. Leer `AGENT.md` y `README.md` antes de empezar.

- **Fecha:** {{AAAA-MM-DD}}
- **Ámbito:** {{módulos y ficheros investigados}}
- **Estado:** informe / propuesta. No se ha modificado código de la aplicación.
- **Objetivo:** {{qué capacidad se quiere obtener, para quién y con qué umbral medible}}
- **Relacionado:** {{informes anteriores, ADRs y prompts de ejecución ya cerrados}}

> **Reglas de este tipo de prompt:** distinguir siempre lo **medido** de la
> **hipótesis** y de la **propuesta**; toda cifra lleva un comando reproducible
> y las decisiones pendientes llevan recomendación. Sin tocar originales ni la
> fototeca del dueño, sin dependencias nuevas, sin commits ni pushes.

---

## 0. Misión / resumen ejecutivo

{{Párrafo corto: qué ocurre hoy, qué se quiere conseguir y por qué importa.}}

{{Si hay un objetivo de rendimiento o calidad, escribirlo como umbral verificable, no como adjetivo: «de X a Y», «< Z ms», «sin cambiar A, B y C».}}

| ID | Hallazgo / palanca | Tipo | Prioridad | Esfuerzo | Riesgo |
| --- | --- | --- | --- | --- | --- |
| {{A1}} | {{hallazgo verificable}} | Corrección / Rendimiento / Feature | {{P0/P1/P2}} | {{XS/S/M/L}} | {{Bajo/Medio/Alto}} |

**Regla de trabajo propuesta:** {{p. ej. corregir primero, medir antes de reescribir, no añadir capas para problemas locales}}. Los IDs son etiquetas; la prioridad real es la columna `Prioridad`.

---

## 1. Estado actual, evidencia y método

**Método:** {{revisión estática / perfilado / benchmark / prototipo fuera del repo}}. Se revisaron {{lista de ficheros}} sobre el estado del árbol de {{fecha o commit}}.

**Comandos reproducibles:**

```sh
{{comando de benchmark o medición con sus parámetros}}
```

**Línea base medida:**

| Métrica | Mínimo | Mediana | p95 | Condición |
| --- | ---: | ---: | ---: | --- |
| {{métrica}} | {{ms}} | {{ms}} | {{ms}} | {{banco, repeticiones, plataforma}} |

**Desglose / evidencia principal:**

| Operación | Coste | Origen (`fichero:función`) | Detalle |
| --- | ---: | --- | --- |
| {{operación}} | {{ms}} | `src/foto/...:{{función}}` | {{por qué cuesta}} |

{{Citar siempre por nombre de función además de por línea: las líneas son una foto y se desplazan.}}

**Limitaciones:** {{qué NO se ha medido, por qué y qué consecuencia tiene para las conclusiones; p. ej. arranque caliente vs. frío, offscreen vs. plataforma real}}.

---

## 2. Diagnóstico

{{Cadena causal de por qué el enfoque actual no alcanza el objetivo. Separar causa raíz de síntomas. Si una optimización previa no sirvió, explicar por qué con cifras.}}

{{Regla útil: si el trabajo se paga antes de que existan los objetos/ítems, no se puede recuperar después.}}

**Prototipos probados sin tocar el repositorio:**

| Variante | Medición | Veredicto |
| --- | ---: | --- |
| {{implementación actual}} | {{ms}} | {{baseline}} |
| {{prototipo}} | {{ms}} | {{aceptado / descartado y por qué}} |

---

## 3. Propuesta concreta por fases

> Cada punto debe indicar **fichero, función y cambio exacto**, con un snippet
> cuando aclare la intención. Ningún punto debe decir «optimizar» sin decir qué.
> Las fases condicionadas se marcan como tal y exigen autorización explícita.

### Fase {{A}} — {{título}} ({{impacto esperado}})

**{{A.1}} — {{título}}.**
En `src/foto/{{modulo}}.py`, `{{funcion}}`: {{qué cambiar, qué firma gana, qué regla se reutiliza}}. {{Snippet SQL o Python si ayuda.}}

**{{A.2}} — {{título}}.**
En `src/foto/{{modulo}}.py`, `{{funcion}}`: {{qué cambiar y qué se conserva exactamente}}.

**{{A.3}} — {{título}}.**
{{...}}

**Impacto esperado (fase {{A}} sola):** {{cifras y supuestos explícitos}}.

### Fase {{B}} — {{título}}

{{B.1, B.2, … con el mismo formato.}}

### Fase {{C}} — Condicionada: {{título}}

Solo si {{condición measurable tras A y B}}. Alcance: {{qué afecta}}. **Requiere autorización explícita**; no se ejecuta en el prompt de ejecución de las fases anteriores.

---

## 4. Impacto estimado e hipótesis a medir

| Métrica | Hoy | Tras {{A}} | Tras {{A+B}} | Naturaleza |
| --- | ---: | ---: | ---: | --- |
| {{métrica}} | {{valor}} | {{valor}} | {{valor}} | Medida / Estimación |

**Protocolo de medición:** {{n}} repeticiones, reportar mediana y p95 (no solo el mínimo), {{banco sintético y biblioteca real}}, {{plataforma}}, {{frío/caliente}}. Comparar siempre contra cifras medidas, nunca contra estimaciones de este informe.

| Hipótesis | Cómo medir | Criterio para aceptarla |
| --- | --- | --- |
| {{hipótesis}} | {{procedimiento}} | {{umbral}} |

---

## 5. Criterios de aceptación

1. {{criterio medible, con comando y umbral.}}
2. {{criterio de comportamiento: qué debe seguir funcionando igual.}}
3. {{criterio de no regresión de tests y herramientas.}}
4. `uv run pytest` y `uv run ruff check .` completos; sin dependencias nuevas; sin tocar originales, ADRs ni esquema del catálogo salvo {{excepción justificada}}.

---

## 6. Decisiones cerradas y abiertas

| # | Decisión | Elección | Consecuencia |
| --- | --- | --- | --- |
| {{1}} | {{decisión}} | {{elección}} | {{consecuencia}} |

{{Decisiones que el ADR de turno debe cerrar antes de escribir código, con recomendación y alternativa descartada.}}

---

## 7. Riesgos y mitigaciones

| Riesgo | Mitigación | Cómo se verifica |
| --- | --- | --- |
| {{riesgo}} | {{mitigación}} | {{test, medición o revisión}} |

---

## 8. Orden de ejecución recomendado

1. **{{A.1 + A.2}}** → nuevo prompt de ejecución `prompts/ejecutar-{{slug-a}}.md`. {{Por qué primero.}}
2. **{{A.3 + A.4}}**, con {{bench o prueba nueva}}. {{Aquí se cumple el objetivo principal.}}
3. **{{B…}}** como quick wins finales. Medir de nuevo y solo entonces decidir sobre la **Fase {{C}}**.

**Qué no refactorizar ahora:** {{lista con motivo; p. ej. migrar widgets, añadir capas MVC, cambiar esquema, paginar sin evidencia}}.

---

## 9. Entregable

Este fichero queda como registro de la investigación. Los pasos se ejecutan **únicamente** mediante prompts de ejecución que lo citan por número de sección; el informe no se convierte en registro de tareas ejecutadas.
