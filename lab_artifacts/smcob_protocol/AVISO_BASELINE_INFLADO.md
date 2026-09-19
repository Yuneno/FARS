# AVISO FORMAL: BASELINES DE SMC-OB INFLADOS POR ARTEFACTO DE FILL-BAR

**Fecha de Revocación:** 2026-09-19  
**Estado:** VIGENCIA REVOCADA — LECTURA HISTÓRICA ÚNICAMENTE  
**Causa:** Defecto de resolución temporal "Fill-Bar" en órdenes límite descansadas (`src/backtest/executor.py`)

---

### 1. Declaración de Revocación
El artefacto canónico de resultados publicado en este directorio:
- `resultados.json`

y los informes derivados en `SUMMARY.md` contienen métricas multimercado (MNQ WR 60.23%, E[R] +0.0724 R, Net R +126.99 R, 6/8 folds positivos) que **resultaron ser un artefacto puro del resolutor de órdenes límite**.  
En órdenes límite descansadas, el ejecutor enhanced acreditaba tanto el Take Profit completo como el `tp1` parcial (cerrando 50% de la posición en ganancia y moviendo stop a break-even) en la misma vela M5 del fill, adjudicando movimientos de precio que ocurrieron en la vela antes de la ejecución del límite.

### 2. Nulidad de la Simulación de Cuenta Apex (E1)
> [!CAUTION]
> **SIMULACIÓN APEX (E1) REVOCADA Y DECLARADA NULA:**  
> La simulación de cuenta de fondeo Apex 25K (E1) documentada en `SUMMARY.md` y asignada condicionalmente a SMC-OB queda **oficialmente revocada y declarada sin validez**. La distribución empírica OOS que alimentaba el remuestreo de Monte Carlo tenía una esperanza matemática artificialmente positiva generada por el resolutor defectuoso.

### 3. Magnitud del Desplome (Regla Limpia A1)
Bajo la regla de resolución limpia A1:
- **MNQ:**
  - Win Rate cae de 60.23% a **44.12%** (-16.11 pp).
  - $E[R]$ cae de +0.0724 R a **-0.1512 R** (-0.2236 R).
  - Folds positivos colapsan de 6/8 a **0/8**.
  - Net R cae de +126.99 R a **-263.55 R** (-390.54 R de impacto neto, con 288 flips netos de trades).
- **MYM:**
  - Win Rate cae de 58.03% a **45.27%** (-12.76 pp).
  - $E[R]$ cae de -0.0300 R a **-0.2090 R** (0/8 folds positivos).
- **MGC:**
  - Win Rate cae de 57.87% a **44.47%** (-13.40 pp).
  - $E[R]$ cae de -0.0880 R a **-0.2838 R** (0/8 folds positivos).

### 4. Artefactos Limpios y Vigentes
Por política de inmutabilidad y trazabilidad forense de FARS, `resultados.json` **no ha sido sobreescrito ni modificado**.

Los datos canónicos limpios, re-congelados con el resolutor de producción corregido (`src/backtest/executor.py`), se encuentran en:
- Informe de Re-Congelado: [`lab_artifacts/re_congelado_fillbar/RE_CONGELADO.md`](../re_congelado_fillbar/RE_CONGELADO.md)
- Datos Re-Congelados: [`lab_artifacts/re_congelado_fillbar/re_congelado_smc_ob.json`](../re_congelado_fillbar/re_congelado_smc_ob.json)
- Informe Completo de Auditoría: [`lab_artifacts/auditoria_fillbar/INFORME.md`](../auditoria_fillbar/INFORME.md)
- Acta de Verificación de Hermes: [`lab_artifacts/auditoria_fillbar/HERMES_REVISION_FILLBAR.md`](../auditoria_fillbar/HERMES_REVISION_FILLBAR.md)

**Conclusión:** SMC-OB queda formalmente clasificado como **NO APTO PARA PRODUCCIÓN**, con suspensión total de asignación de cuentas y de cualquier prueba posterior.
