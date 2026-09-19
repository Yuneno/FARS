# AVISO FORMAL: BASELINES DE SMC-FVG INFLADOS POR ARTEFACTO DE FILL-BAR

**Fecha de Revocación:** 2026-09-19  
**Estado:** VIGENCIA REVOCADA — LECTURA HISTÓRICA ÚNICAMENTE  
**Causa:** Defecto de resolución temporal "Fill-Bar" en órdenes límite descansadas (`src/backtest/executor.py`)

---

### 1. Declaración de Revocación
Los artefactos publicados en este directorio:
- `smc_fvg_baseline_fold_metrics.json`
- `smc_fvg_risk_5_fold_metrics.json`
- `smc_fvg_risk_10_fold_metrics.json`

contienen métricas out-of-sample (WR ~58%, E[R] ~+0.09 R a +0.10 R, 8/8 y 7/8 folds positivos) que **resultaron ser un artefacto del resolutor de órdenes límite**. El motor enhanced original acreditaba Take Profit en la misma vela M5 del fill basándose en los extremos del High/Low de la vela, ignorando que el extremo que tocó el target ocurrió con anterioridad a que el precio retrocediera al nivel de la orden límite descansada.

### 2. Magnitud del Desplome (Regla Limpia A1)
Al aplicar la regla limpia A1 (donde órdenes límite descansadas solo evalúan Stop Loss en la vela del fill, y el Take Profit inicia su evaluación en la vela posterior):
- **Win Rate:** cae de 58.55% a **45.19%** (-13.37 pp).
- **Esperanza Matemática $E[R]$:** cae de +0.0977 R a **-0.1301 R** (-0.2278 R).
- **Folds Positivos:** colapsa de 8/8 a **0/8**.
- **Net R:** se desploma de +354.24 R a **-466.03 R**.

### 3. Artefactos Limpios y Vigentes
Por política de inmutabilidad y trazabilidad forense de FARS, los archivos `*.json` preexistentes en este directorio **no han sido sobreescritos ni modificados**.

Los números canónicos limpios, re-congelados con el resolutor de producción corregido (`src/backtest/executor.py`), se encuentran en:
- Informe de Re-Congelado: [`lab_artifacts/re_congelado_fillbar/RE_CONGELADO.md`](../re_congelado_fillbar/RE_CONGELADO.md)
- Datos Re-Congelados: [`lab_artifacts/re_congelado_fillbar/re_congelado_smc_fvg.json`](../re_congelado_fillbar/re_congelado_smc_fvg.json)
- Informe Completo de Auditoría: [`lab_artifacts/auditoria_fillbar/INFORME.md`](../auditoria_fillbar/INFORME.md)
- Acta de Verificación de Hermes: [`lab_artifacts/auditoria_fillbar/HERMES_REVISION_FILLBAR.md`](../auditoria_fillbar/HERMES_REVISION_FILLBAR.md)

**Conclusión:** SMC-FVG queda formalmente clasificado como **NO APTO PARA PRODUCCIÓN** y descalificado de cualquier avance en la cadena de elegibilidad.
