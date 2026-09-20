# BLOCKERS — Bloque RT-9: Órdenes Reales en Cuenta Practice

**Fecha:** 2026-09-20  
**Rama:** `bloque-rt9-ordenes-practice`  
**Base:** `6cbe1cb` (`bloque-circuit-breakers-sesion`)  
**Estado:** **0 BLOQUEOS TÉCNICOS CRÍTICOS**

---

## 1. Estado de Bloqueos Técnicos
- **Implementación de Código:** Completa sin errores ni advertencias.
- **Pruebas Offline:** 100% aprobadas (263/263 tests unitarios e integrados en verde).
- **Arnés de Aceptación (Offline):** PASS en los 8 pasos del protocolo.
- **Aislamiento de Cuentas:** Totalmente protegido. Cuentas de combine (`1.5KCHCR-...`) y live son rechazadas a nivel de validación antes de cualquier llamada. `LIVE_EXECUTION_ENABLED = False` se mantiene intacto.

---

## 2. Ventana de Mercado en Vivo (Acción Pendiente Programada)
- **Condición:** El mercado de futuros CME (NQ/MNQ) se encuentra cerrado durante el fin de semana hasta las 6:00 PM US Eastern (5:00 PM CT) del domingo 2026-09-20.
- **Instrucción de Ricardo:** Construir y validar offline primero; la ejecución en vivo se corre en la ventana autorizada con mercado abierto sin colisionar con S2.
- **Acción al abrir ventana:**
  ```powershell
  E:\FARS-LAB\.venv-fars\Scripts\python.exe -m src.realtime.acceptance_rt9 --live
  ```
  La cuenta Practice `PRAC-V2-673085-85699223` (id `27765990`) ya está configurada en `.env` y validada por `fars-projectx doctor`.
