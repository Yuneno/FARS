---
tags: [FARS, intradia, resumen, dictamen]
fecha: 2026-10-12
bloque: investigación intradía (orden de Ricardo 11-oct: "desde las 8 hasta que se dictamine")
estado: CERRADO — dictamen de ciclo emitido
---

# RESUMEN DEL BLOQUE 12-oct — dictamen de ciclo intradía

## Pregunta del ciclo

¿Alguna familia del catálogo tiene un edge que sobreviva el **flat diario** (todo cierra 15:10 CT,
sin overnight)? Es la única clase de edge que sirve para fondeo con bots (Topstep y Apex obligan a
flat diario y ninguno permite la combinación overnight+bot).

## Dictamen: **NINGUNA familia del catálogo sobrevive el flat diario.**

Tres líneas de evidencia independientes, todas con sus controles emparejados de cola acotada,
motor corregido (F1) y semántica flat idéntica:

| Evidencia | Estrategia | E[R] bajo flat | vs control emparejado | Veredicto |
|---|---|---:|---|---|
| Rondas 1-2 (10-11 oct) | ORB (y pool ORB MNQ+MGC) | +0,0129 (t=1,06, sin significar) | control gana +2,3 pp | sin edge certificable |
| Mira 1 (11-oct) | EMAS target-puro RR3 | **+0,0018** | empate (Δ ±0,8 pp, IC cruza) | NO CONFIRMADA |
| Mira 2 (12-oct) | zona_rebote (Z1-Z6) | **−0,0675** (−247,7 R) | le gana a su shuffle, pierde vs azar sin deriva por +10 pp | NO PROMOVIDA (perdedora) |

La tesis se mantiene con más fuerza: lo que parecía edge en estas familias vivía en las posiciones
que cruzan la noche. Con flat diario: ruido (ORB, EMAS) o pérdidas (zonas).

## Qué se hizo en el bloque

1. **Preregistros V1 y V2 congelados antes de correr** (sha256 en sus `.sha256`), criterio de éxito
   literal de Ricardo, controles de cola acotada (permutación + centrado), folds 6..7 sellados.
2. **Harness por-día reutilizable** (`run_cand1_emas.py`, `run_cand2_zonas.py` + docs de diseño):
   warmup 500 barras + flat 15:10 CT DST-safe vía `end_of_data_policy="close"`, yardstick Topstep
   50K corregido (motor F1: ratchet EOD, day_close, exclusion mutua), MC B=2.000, sensibilidad
   ±ticks, manifest con hashes. 774 días por corrida, ~30 min.
3. Mira 3 (bias EMAS × zonas) no corrió: prerequisito no cumplido (ningún componente con señal),
   regla anti-confluencias.

## Hallazgos registrados (sin mover umbrales)

- **HUECO DEL CRITERIO:** el criterio de éxito no fijaba el signo de la deriva; una candidata
  perdedora puede "superar" a la permutación de sí misma. Para futuros preregistros: condición previa
  **E[R] > 0 con IC95 inferior > 0**. No aplicado retroactivamente.
- El orden temporal de los trades SÍ importa para el motor de cuenta (mira 2: −2,8 pp vs shuffle con
  IC sin cruzar) — pero con deriva negativa es irrelevante para fondeo.
- Quedan por testear (fuera de este ciclo, con nuevo preregistro si se reabre): CRT-TBS (única
  históricamente positiva con overnight), señales externas (APARCADAS hasta que Juanca pase los
  repos fuente), y combinaciones (prohibidas hasta que un componente demuestre señal).

## Estado del proyecto al cierre del bloque

- **Fondeo Topstep: NO PAGAR** (ronda 1). **Apex: DESCARTADA** por cumplimiento (ronda 2).
- **Estrategias con flat diario: ninguna con edge** (este bloque).
- **Motor de cuentas: corregido y aprobado** (F1, Codex APTO, suite 1.803✅) en rama `fix/motor-r2`
  — pendiente merge a main con OK de Ricardo.
- **Mercado:** nada se lanza hasta que algo funcione en históricos (regla de Ricardo 11-oct). RT-9
  Practice listo para cuando eso ocurra.
- **Próximo paso lógico (decisión de Ricardo):** (a) aceptar el dictamen y explorar con más tiempo/fuera
  del catálogo (p.ej. cuando Juanca pase los repos, o señales nuevas), (b) reabrir el ciclo con más
  miras y presupuesto de tiempo nuevo, o (c) pausar el tema fondeo y concentrarse en el pipeline.
