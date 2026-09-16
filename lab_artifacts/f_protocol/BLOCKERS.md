# Bloque F · Z3-b — Preguntas Metodológicas y Registro de Blockers

**Fecha:** 2026-09-16 · **Módulo:** Pools tipados de sesión (`src/zones/session_levels.py`) · **Rama:** `bloque-f-z3b-sesion`

Siguiendo la **Regla de Honestidad (§8)** del encargo, se registran las preguntas de investigación y decisiones de diseño metodológicas adoptadas:

---

### 1. Alcance de PDH/PDL: RTH estricto vs sesión completa 24 h
- **Pregunta:** ¿Cuál alcance prefiere Kai/GPT para los anclajes del día previo: extremos de la ventana regular RTH (`[09:30, 16:00)` ET para MNQ) o el rango completo de la sesión de futuros incluyendo la ventana Globex / overnight (24 horas)?
- **Estado:** Pregunta abierta para Kai/GPT.
- **Mitigación aplicada:** Se implementó estrictamente la Convención 2 declarada: **extremos exclusivos de la ventana RTH de la sesión completada anterior**. Una barra del overnight no entra en el PDH/PDL. Esta convención es la estándar en trading institucional de índices futuros en CME (separación clara entre niveles de sesión de contado RTH y niveles de liquidez overnight).

---

### 2. Convención de timestamps del dataset canónico
- **Pregunta:** ¿Cómo vienen formateados e interpretados los timestamps en los datasets de barras canónicas M5 de Databento?
- **Evidencia empírica (Probe local ejecutado sobre `databento/MNQ_M5.csv`):**
  ```text
  total bars: 518237
  first bar ts: 2019-05-06 00:00:00+00:00 type: <class 'datetime.datetime'> tzinfo: UTC
  last bar ts: 2026-09-03 23:55:00+00:00 type: <class 'datetime.datetime'> tzinfo: UTC
  ```
- **Conclusión / Mitigación:** Los timestamps son objetos `datetime.datetime` tz-aware en `UTC`. La función canónica `src.session_calendar.session_date` maneja uniformemente tanto objetos tz-aware en UTC como datetimes naive (asumiendo UTC) y strings ISO, convirtiendo a la zona horaria del mercado (`America/New_York`) antes de aplicar la regla de rollover de las 17:00 ET y el salto de fin de semana (viernes 17:00 ET -> lunes). Cero ambigüedad.

---

### 3. Supervivencia multisesión de anclajes no rotos
- **Pregunta:** ¿Deben los anclajes de sesión sobrevivir más de una sesión si no han sido rotos (por ejemplo, que un PDH no testeado o no roto permanezca activo durante 2 o 3 sesiones consecutivas)?
- **Estado:** Pregunta abierta de modelado para investigación futura (Z5).
- **Mitigación aplicada:** Se implementó estrictamente la Convención 6 declarada: **retiro determinista al publicar un juego nuevo**. En cada rollover/apertura de una nueva sesión, el juego anterior completo pasa a `state="expired"` (estado terminal). Esto garantiza memoria acotada, determinismo estricto y evita la acumulación infinita de niveles zombis.
