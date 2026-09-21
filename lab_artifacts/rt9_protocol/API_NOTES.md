# API Notes — TopstepX / ProjectX Gateway Order Management System

**Fecha de extracción y verificación:** 2026-09-20  
**Fuente:** TopstepX / ProjectX Gateway Developer API Reference (Swagger & REST Schema)  
- Swagger UI: `https://api.topstepx.com/swagger/index.html`  
- Swagger OpenAPI JSON: `https://api.topstepx.com/swagger/v1/swagger.json`  
- Documentación oficial: `https://topstepx.com/api-docs`  
**Base URL:** `https://api.topstepx.com`  
**Autenticación:** Encabezado HTTP `Authorization: Bearer <token>`  
**Regla de Seguridad FARS:** Cero adivinanzas; únicamente endpoints documentados y verificados.

---

## 1. Autenticación y Cuentas

### 1.1 `POST /api/Auth/loginKey`
- **Propósito:** Obtener Bearer token para la sesión a partir de credenciales.
- **Request Body:**
  ```json
  {
    "userName": "string",
    "apiKey": "string"
  }
  ```
- **Response Schema:**
  ```json
  {
    "token": "string",
    "success": true,
    "errorCode": 0,
    "errorMessage": null
  }
  ```
- **Notas:** Implementado en `ProjectXClient.authenticate()` en `src/realtime/connectors/projectx.py`. El cliente de órdenes `PracticeOrderClient` reutiliza el token obtenido sin re-autenticar innecesariamente.

### 1.2 `POST /api/Account/search`
- **Propósito:** Listar cuentas del usuario y sus propiedades de trading.
- **Request Body:**
  ```json
  {
    "onlyActive": true
  }
  ```
- **Campos Oficiales de Cuenta:**
  - `id` (int): Identificador numérico interno (ej. `27765990`).
  - `name` (string): Nombre de la cuenta (ej. `PRAC-V2-673085-85699223`).
  - `balance` (number): Saldo de la cuenta en dólares.
  - `canTrade` (boolean): Flag de autorización de operativa.
  - `isVisible` (boolean): Visibilidad en UI.
  - `simulated` (boolean): `true` para cuentas Practice; `false` para cuentas Live o Combine.

---

## 2. Contratos y Resolución de Símbolos

### 2.1 `POST /api/Contract/search`
- **Propósito:** Obtener detalles del contrato activo evitando desalineaciones por expiración/roll.
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Request Body:**
  ```json
  {
    "searchText": "MNQ",
    "live": false
  }
  ```
- **Response Schema:**
  ```json
  {
    "success": true,
    "errorCode": 0,
    "errorMessage": null,
    "contracts": [
      {
        "id": "CON.F.US.MNQ.Z26",
        "name": "MNQZ6",
        "description": "Micro E-mini Nasdaq-100: December 2026",
        "tickSize": 0.25,
        "tickValue": 0.5,
        "activeContract": true,
        "symbolId": "F.US.MNQ"
      }
    ]
  }
  ```
- **Campos del Contrato:**
  - `id` (string): ID de contrato en TopstepX (ej. `CON.F.US.MNQ.Z26`).
  - `name` (string): Nombre corto (ej. `MNQZ6`).
  - `description` (string): Descripción completa.
  - `tickSize` (number): Tamaño mínimo de tick (ej. `0.25` para MNQ).
  - `tickValue` (number): Valor monetario por tick (ej. `0.50` para MNQ, equivalente a `$2.00` por punto).
  - `activeContract` (boolean): `true` si es el contrato vigente actual (sustituye al campo obsoleto `active`).
  - `symbolId` (string): Identificador canónico del símbolo (ej. `F.US.MNQ`).
- **Regla FARS:** Nunca hardcodear `contractId`. La resolución dinámica consulta el contrato con `activeContract: true` y coincidencia estricta de símbolo (sin aceptar substrings como `NQ` para `MNQ`).

---

## 3. Gestión de Órdenes

### 3.1 `POST /api/Order/place`
- **Propósito:** Enviar una orden al gateway de TopstepX.
- **Headers:** `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Request Body:**
  ```json
  {
    "accountId": 27765990,
    "contractId": "CON_MNQ_...",
    "type": 1,
    "side": 0,
    "size": 1,
    "limitPrice": 20450.50,
    "stopPrice": null,
    "customTag": "fars-prac-uuid-timestamp",
    "stopLossBracket": {
      "ticks": 40,
      "type": 4
    },
    "takeProfitBracket": {
      "ticks": 80,
      "type": 1
    }
  }
  ```
- **Tipos de Orden (`type`):**
  - `1`: Limit
  - `2`: Market
  - `4`: Stop
  - `5`: TrailingStop
- **Lados (`side`):**
  - `0`: Buy (Long)
  - `1`: Sell (Short)
- **Brackets (`stopLossBracket` / `takeProfitBracket`):**
  - Objeto opcional con `ticks` (distancia en ticks enteros desde el precio de ejecución) y `type` (`4` para stop loss, `1` para take profit limit).
- **Tag Personalizado (`customTag`):**
  - Identificador único de orden del cliente (`client_order_id`).
  - **Disciplina anti-`OrderPending` de FARS:** Toda orden enviada debe llevar un `customTag` único generado por FARS. En caso de timeout de red o falta de respuesta HTTP, FARS ejecuta una búsqueda (`search_open_orders` / `search_orders`) consultando por el `customTag` antes de reintentar o asumir el rechazo.
- **Response Schema:**
  ```json
  {
    "orderId": 89412034,
    "success": true,
    "errorMessage": null
  }
  ```

### 3.2 `POST /api/Order/cancel`
- **Propósito:** Cancelar una orden pendiente o working.
- **Request Body:**
  ```json
  {
    "accountId": 27765990,
    "orderId": 89412034
  }
  ```
- **Response Schema:**
  ```json
  {
    "success": true,
    "errorMessage": null
  }
  ```

### 3.3 `POST /api/Order/search`
- **Propósito:** Buscar historial de órdenes para una cuenta y rango de fechas.
- **Request Body:**
  ```json
  {
    "accountId": 27765990,
    "startTimestamp": "2026-09-20T00:00:00Z",
    "endTimestamp": "2026-09-20T23:59:59Z"
  }
  ```
- **Response Schema:**
  Array de objetos de orden con campos:
  - `id` (int): ID de orden asignado por el broker.
  - `accountId` (int): Cuenta propietaria.
  - `contractId` (string): Contrato.
  - `type` (int): 1=Limit, 2=Market, 4=Stop.
  - `side` (int): 0=Buy, 1=Sell.
  - `size` (int): Número de contratos.
  - `limitPrice` (number | null).
  - `stopPrice` (number | null).
  - `status` (int): 0=Working/Open, 1=Filled, 2=Cancelled, 3=Rejected.
  - `customTag` (string | null): Tag del cliente asignado por FARS.
  - `creationTimestamp` (string ISO-8601 UTC).

### 3.4 `POST /api/Order/searchOpen`
- **Propósito:** Obtener todas las órdenes actualmente activas/working en la cuenta.
- **Request Body:**
  ```json
  {
    "accountId": 27765990
  }
  ```
- **Response Schema:**
  Array de órdenes en estado working.

---

## 4. Gestión de Posiciones y Flatten

### 4.1 `POST /api/Position/searchOpen`
- **Propósito:** Obtener posiciones actualmente abiertas.
- **Request Body:**
  ```json
  {
    "accountId": 27765990
  }
  ```
- **Response Schema:**
  Array de objetos de posición:
  - `id` (int): ID de posición.
  - `accountId` (int): ID de cuenta.
  - `contractId` (string): ID de contrato.
  - `type` (int): 1=Long, 2=Short.
  - `size` (int): Tamaño neto en contratos.
  - `averagePrice` (number): Precio medio de entrada.
  - `creationTimestamp` (string ISO-8601 UTC).

### 4.2 `POST /api/Position/closeContract`
- **Propósito:** Cerrar a mercado la posición completa de un contrato específico (Flatten).
- **Request Body:**
  ```json
  {
    "accountId": 27765990,
    "contractId": "CON_MNQ_..."
  }
  ```
- **Response Schema:**
  ```json
  {
    "success": true,
    "errorMessage": null
  }
  ```

---

## 5. Declaración de Métodos NO Documentados
- **`modify_order`:** No existe un endpoint directo `/api/Order/modify` garantizado atómico en la documentación pública actual de ProjectX Gateway. Por lo tanto, FARS adopta la política fail-closed de **Cancel-and-Replace**: para modificar una orden o bracket, se cancela la orden existente (`/api/Order/cancel`) y se coloca la nueva orden (`/api/Order/place`).
