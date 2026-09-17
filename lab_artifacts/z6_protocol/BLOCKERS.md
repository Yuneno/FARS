# BLOCKERS — Bloque Z6 (S/R con ancho + order blocks como zona)

Registro de decisiones declaradas y preguntas abiertas, en cumplimiento de la regla de honestidad de FARS.
(Completado por Hermes al cerrar el bloque; las decisiones marcadas «declarada» son las que el código aplica.)

---

### 1. Tolerancia del cluster de S/R
- **Decisión declarada:** `sr_tolerance = 1.0` puntos (la misma familia de clustering greedy 1D determinista
  que usa el motor de liquidez, sin escribir un segundo clustering).
- **Pregunta abierta:** la spec (§11 paso 9) no fija tolerancia. ¿Debe ser absoluta (puntos) o normalizada por
  volatilidad (p. ej. fracción de ATR) para que sea comparable entre mercados e instrumentos? Referencia
  externa citada por el MD, `pkg-support-resistance`, usa clustering propio: no se ha instalado ni copiado.

### 2. Lifecycle de un S/R ya testeado
- **Decisión declarada:** se reutilizan los estados del motor (`touched/swept/broken`) sin inventar semántica.
- **Pregunta abierta:** el MD (§5) recuerda que «una zona testeada puede convertirse en liquidez». ¿Debe un
  S/R `broken` migrar a pool de liquidez tipado, o mantenerse como zona histórica inerte? Determinarlo antes
  de que algún filtro lo use.

### 3. Fortaleza (strength) y su lectura causal
- **Decisión declarada:** fortaleza = evidencia acumulada disponible hasta `t` (nunca futura).
- **Pregunta abierta:** ¿debe la fortaleza decaer con el tiempo (aging) o con distancia al precio? No está en
  la spec; cualquier decaimiento sería un parámetro nuevo que exigiría preregistro propio.

### 4. Order blocks: no tocar el port
- **Decisión declarada:** el OB como zona **reproduce** la definición del port SMC-OB; no se «mejora».
- **Pregunta abierta:** si en el futuro se decide cambiar la definición (p. ej. exigir desplazamiento), el
  cambio debe entrar **con paridad rota declarada** y su propio preregistro — nunca en silencio.

### 5. Features de Z6 en `context()`
- **Decisión declarada:** Z6 pasa de `None` a valor real; **Z7 (volume voids) sigue `None`** y el set de claves
  no cambia.
- **Pregunta abierta:** los artefactos de Z5 son anteriores y no se re-midieron con las features nuevas. Si
  algún día se quiere saber si «S/R + FVG mejora el edge» (experimento §12 **#3**), hay que correr **un
  protocolo C1 nuevo** con preregistro — no reusar las tablas de Z5.
