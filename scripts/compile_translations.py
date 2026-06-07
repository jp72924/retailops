#!/usr/bin/env python
"""Build the Spanish (es) gettext catalog without GNU gettext installed.

RetailOps targets Windows dev machines that usually lack the `gettext`
toolchain, so `django.po` / `django.mo` cannot be produced with
`makemessages` / `compilemessages`. This script is the offline substitute:
the Spanish translations live in the TRANSLATIONS / PLURALS tables below
(one source of truth), and running it writes both files:

    locale/es/LC_MESSAGES/django.po   (human-editable reference)
    locale/es/LC_MESSAGES/django.mo   (binary catalog Django loads)

Usage:
    python scripts/compile_translations.py

Any English UI string not present here falls back to English at runtime,
so a partial catalog is always safe. When GNU gettext is available you can
instead use the standard `makemessages` / `compilemessages` workflow.
"""

import struct
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
LOCALE_DIR = BASE_DIR / 'locale' / 'es' / 'LC_MESSAGES'

PLURAL_FORMS = 'nplurals=2; plural=(n != 1);'

# ── Singular translations: English msgid -> Spanish ─────────────────────────
TRANSLATIONS = {
    # Navigation / shell
    'Dashboard': 'Panel',
    'Orders': 'Pedidos',
    'Customers': 'Clientes',
    'Inventory': 'Inventario',
    'Categories': 'Categorías',
    'Payments': 'Pagos',
    'Users': 'Usuarios',
    'Settings': 'Configuración',
    'Logout': 'Cerrar sesión',
    'Language': 'Idioma',
    'Current exchange rate': 'Tasa de cambio actual',

    # Auth / login
    'Sign In': 'Iniciar sesión',
    'Unified Retail & Order Management': 'Gestión unificada de ventas y pedidos',
    'Email Address': 'Correo electrónico',
    'Password': 'Contraseña',
    'Remember me': 'Recordarme',
    'Forgot password?': '¿Olvidó su contraseña?',
    'Invalid email or password.': 'Correo o contraseña inválidos.',
    'Reset Password': 'Restablecer contraseña',
    'Reset your password': 'Restablezca su contraseña',
    "Enter your email address and we'll send you a link to reset your password.":
        'Ingrese su correo electrónico y le enviaremos un enlace para restablecer su contraseña.',
    'Send Reset Link': 'Enviar enlace de restablecimiento',
    'Back to Sign In': 'Volver a iniciar sesión',
    'Check Your Email': 'Revise su correo',
    'Check your email': 'Revise su correo',
    'Development — console email backend': 'Desarrollo — backend de correo por consola',
    'Production — send real emails': 'Producción — enviar correos reales',
    'Set New Password': 'Establecer nueva contraseña',
    'Set a new password': 'Establezca una nueva contraseña',
    'Choose a strong password for your account.': 'Elija una contraseña segura para su cuenta.',
    'New Password': 'Nueva contraseña',
    'Confirm Password': 'Confirmar contraseña',
    'At least 8 characters.': 'Al menos 8 caracteres.',
    'This reset link is invalid or has expired.': 'Este enlace de restablecimiento es inválido o ha expirado.',
    'Request a new reset link': 'Solicitar un nuevo enlace',
    'Password Reset Complete': 'Restablecimiento completado',
    'Password reset successfully': 'Contraseña restablecida con éxito',
    'Your password has been updated. You can now sign in with your new password.':
        'Su contraseña ha sido actualizada. Ya puede iniciar sesión con su nueva contraseña.',

    # Generic actions / columns
    'Name': 'Nombre',
    'Type': 'Tipo',
    'Parent': 'Principal',
    'Products': 'Productos',
    'Created': 'Creado',
    'Actions': 'Acciones',
    'Search': 'Buscar',
    'Clear': 'Limpiar',
    'Clear search': 'Limpiar búsqueda',
    'Clear filters': 'Limpiar filtros',
    'Filter': 'Filtrar',
    'Filters active': 'Filtros activos',
    'Edit': 'Editar',
    'Delete': 'Eliminar',
    'View': 'Ver',
    'Cancel': 'Cancelar',
    'Save Changes': 'Guardar cambios',
    'Back': 'Volver',
    'Email': 'Correo',
    'Phone': 'Teléfono',
    'City': 'Ciudad',
    'Country': 'País',
    'State': 'Estado',
    'Registered': 'Registrado',
    'Status': 'Estado',
    'Date': 'Fecha',
    'Total': 'Total',
    'Items': 'Artículos',
    'Customer': 'Cliente',

    # Categories
    'Product Categories': 'Categorías de productos',
    '+ New Category': '+ Nueva categoría',
    'Subcategory': 'Subcategoría',
    'No categories found': 'No se encontraron categorías',
    'Add your first product category': 'Agregue su primera categoría de productos',
    'New Category': 'Nueva categoría',
    'Edit Category': 'Editar categoría',
    'Add a new product category': 'Agregue una nueva categoría de productos',
    'Category Details': 'Detalles de la categoría',
    'e.g. Electronics': 'ej. Electrónica',
    'Description': 'Descripción',
    'Optional description…': 'Descripción opcional…',
    'Parent Category': 'Categoría principal',
    '— None (top-level category) —': '— Ninguna (categoría de nivel superior) —',
    'Create Category': 'Crear categoría',

    # Customers
    '+ Register Customer': '+ Registrar cliente',
    'ID Number': 'Cédula / ID',
    'No customers found': 'No se encontraron clientes',
    'Register your first customer': 'Registre su primer cliente',
    'Edit Customer': 'Editar cliente',
    'Register Customer': 'Registrar cliente',
    'Add a new customer to the system': 'Agregue un nuevo cliente al sistema',
    'Personal Information': 'Información personal',
    'First Name': 'Nombre',
    'First name': 'Nombre',
    'Last Name': 'Apellido',
    'Last name': 'Apellido',
    'Phone Number': 'Número de teléfono',
    'e.g. V12345678': 'ej. V12345678',
    'Date of Birth': 'Fecha de nacimiento',
    'Gender': 'Género',
    'Male': 'Masculino',
    'Female': 'Femenino',
    '— Not specified —': '— No especificado —',
    'Address': 'Dirección',
    'Address Line 1': 'Dirección línea 1',
    'Address Line 2': 'Dirección línea 2',
    'Street address': 'Dirección',
    'Apartment, suite, unit, etc.': 'Apartamento, suite, unidad, etc.',
    'Postal Code': 'Código postal',
    'ZIP / Postal': 'Código postal',
    'Internal Notes': 'Notas internas',
    'Notes': 'Notas',
    'Any additional notes about this customer…': 'Notas adicionales sobre este cliente…',
    'Contact Details': 'Datos de contacto',
    'System User': 'Usuario del sistema',
    'Linked': 'Vinculado',
    'Line 1': 'Línea 1',
    'Line 2': 'Línea 2',
    'Order History': 'Historial de pedidos',
    'No orders yet': 'Aún no hay pedidos',
    'Create first order for this customer': 'Crear el primer pedido para este cliente',
    '+ New Order': '+ Nuevo pedido',

    # Users / staff
    'Staff Management': 'Gestión de personal',
    'Manage user accounts and roles — Admin only': 'Gestione cuentas de usuario y roles — solo Admin',
    '+ Invite New User': '+ Invitar nuevo usuario',
    'Role': 'Rol',
    'Last Login': 'Último acceso',
    '(you)': '(usted)',
    'Active': 'Activo',
    'Inactive': 'Inactivo',
    'Never': 'Nunca',
    'Deactivate': 'Desactivar',
    'Reactivate': 'Reactivar',
    'No users found': 'No se encontraron usuarios',
    'Invite New Staff Member': 'Invitar nuevo miembro del personal',
    '— Select role —': '— Seleccione un rol —',
    'Temporary Password': 'Contraseña temporal',
    'Set a temporary password': 'Establezca una contraseña temporal',
    'Create User': 'Crear usuario',
    'Edit User': 'Editar usuario',
    'Back to Staff': 'Volver al personal',
    'Account Details': 'Detalles de la cuenta',
    "Changing a user's role immediately updates their permissions.":
        'Cambiar el rol de un usuario actualiza sus permisos de inmediato.',
    'Account is active': 'La cuenta está activa',
    'You cannot deactivate your own account.': 'No puede desactivar su propia cuenta.',
    'Change Password': 'Cambiar contraseña',
    'Enter new password': 'Ingrese la nueva contraseña',
    'Repeat new password': 'Repita la nueva contraseña',
    'Set Password': 'Establecer contraseña',

    # Payments
    'All recorded payments': 'Todos los pagos registrados',
    'All Methods': 'Todos los métodos',
    'All Statuses': 'Todos los estados',
    'From date': 'Desde la fecha',
    'To date': 'Hasta la fecha',
    'Payment #': 'Pago #',
    'Order #': 'Pedido #',
    'Amount': 'Monto',
    'Method': 'Método',
    'Reference': 'Referencia',
    'Receipt': 'Comprobante',
    'Recorded By': 'Registrado por',
    'No payments found': 'No se encontraron pagos',
    'No results match your filters.': 'Ningún resultado coincide con sus filtros.',
    'Payment Details': 'Detalles del pago',
    'Linked Order': 'Pedido vinculado',
    'View Order →': 'Ver pedido →',
    'Order Total': 'Total del pedido',
    'Amount Paid': 'Monto pagado',
    'Outstanding': 'Pendiente',
    'Back to Order': 'Volver al pedido',

    # Orders
    'Sales Orders': 'Pedidos de venta',
    'Manage and track all sales orders': 'Gestione y siga todos los pedidos de venta',
    '+ New Order': '+ Nuevo pedido',
    'No orders found': 'No se encontraron pedidos',
    'No orders match your filters.': 'Ningún pedido coincide con sus filtros.',
    'Create your first order': 'Cree su primer pedido',
    'New Order': 'Nuevo pedido',
    'Create a new sales order': 'Cree un nuevo pedido de venta',
    'Save Draft': 'Guardar borrador',
    'Submit for Review': 'Enviar a revisión',
    'Confirm Order': 'Confirmar pedido',
    'Record Payment': 'Registrar pago',
    'Mark Shipped': 'Marcar como enviado',
    'Mark Delivered': 'Marcar como entregado',
    'Cancel Order': 'Cancelar pedido',
    'Issue Refund': 'Emitir reembolso',
    'Select a customer for this order': 'Seleccione un cliente para este pedido',
    '— Select customer —': '— Seleccione un cliente —',
    'Line Items': 'Líneas del pedido',
    '+ Add Item': '+ Agregar artículo',
    'Product': 'Producto',
    'SKU': 'SKU',
    'Qty': 'Cant.',
    'Unit Price': 'Precio unitario',
    'Line Total': 'Total línea',
    'No items yet — click "+ Add Item" to begin.':
        'Aún no hay artículos — haga clic en "+ Agregar artículo" para comenzar.',
    'Subtotal': 'Subtotal',
    'Tax': 'Impuesto',
    'Discount': 'Descuento',
    'Grand Total': 'Total general',
    'Order Notes': 'Notas del pedido',
    'Internal notes about this order…': 'Notas internas sobre este pedido…',
    'No notes.': 'Sin notas.',
    '+ Record': '+ Registrar',
    'No payments recorded yet.': 'Aún no hay pagos registrados.',
    'Order Info': 'Información del pedido',
    'Paid': 'Pagado',
    'Payment Method': 'Método de pago',
    'Reference Number': 'Número de referencia',
    'Transaction ID, check #, etc.': 'ID de transacción, n.° de cheque, etc.',
    'Required for bank transfer, card, and check payments.':
        'Requerido para transferencias, tarjetas y cheques.',
    'Optional notes…': 'Notas opcionales…',
    'An order must have at least one line item.': 'Un pedido debe tener al menos una línea.',

    # Inventory
    'Movement History': 'Historial de movimientos',
    'Select a product to view movements.': 'Seleccione un producto para ver los movimientos.',
    'Track stock levels and product movements': 'Controle niveles de stock y movimientos de productos',
    'Adjust Stock': 'Ajustar stock',
    '+ Add Product': '+ Agregar producto',
    'All Categories': 'Todas las categorías',
    'All Stock Status': 'Todo el estado de stock',
    'In Stock': 'En stock',
    'Low Stock': 'Stock bajo',
    'Out of Stock': 'Sin stock',
    'Product Name': 'Nombre del producto',
    'Category': 'Categoría',
    'Unit': 'Unidad',
    'Stock Level': 'Nivel de stock',
    'Missing image': 'Sin imagen',
    'Adjust': 'Ajustar',
    'Movements': 'Movimientos',
    'No products found': 'No se encontraron productos',
    'No products match your filters.': 'Ningún producto coincide con sus filtros.',
    'Add your first product': 'Agregue su primer producto',
    '— Select product —': '— Seleccione un producto —',
    'Movement Type': 'Tipo de movimiento',
    'Purchase — add stock received from supplier': 'Compra — agregar stock recibido del proveedor',
    'Adjustment — correct a counting error': 'Ajuste — corregir un error de conteo',
    'Return — stock returned from a customer': 'Devolución — stock devuelto por un cliente',
    'Quantity': 'Cantidad',
    '(positive = add, negative = deduct)': '(positivo = agregar, negativo = restar)',
    'e.g. 50 or -10': 'ej. 50 o -10',
    '(optional)': '(opcional)',
    'Reason for adjustment, PO number, etc.': 'Motivo del ajuste, n.° de orden de compra, etc.',
    'Record Adjustment': 'Registrar ajuste',
    'Loading…': 'Cargando…',
    'Current stock:': 'Stock actual:',
    'No movements recorded.': 'No hay movimientos registrados.',
    'Failed to load movements.': 'Error al cargar los movimientos.',
    'Out of stock': 'Sin stock',

    # Products
    'Add Product': 'Agregar producto',
    'Edit Product': 'Editar producto',
    'Add a new product to inventory': 'Agregue un nuevo producto al inventario',
    'Back to Inventory': 'Volver al inventario',
    'Product Identity': 'Identidad del producto',
    'e.g. SKU-0001': 'ej. SKU-0001',
    'Product name': 'Nombre del producto',
    '+ New': '+ Nueva',
    '— Select category —': '— Seleccione una categoría —',
    'Name is required.': 'El nombre es obligatorio.',
    'Saving…': 'Guardando…',
    'Error creating category.': 'Error al crear la categoría.',
    'Network error. Please try again.': 'Error de red. Inténtelo de nuevo.',
    'Create': 'Crear',
    'Optional…': 'Opcional…',
    '— None (top-level) —': '— Ninguna (nivel superior) —',
    'Unit of Measure': 'Unidad de medida',
    'Optional product description…': 'Descripción opcional del producto…',
    'Product Image': 'Imagen del producto',
    'No image': 'Sin imagen',
    'Upload Image': 'Subir imagen',
    'JPEG, PNG, or WebP. Maximum size: 5 MB.': 'JPEG, PNG o WebP. Tamaño máximo: 5 MB.',
    'External Image URL': 'URL de imagen externa',
    'Remove uploaded image': 'Eliminar imagen subida',
    'Active products require either an uploaded image or an external image URL.':
        'Los productos activos requieren una imagen subida o una URL de imagen externa.',
    'Pricing & Stock': 'Precio y stock',
    'Unit Price ($)': 'Precio unitario ($)',
    'Low Stock Threshold': 'Umbral de stock bajo',
    'Alert shown when stock falls at or below this number.':
        'Se muestra una alerta cuando el stock cae a este número o por debajo.',
    'Product is active (visible and orderable)': 'Producto activo (visible y disponible para pedidos)',

    # Dashboard
    'Orders This Month': 'Pedidos este mes',
    'Sales orders created': 'Pedidos de venta creados',
    'Revenue This Month': 'Ingresos este mes',
    'From paid orders': 'De pedidos pagados',
    'Pending Payments': 'Pagos pendientes',
    'Orders awaiting payment': 'Pedidos en espera de pago',
    'Low Stock Items': 'Artículos con stock bajo',
    'Below threshold': 'Bajo el umbral',
    'Recent Orders': 'Pedidos recientes',
    'Inventory Alerts': 'Alertas de inventario',
    'View all →': 'Ver todo →',
    '✓ All stock levels are healthy': '✓ Todos los niveles de stock están saludables',
    'Quick Actions': 'Acciones rápidas',
    'View Payments': 'Ver pagos',

    # Settings
    'Manage your personal preferences and regional settings':
        'Gestione sus preferencias personales y la configuración regional',
    'Regional Preferences': 'Preferencias regionales',
    'Time Zone': 'Zona horaria',
    'All dates and times will display in this timezone.':
        'Todas las fechas y horas se mostrarán en esta zona horaria.',
    'Currency Settings': 'Configuración de moneda',
    'System-wide — applies to all users': 'A nivel de sistema — aplica a todos los usuarios',
    'Currency Code': 'Código de moneda',
    '(ISO 4217)': '(ISO 4217)',
    'Symbol': 'Símbolo',
    'Decimal Places': 'Decimales',
    'Preview: amounts will display as': 'Vista previa: los montos se mostrarán como',
    'e.g.': 'ej.',
    'Show a secondary currency alongside the primary':
        'Mostrar una moneda secundaria junto a la principal',
    'When enabled, every monetary amount gets an approximate conversion displayed in smaller muted text.':
        'Si se activa, cada monto recibe una conversión aproximada mostrada en texto pequeño y atenuado.',
    'Secondary Code': 'Código secundario',
    'Secondary Symbol': 'Símbolo secundario',
    'Secondary Decimals': 'Decimales secundarios',
    'Exchange Rate': 'Tasa de cambio',
    'Units of secondary per 1 primary.': 'Unidades de moneda secundaria por 1 principal.',
    'Preview:': 'Vista previa:',
    'Automatically update the exchange rate from an external source':
        'Actualizar automáticamente la tasa de cambio desde una fuente externa',
    'Rate Source URL': 'URL de la fuente de la tasa',
    'Rate JSON Field': 'Campo JSON de la tasa',
    'Update now': 'Actualizar ahora',
    'Receipt OCR Settings': 'Configuración de OCR de comprobantes',
    'VEPay verification for kiosk receipts': 'Verificación VEPay para comprobantes del kiosco',
    'Enable receipt OCR verification': 'Habilitar verificación OCR de comprobantes',
    'When enabled, mobile payment and bank transfer receipts can be verified through the server-side VEPay proxy.':
        'Si se activa, los comprobantes de pago móvil y transferencia se pueden verificar mediante el proxy VEPay del servidor.',
    'Provider': 'Proveedor',
    'VEPay Base URL': 'URL base de VEPay',
    'API Key': 'Clave API',
    'No key configured': 'Sin clave configurada',
    'Replace key': 'Reemplazar clave',
    'Paste new VEPay API key': 'Pegue la nueva clave API de VEPay',
    'Leave blank after choosing replace to clear the stored key.':
        'Deje en blanco tras elegir reemplazar para borrar la clave guardada.',
    'Timeout Seconds': 'Segundos de espera',
    'Max File Size MB': 'Tamaño máx. de archivo (MB)',
    'Receipt Image Retention Days': 'Días de retención de imágenes',
    'Payment Methods': 'Métodos de pago',
    'Mobile Payment': 'Pago móvil',
    'Bank Transfer': 'Transferencia bancaria',
    'Require amount to match outstanding balance': 'Exigir que el monto coincida con el saldo pendiente',
    'Require VEPay complete validation': 'Exigir validación completa de VEPay',
    'Require receipt image for kiosk mobile payment and bank transfer':
        'Exigir imagen de comprobante para pago móvil y transferencia en el kiosco',
    'When enabled, kiosk customers must upload a receipt image before these payments can be submitted.':
        'Si se activa, los clientes del kiosco deben subir una imagen del comprobante antes de enviar estos pagos.',
    'Test connection': 'Probar conexión',
    'Not tested': 'Sin probar',
    'Checking...': 'Comprobando...',
    'Connected': 'Conectado',
    'Connection failed': 'Conexión fallida',
    'Save Settings': 'Guardar configuración',

    # View flash messages
    'Please select a valid timezone.': 'Seleccione una zona horaria válida.',
    'Please select a valid language.': 'Seleccione un idioma válido.',
    'Settings saved.': 'Configuración guardada.',
}

# ── Plural translations: (msgid, msgid_plural) -> (es_singular, es_plural) ───
PLURALS = {
    ('%(counter)s category registered', '%(counter)s categories registered'):
        ('%(counter)s categoría registrada', '%(counter)s categorías registradas'),
    ('%(counter)s customer registered', '%(counter)s customers registered'):
        ('%(counter)s cliente registrado', '%(counter)s clientes registrados'),
    ('%(counter)s left', '%(counter)s left'):
        ('quedan %(counter)s', 'quedan %(counter)s'),
    ('<strong>%(counter)s product</strong> is below the low-stock threshold — restock soon.',
     '<strong>%(counter)s products</strong> are below the low-stock threshold — restock soon.'):
        ('<strong>%(counter)s producto</strong> está por debajo del umbral de stock bajo — reponga pronto.',
         '<strong>%(counter)s productos</strong> están por debajo del umbral de stock bajo — reponga pronto.'),
}

# ── blocktrans singular msgids with placeholders ────────────────────────────
TRANSLATIONS.update({
    'Filtered by "%(query)s"': 'Filtrado por "%(query)s"',
    'No results for "%(query)s".': 'No hay resultados para "%(query)s".',
    'Showing %(start)s–%(end)s of %(total)s': 'Mostrando %(start)s–%(end)s de %(total)s',
    'Edit %(name)s': 'Editar %(name)s',
    'Update details for %(name)s': 'Actualizar datos de %(name)s',
    'Customer since %(since)s': 'Cliente desde %(since)s',
    "Welcome back, %(name)s. Here's what's happening today.":
        'Bienvenido de nuevo, %(name)s. Esto es lo que ocurre hoy.',
    'Delete %(name)s? This cannot be undone.': '¿Eliminar a %(name)s? Esto no se puede deshacer.',
    'Deactivate %(name)s?': '¿Desactivar a %(name)s?',
    'Delete order %(number)s?': '¿Eliminar el pedido %(number)s?',
    'Cancel order %(number)s?': '¿Cancelar el pedido %(number)s?',
    'Issue refund for %(number)s?': '¿Emitir reembolso para %(number)s?',
    'Created by %(by)s · %(date)s': 'Creado por %(by)s · %(date)s',
    'Confirmed by %(by)s %(date)s': 'Confirmado por %(by)s %(date)s',
    'Recorded on %(date)s by %(by)s': 'Registrado el %(date)s por %(by)s',
    'Ref: %(ref)s': 'Ref: %(ref)s',
    'Outstanding balance: %(balance)s': 'Saldo pendiente: %(balance)s',
    '(min: %(threshold)s)': '(mín: %(threshold)s)',
    'Last updated: %(updated)s': 'Última actualización: %(updated)s',
    'Rate update failed: %(error)s': 'Error al actualizar la tasa: %(error)s',
    'Secondary exchange rate updated to %(rate)s.': 'Tasa de cambio secundaria actualizada a %(rate)s.',
})


def _po_escape(text):
    return (
        text.replace('\\', '\\\\')
        .replace('"', '\\"')
        .replace('\n', '\\n')
        .replace('\t', '\\t')
    )


def write_po(path):
    lines = [
        'msgid ""',
        'msgstr ""',
        '"Project-Id-Version: RetailOps\\n"',
        '"MIME-Version: 1.0\\n"',
        '"Content-Type: text/plain; charset=UTF-8\\n"',
        '"Content-Transfer-Encoding: 8bit\\n"',
        '"Language: es\\n"',
        f'"Plural-Forms: {PLURAL_FORMS}\\n"',
        '',
    ]
    for msgid, msgstr in TRANSLATIONS.items():
        lines.append(f'msgid "{_po_escape(msgid)}"')
        lines.append(f'msgstr "{_po_escape(msgstr)}"')
        lines.append('')
    for (msgid, msgid_plural), (s0, s1) in PLURALS.items():
        lines.append(f'msgid "{_po_escape(msgid)}"')
        lines.append(f'msgid_plural "{_po_escape(msgid_plural)}"')
        lines.append(f'msgstr[0] "{_po_escape(s0)}"')
        lines.append(f'msgstr[1] "{_po_escape(s1)}"')
        lines.append('')
    path.write_text('\n'.join(lines), encoding='utf-8')


def write_mo(path):
    # Build the catalog: keys/values are bytes per the GNU .mo format.
    catalog = {}
    header = (
        'Project-Id-Version: RetailOps\n'
        'MIME-Version: 1.0\n'
        'Content-Type: text/plain; charset=UTF-8\n'
        'Content-Transfer-Encoding: 8bit\n'
        'Language: es\n'
        f'Plural-Forms: {PLURAL_FORMS}\n'
    )
    catalog[b''] = header.encode('utf-8')

    for msgid, msgstr in TRANSLATIONS.items():
        catalog[msgid.encode('utf-8')] = msgstr.encode('utf-8')
    for (msgid, msgid_plural), (s0, s1) in PLURALS.items():
        key = msgid.encode('utf-8') + b'\x00' + msgid_plural.encode('utf-8')
        catalog[key] = s0.encode('utf-8') + b'\x00' + s1.encode('utf-8')

    keys = sorted(catalog.keys())
    offsets = []
    ids = b''
    strs = b''
    for key in keys:
        value = catalog[key]
        offsets.append((len(ids), len(key), len(strs), len(value)))
        ids += key + b'\x00'
        strs += value + b'\x00'

    keystart = 7 * 4 + 16 * len(keys)
    valuestart = keystart + len(ids)
    koffsets = []
    voffsets = []
    for o1, l1, o2, l2 in offsets:
        koffsets += [l1, o1 + keystart]
        voffsets += [l2, o2 + valuestart]

    output = struct.pack(
        'Iiiiiii',
        0x950412de,        # magic
        0,                 # version
        len(keys),         # number of entries
        7 * 4,             # offset of key table
        7 * 4 + len(keys) * 8,  # offset of value table
        0, 0,              # hash table size / offset (unused)
    )
    output += struct.pack('i' * len(koffsets), *koffsets)
    output += struct.pack('i' * len(voffsets), *voffsets)
    output += ids
    output += strs
    path.write_bytes(output)


def main():
    LOCALE_DIR.mkdir(parents=True, exist_ok=True)
    write_po(LOCALE_DIR / 'django.po')
    write_mo(LOCALE_DIR / 'django.mo')
    total = len(TRANSLATIONS) + len(PLURALS)
    print(f'Wrote {total} Spanish messages to {LOCALE_DIR}')


if __name__ == '__main__':
    main()
