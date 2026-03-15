# Flujo de Datos — cocoScraper

Este documento explica, de punta a punta, cómo el sistema obtiene precios de los proveedores, los normaliza y los presenta en el dashboard.

---

## Visión general

```
Sitios web de proveedores
        │
        ▼
   [ Scraper ]  ──────────────────────────────────────────────────
        │  extrae nombre, SKU, precio, stock por categoría        │
        ▼                                                          │
  [ Base de datos ]                                               │
   tabla products      ← datos crudos del proveedor              │
   tabla price_snapshots ← historial de precios                   │
        │                                                          │
        ▼                                                          │
   [ Pipeline de postprocesamiento ]  (se ejecuta automáticamente)│
        │  normaliza marca, tipo, tamaño, categoría               │
        ▼                                                          │
  [ Base de datos ]                                               │
   tabla products  ← ahora con brand, product_type, canonical_key │
        │                                                          │
        ▼                                                          │
   [ Dashboard ]                                                   │
   exploración, comparación, historial de precios ────────────────
```

---

## 1. Scraping

### ¿Qué es?

El scraper visita los sitios web de cada proveedor y extrae los productos disponibles con sus precios del día.

### Proveedores soportados

| Proveedor | Código | Plataforma | Login |
|---|---|---|---|
| Maxiconsumo | `mx` | Sitio propio (HTML) | Sí |
| Santa María | `sm` | osCommerce (HTML) | No |
| Luvik | `lv` | Shopify (JSON API) | No |
| Vital | `vt` | VTEX (JSON API) | Sí |
| Nini | `nn` | ASP.NET propio (JSON API) | Sí |

### ¿Qué datos extrae?

Por cada producto se obtiene:

- **SKU**: identificador interno del proveedor
- **Nombre**: nombre del producto tal como aparece en el sitio
- **Categoría**: categoría del proveedor (varía por sitio)
- **Precio unitario** (`price_unit`): precio por unidad de venta
- **Precio mayorista** (`price_bulk`): precio por caja/pallet
- **Stock**: disponibilidad

### ¿Cómo se ejecuta?

```bash
# Scrapear todos los proveedores
python -m scraper.main scrape

# Scrapear un proveedor específico
python -m scraper.main scrape --supplier maxiconsumo
```

### Deduplicación de precios

No se guarda un nuevo snapshot si el precio unitario y mayorista son idénticos al último registrado. Esto mantiene la tabla de historial pequeña y limpia: solo se registra cuando algo cambia.

---

## 2. Almacenamiento en base de datos

### Tabla `products`

Cada combinación `(sku, supplier)` es única. Si el mismo producto ya existe, se actualiza su nombre, categoría y precios — nunca se duplica.

Campos clave:
- `sku` + `supplier` → clave primaria compuesta
- `product_id` → ID portátil único (ej: `mx_328`, `nn_14230`)
- `name` → nombre crudo del proveedor
- `brand`, `product_type`, `size_value`, `size_unit` → campos normalizados (ver sección 3)
- `category_dept`, `category_sub` → categoría unificada (ver sección 4)
- `canonical_key` → clave de matching entre proveedores (ver sección 5)
- `last_scraped_at` → fecha/hora de la última vez que fue visto en un scrape

### Tabla `price_snapshots`

Historial completo de precios. Cada fila es un punto en el tiempo:
- `(sku, supplier, scraped_at)` → único por día
- `price_unit`, `price_bulk`, `stock`

### Tabla `run_log`

Registro de cada ejecución del scraper:
- Cuándo comenzó y terminó
- Cuántos productos se procesaron
- Si hubo errores

---

## 3. Postprocesamiento y normalización

### El problema

Cada proveedor nombra sus productos de forma diferente:

| Proveedor | Nombre crudo del producto |
|---|---|
| Maxiconsumo | `ACEITE COCINERO GIRASOL 1,5 LT` |
| Santa María | `Aceite Girasol Cocinero 1500 ml` |
| Luvik | `COCINERO Aceite de Girasol 1.5 lts` |

Son el mismo producto, pero no hay forma directa de saberlo sin normalizar.

### La solución: extracción de features

Cada proveedor tiene su propio postprocesador (`scraper/postprocess/<proveedor>.py`) que analiza el nombre crudo y extrae:

| Campo | Ejemplo | Descripción |
|---|---|---|
| `brand` | `Cocinero` | Marca del producto |
| `product_type` | `Aceite` | Tipo de producto |
| `variant` | `Girasol` | Variante o sabor |
| `size_value` | `1500.0` | Tamaño numérico |
| `size_unit` | `ml` | Unidad: g, ml, uni, m… |

### ¿Cuándo se ejecuta?

El pipeline se ejecuta **automáticamente** al finalizar cada scrape. También se puede correr manualmente:

```bash
# Procesar productos sin features (solo los nuevos)
python -m scraper.postprocess.pipeline

# Reprocesar todos los productos (útil si mejoró el extractor)
python -m scraper.postprocess.pipeline --force

# Ver qué product_types aún no tienen categoría asignada
python -m scraper.postprocess.pipeline --list-unmapped
```

---

## 4. Taxonomía de categorías unificada

### El problema

Las categorías de los proveedores son inconsistentes:

- Maxiconsumo: `Almacen > Aceites Y Vinagres > Aceites`
- Vital: `Almacenes`
- Nini: `ALIMENTOS 1`

### La solución

Se usa el `product_type` normalizado (que sí es consistente entre proveedores) para asignar una categoría unificada de dos niveles:

```
Departamento  →  Subcategoría
────────────────────────────
Almacén       →  Aceites y Grasas
Almacén       →  Salsas y Aderezos
Bebidas       →  Gaseosas
Lácteos       →  Leche
Limpieza      →  Detergentes
...
```

El mapeo está definido en el archivo de datos:
```
scraper/postprocess/data/unified_categories.txt
```

Formato:
```
Almacén|Aceites y Grasas|Aceite
Bebidas|Gaseosas|Gaseosa
Lácteos|Leche|Leche
```

Para extender la taxonomía, solo hay que agregar líneas a ese archivo — sin tocar código.

Ver la referencia completa en [docs/categories.md](categories.md).

---

## 5. ID único de producto (`product_id`)

### El problema

El SKU es asignado por cada proveedor y no es comparable entre ellos. El mismo número puede referirse a productos completamente distintos en diferentes proveedores.

### La solución

Se asigna un `product_id` portátil combinando el código corto del proveedor con el SKU:

| Proveedor | Código | SKU | product_id |
|---|---|---|---|
| Maxiconsumo | `mx` | `328` | `mx_328` |
| Santa María | `sm` | `10524` | `sm_10524` |
| Luvik | `lv` | `46583847294174` | `lv_46583847294174` |
| Vital | `vt` | `7890123` | `vt_7890123` |
| Nini | `nn` | `14230` | `nn_14230` |

El `product_id` es único en toda la base de datos (tiene índice UNIQUE) y es estable en el tiempo — no cambia si se agrega un nuevo proveedor.

---

## 6. Matching entre proveedores (`canonical_key`)

### El problema

Queremos saber cuándo dos productos de distintos proveedores son el mismo artículo, para poder comparar precios.

### La solución: `canonical_key`

Se construye una clave de matching normalizada con el formato:

```
MARCA|TIPO|MEDIDA
```

Ejemplos:
```
COCINERO|ACEITE|V1500     → Aceite Cocinero 1.5L
ARCOR|GALLETITAS|W200     → Galletitas Arcor 200g
SERENISIMA|LECHE|V1000    → Leche La Serenísima 1L
MAROLIO|ACEITE|?          → Aceite Marolio (tamaño desconocido)
```

Reglas de normalización:
- Todo en mayúsculas, sin tildes
- Peso en gramos: `W500` (500g)
- Volumen en ml: `V1000` (1 litro)
- Tamaño desconocido: `?` (igual puede matchear, con baja confianza)

Dos productos de distintos proveedores con la misma `canonical_key` son considerados el mismo artículo.

---

## 7. Población de la base de datos

### Secuencia completa (primera vez o re-población)

```bash
# 1. Inicializar esquema (idempotente, no borra datos)
python -m scraper.main db init

# 2. Scrapear todos los proveedores
#    El pipeline de postprocesamiento corre automáticamente al final de cada scrape
python -m scraper.main scrape

# 3. (Opcional) Ver qué product_types quedaron sin categoría
python -m scraper.postprocess.pipeline --list-unmapped

# 4. (Opcional) Re-procesar todo si se actualizó la taxonomía o los extractores
python -m scraper.postprocess.pipeline --force
```

### ¿Qué pasa cuando el nombre de un producto cambia?

Si el proveedor renombra un producto, el campo `features_version` se pone en NULL automáticamente. El pipeline detecta esto y re-extrae las features en la próxima ejecución.

---

## 8. Dashboard

El dashboard se abre con:

```bash
streamlit run dashboard/app.py
```

### Tab 1: Latest Prices

Vista de todos los productos con sus últimos precios registrados. Permite filtrar por:
- Proveedor
- Departamento de categoría
- Búsqueda por `product_id` o nombre

Columnas: `product_id`, marca, tipo, categoría, tamaño, proveedor, precio unitario, precio mayorista, stock, fecha.

### Tab 2: Comparación entre proveedores

Pivot table que muestra, para cada producto, el precio de cada proveedor lado a lado. Resalta en verde el más barato. Incluye la diferencia porcentual entre el más caro y el más barato.

Solo aparecen productos presentes en al menos 2 proveedores (matched por `canonical_key`).

### Tab 3: Historial de precios

Gráfico de líneas con la evolución del precio de un producto específico a lo largo del tiempo, por proveedor. Permite buscar por `product_id`, nombre o SKU.

### Tab 4: Run Log

Registro de las últimas 20 ejecuciones del scraper. Muestra estado (verde=éxito, rojo=error, amarillo=en curso), cantidad de productos procesados y snapshots escritos. Alerta si un scrape terminó con 0 snapshots (posible fallo silencioso de login).

---

## Resumen del flujo

```
SCRAPE ──► DB (raw) ──► PIPELINE ──► DB (normalizado) ──► DASHBOARD
  │                         │
  │  sku, nombre, precio     │  brand, product_type, size
  │  category (cruda)        │  category_dept, category_sub
  │                          │  canonical_key, product_id
  └──────────────────────────┘
         automático
```
