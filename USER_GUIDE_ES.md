# RetailOps — Guía del Usuario

Esta guía explica el uso diario de RetailOps: cómo iniciar sesión, registrar clientes, realizar y procesar pedidos, registrar pagos y gestionar el inventario. No se requieren conocimientos técnicos.

---

## Tabla de Contenidos

1. [Roles y lo que puede hacer cada uno](#1-roles-y-lo-que-puede-hacer-cada-uno)
2. [Iniciar y cerrar sesión](#2-iniciar-y-cerrar-sesión)
3. [El Tablero](#3-el-tablero)
4. [Gestión de Clientes](#4-gestión-de-clientes)
5. [Procesar un Pedido de Venta — De principio a fin](#5-procesar-un-pedido-de-venta--de-principio-a-fin)
6. [Registrar un Pago](#6-registrar-un-pago)
7. [Cancelar un Pedido](#7-cancelar-un-pedido)
8. [Emitir un Reembolso](#8-emitir-un-reembolso)
9. [Gestión de Inventario](#9-gestión-de-inventario)
10. [Gestión de Cuentas de Personal (Solo Administrador)](#10-gestión-de-cuentas-de-personal-solo-administrador)

---

## 1. Roles y lo que puede hacer cada uno

Cada usuario en RetailOps tiene asignado uno de tres roles. Su rol determina qué botones y páginas puede ver.

| Acción | Personal | Gerente | Administrador |
|--------|:-----:|:-------:|:-----:|
| Ver tablero, clientes, pedidos, pagos, inventario | Sí | Sí | Sí |
| Registrar y editar clientes | Sí | Sí | Sí |
| Crear un nuevo pedido de venta | Sí | Sí | Sí |
| Enviar un pedido para revisión (Borrador → Pendiente) | Sí | Sí | Sí |
| Aprobar/confirmar un pedido (Pendiente → Confirmado) | — | Sí | Sí |
| Marcar un pedido como Enviado / Entregado | Sí | Sí | Sí |
| Cancelar un pedido confirmado | — | Sí | Sí |
| Emitir un reembolso | — | — | Sí |
| Añadir o editar productos | — | Sí | Sí |
| Gestionar cuentas de personal | — | — | Sí |

Si intenta realizar una acción que su rol no permite, el sistema mostrará una página de "Permiso Denegado".

---

## 2. Iniciar y cerrar sesión

### Iniciar sesión

1. Abra RetailOps en su navegador. Llegará a la página de **Inicio de Sesión** automáticamente.
2. Ingrese su **correo electrónico** y **contraseña**.
3. Haga clic en **Iniciar sesión**.
4. Será llevado al **Tablero**.

Si ve "Correo o contraseña inválidos", verifique sus credenciales. Contacte a su Administrador si no puede iniciar sesión.

### Cerrar sesión

Haga clic en su nombre o avatar en la esquina superior derecha de la barra de navegación y luego en **Cerrar sesión**. Volverá a la página de Inicio de Sesión.

---

## 3. El Tablero

El Tablero es su pantalla de inicio. Le ofrece un resumen rápido del negocio:

- **Tarjetas de resumen** en la parte superior muestran los pedidos de este mes, los ingresos totales, los pagos pendientes y cuántos productos tienen existencias bajas.
- La pestaña **Pedidos Recientes** enumera los cinco pedidos más recientes con su estado actual.
- La pestaña **Alertas de Inventario** enumera los productos que han caído por debajo de su umbral de existencias bajas.
- La barra lateral **Acciones Rápidas** tiene botones de acceso directo para las tareas más comunes: Nuevo Cliente, Nuevo Pedido, Registrar Pago, Añadir Producto.

Utilice la barra de navegación superior para moverse entre secciones: Clientes, Pedidos, Pagos, Inventario y (solo para Administradores) Usuarios.

---

## 4. Gestión de Clientes

Cada pedido debe estar vinculado a un cliente, por lo que los clientes deben registrarse antes de poder hacer un pedido para ellos.

### Registrar un nuevo cliente

1. Haga clic en **Clientes** en la barra de navegación.
2. Haga clic en el botón **Nuevo Cliente** (parte superior derecha de la página).
3. Complete el formulario:
   - **Nombre** y **Apellido** — obligatorios.
   - **Correo electrónico** — obligatorio; debe ser único entre todos los clientes.
   - **Teléfono** — opcional.
   - **Dirección** — La línea de dirección 1 es obligatoria; ciudad, estado, código postal y país también son obligatorios.
   - **Notas** — cualquier nota de texto libre sobre el cliente (por ejemplo, método de contacto preferido, instrucciones especiales).
4. Haga clic en **Guardar Cliente**.
5. Será llevado al perfil del cliente, que muestra sus datos de contacto y el historial completo de pedidos.

### Editar un cliente

1. Desde la lista de clientes o su página de perfil, haga clic en **Editar**.
2. Realice sus cambios.
3. Haga clic en **Guardar Cliente**.

### Buscar un cliente

En la página de Clientes, escriba un nombre o correo electrónico en la barra de búsqueda y presione Enter. La lista se filtrará para mostrar los registros coincidentes.

### Eliminar un cliente

Un cliente solo se puede eliminar si no tiene pedidos registrados. Haga clic en **Eliminar** en su perfil y confirme la acción. Si el botón no está visible o la acción falla, el cliente tiene pedidos existentes y no se puede eliminar.

---

## 5. Procesar un Pedido de Venta — De principio a fin

Un pedido de venta pasa por estas etapas en orden:

```
Borrador → Pendiente → Confirmado → Pagado → Enviado → Entregado
```

Cada etapa se describe a continuación.

---

### Paso 1 — Crear el pedido (Borrador)

*Quién puede hacerlo: Personal, Gerente, Administrador*

1. Haga clic en **Pedidos** en la barra de navegación y luego en **Nuevo Pedido**.
2. Seleccione el **Cliente** del desplegable. Si el cliente no aparece, regístrelo primero (consulte la Sección 4).
3. Añada líneas de pedido:
   - Haga clic en **Añadir Ítem**.
   - Seleccione un **Producto** del desplegable o escriba un SKU. El precio unitario se completará automáticamente según el precio actual del producto.
   - Ingrese la **Cantidad**.
   - Repita para cada producto del pedido.
   - Para eliminar una línea, haga clic en el icono de papelera en esa fila.
4. Opcionalmente, complete:
   - **Descuento** — una cantidad fija para restar del subtotal.
   - **Impuesto** — una cantidad fija de impuesto para añadir.
   - **Notas** — cualquier nota interna sobre este pedido.
5. Revise los totales en la parte inferior de la tabla de líneas de pedido.
6. Haga clic en **Guardar Pedido**.

El pedido se guarda como **Borrador**. Se asigna automáticamente un número de pedido único (formato: `SO-YYYYMMDD-XXXX`). El pedido aún se puede editar libremente mientras está en Borrador.

---

### Paso 2 — Enviar para revisión (Borrador → Pendiente)

*Quién puede hacerlo: Personal, Gerente, Administrador*

Una vez que el pedido parezca correcto, envíelo para que un gerente lo revise:

1. Abra el pedido (haga clic en su número de pedido en la lista de Pedidos).
2. Haga clic en el botón **Enviar para Revisión**.
3. El estado cambia a **Pendiente**. El pedido ahora está bloqueado contra ediciones posteriores.

---

### Paso 3 — Confirmar el pedido (Pendiente → Confirmado)

*Quién puede hacerlo: Gerente, Administrador*

Un gerente o administrador revisa el pedido y lo aprueba:

1. Abra el pedido Pendiente.
2. Verifique que el cliente, los productos, las cantidades y los totales sean correctos.
3. Haga clic en **Confirmar Pedido**.
4. El estado cambia a **Confirmado**. En este punto, las existencias se deducen automáticamente del inventario.

Si hay algún problema con el pedido, se puede cancelar en esta etapa (consulte la Sección 7).

---

### Paso 4 — Registrar pago (Confirmado → Pagado)

*Quién puede hacerlo: Personal, Gerente, Administrador*

Una vez confirmado el pedido, se puede registrar el pago:

1. Abra el pedido Confirmado.
2. Haga clic en **Registrar Pago**. Aparecerá un formulario de pago (en un modal o panel lateral).
3. Complete:
   - **Monto** — la cantidad recibida. Puede registrar pagos parciales; el pedido pasa a Pagado solo cuando se cubre el monto total.
   - **Método de Pago** — Efectivo, Transferencia Bancaria, Tarjeta, Cheque u Otro.
   - **Número de Referencia** — opcional; un número de cheque, ID de transferencia bancaria, etc.
   - **Notas** — nota interna opcional.
4. Haga clic en **Guardar Pago**.

Se asigna automáticamente un número de pago único (formato: `PAY-YYYYMMDD-XXXX`). Si el total de pagos registrados iguala o supera el total del pedido, el estado del pedido cambia automáticamente a **Pagado**.

Puede registrar múltiples pagos parciales contra el mismo pedido. Cada uno aparece en el historial de pagos de la página de detalle del pedido.

---

### Paso 5 — Marcar como Enviado (Pagado → Enviado)

*Quién puede hacerlo: Personal, Gerente, Administrador*

Cuando la mercancía sale de su almacén o tienda:

1. Abra el pedido Pagado.
2. Haga clic en **Marcar como Enviado**.
3. El estado cambia a **Enviado**.

---

### Paso 6 — Marcar como Entregado (Enviado → Entregado)

*Quién puede hacerlo: Personal, Gerente, Administrador*

Cuando se confirma la entrega:

1. Abra el pedido Enviado.
2. Haga clic en **Marcar como Entregado**.
3. El estado cambia a **Entregado**. Este es el estado final y completado para un pedido normal.

---

## 6. Registrar un Pago

Los pagos siempre se registran desde la página de detalle del pedido (consulte el Paso 4 en la Sección 5 anterior). También puede ver todos los pagos haciendo clic en **Pagos** en la barra de navegación.

La lista de Pagos muestra cada pago con su número de pago, pedido vinculado, monto, método y fecha. Haga clic en cualquier número de pago para ver el recibo completo del pago, incluido el resumen del pedido vinculado.

**Aspectos clave sobre los pagos:**

- Los pagos son **manuales** — no hay procesamiento automático de tarjetas. Usted registra lo que se recibió.
- Un pedido puede tener **múltiples pagos** (se admiten pagos parciales). El pedido se vuelve Pagado cuando los pagos acumulados cubren el total.
- Los pagos no se pueden eliminar a través de la interfaz. Para revertir un pago, el pedido debe ser reembolsado (solo Administrador — consulte la Sección 8).

---

## 7. Cancelar un Pedido

*Quién puede hacerlo: Gerente, Administrador — solo mientras el pedido está Confirmado*

Un pedido se puede cancelar después de haber sido confirmado pero antes de que se registre el pago:

1. Abra el pedido Confirmado.
2. Haga clic en **Cancelar Pedido**.
3. El estado cambia a **Cancelado**. Las existencias que se dedujeron cuando se confirmó el pedido se devuelven automáticamente al inventario.

> **Nota:** Los pedidos en Borrador o Pendiente se pueden eliminar por completo (no solo cancelar) desde la lista de Pedidos, ya que aún no se han tocado las existencias. Solo un pedido en Borrador se puede eliminar permanentemente; una vez enviado, debe pasar por el proceso de cancelación.

---

## 8. Emitir un Reembolso

*Quién puede hacerlo: Solo Administrador — solo mientras el pedido está Pagado*

Si es necesario revertir un pedido pagado:

1. Abra el pedido Pagado.
2. Haga clic en **Emitir Reembolso**.
3. El estado cambia a **Reembolsado**. Las existencias se añaden automáticamente de nuevo al inventario.

Los reembolsos son una acción de registro contable — marcan el pedido como reembolsado en el sistema. La devolución real del dinero al cliente se maneja fuera del sistema (devolución en efectivo, transferencia bancaria, etc.).

---

## 9. Gestión de Inventario

Haga clic en **Inventario** en la barra de navegación para ver todos los productos y sus niveles actuales de existencias.

### Leer la lista de inventario

- Cada fila muestra el SKU, nombre, categoría, unidad de medida, precio unitario y existencias actuales de un producto.
- Una fila o banner de **advertencia amarilla** indica que las existencias del producto están por debajo de su umbral de existencias bajas.
- Un indicador **rojo** significa que el producto está agotado.
- Utilice la **barra de búsqueda** para filtrar por SKU o nombre de producto.
- Utilice el desplegable **Categoría** para filtrar por categoría de producto.
- Utilice el filtro **Estado de Existencias** para mostrar solo productos con existencias bajas, agotados o saludables.

### Ver el historial de movimientos de existencias

Haga clic en el **icono de historial** (o botón "Ver Movimientos") en cualquier fila de producto. Se abre un panel lateral que muestra los últimos 50 movimientos de existencias de ese producto: compras, ventas, ajustes y devoluciones, con fechas, cantidades y quién realizó cada registro.

### Añadir un nuevo producto

*Quién puede hacerlo: Gerente, Administrador*

1. En la página de Inventario, haga clic en **Añadir Producto**.
2. Complete:
   - **SKU** — un identificador único para el producto (ej. `SHOE-RED-42`).
   - **Nombre** — el nombre visible.
   - **Categoría** — seleccione de las categorías existentes.
   - **Unidad de Medida** — Pieza, Kilogramo, Litro, Metro, Caja o Paquete.
   - **Precio Unitario** — el precio de venta predeterminado. Se puede sobrescribir por línea de pedido.
   - **Umbral de Existencias Bajas** — la cantidad por debajo de la cual el sistema marcará este producto como con existencias bajas. El valor predeterminado es 10.
   - **Descripción** — opcional.
3. Haga clic en **Guardar Producto**.

El producto comienza con existencias cero. Las existencias aumentan cuando recibe inventario (registrado como un ajuste manual o movimiento de compra) y disminuyen automáticamente cuando se confirman los pedidos.

### Editar un producto

*Quién puede hacerlo: Gerente, Administrador*

1. Encuentre el producto en la lista de Inventario.
2. Haga clic en el botón **Editar** en su fila.
3. Actualice los campos según sea necesario.
4. Haga clic en **Guardar Producto**.

> **Nota:** Cambiar el precio unitario de un producto aquí no afecta los pedidos ya realizados. Las líneas de pedido fijan el precio en el momento en que se crea el pedido.

---

## 10. Gestión de Cuentas de Personal (Solo Administrador)

Haga clic en **Usuarios** en la barra de navegación (visible solo para Administradores) para gestionar quién tiene acceso a RetailOps.

### Invitar a un nuevo usuario

1. En la página de Usuarios, haga clic en **Invitar Usuario**. Aparecerá un formulario.
2. Complete:
   - **Nombre** y **Apellido**.
   - **Correo electrónico** — será su nombre de usuario para iniciar sesión.
   - **Rol** — Personal, Gerente o Administrador.
   - **Contraseña** — establezca una contraseña inicial para ellos. Pídales que la cambien después del primer inicio de sesión.
3. Haga clic en **Enviar Invitación** (o Guardar, según la versión).

El usuario ahora puede iniciar sesión con el correo electrónico y la contraseña que proporcionó.

### Editar un usuario

1. Encuentre al usuario en la lista.
2. Haga clic en **Editar** en su fila.
3. Puede actualizar su nombre, rol y contraseña.
4. Haga clic en **Guardar**.

### Desactivar un usuario

Si un miembro del personal se va, desactive su cuenta en lugar de eliminarla (para que sus acciones históricas se conserven):

1. Encuentre al usuario en la lista.
2. Haga clic en **Desactivar**.
3. El estado del usuario cambia a Inactivo. Ya no podrá iniciar sesión.

No puede desactivar su propia cuenta.

### Reactivar un usuario

1. Encuentre al usuario inactivo en la lista (se mostrará con una insignia de Inactivo).
2. Haga clic en **Reactivar**.
3. Podrá iniciar sesión nuevamente de inmediato.

---

## Referencia Rápida — Resumen de Estados de Pedido

| Estado | Qué significa | Siguiente acción |
|--------|--------------|------------------|
| **Borrador** | Creado, aún no revisado | Enviar para Revisión |
| **Pendiente** | Esperando aprobación del gerente | Confirmar (Gerente/Administrador) o Eliminar |
| **Confirmado** | Aprobado; existencias reservadas | Registrar Pago |
| **Pagado** | Pago recibido en su totalidad | Marcar como Enviado |
| **Enviado** | Mercancía despachada | Marcar como Entregado |
| **Entregado** | Pedido completado | — |
| **Cancelado** | Cancelado antes del pago; existencias restauradas | — |
| **Reembolsado** | Pedido pagado revertido; existencias restauradas | — |