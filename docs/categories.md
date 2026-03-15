# Unified Category Taxonomy

Reference guide for the unified category hierarchy used across all suppliers.

**Structure:** Two-level hierarchy (Department → Subcategory) mapped from normalized `product_type` values.

---

## Almacén (General Grocery)

| Subcategory | Product Types |
|---|---|
| **Aceites y Grasas** | Aceite, Grasa |
| **Arroz y Legumbres** | Arroz, Garbanzos, Lentejas, Porotos |
| **Conservas** | Aceitunas, Arvejas, Choclo, Palmitos, Tomate |
| **Condimentos** | Aji, Aji Molido, Aderezo, Ajo, Albahaca, Azafran, Caldo, Canela, Cebolla, Comino, Condimento, Condimento para Arroz, Condimento para Pizza, Especias, Mezcla de Especias, Mix de Sabor, Mostaza, Nuez Moscada, Oregano, Perejil, Pimienta, Pimenton, Provenzal, Romero, Saborizador, Sal, Salero, Tomillo, Vegetales Deshidratados, Vinagre |
| **Galletitas** | Galletitas |
| **Pastas** | Fideos |
| **Harinas y Panificados** | Bicarbonato, Harina, Levadura, Pan, Tapas para empanadas |
| **Salsas y Aderezos** | Aceto, Chimichurri, Ketchup, Mayonesa, Salsa, Salsa Blanca, Salsa Golf |
| **Sopas** | Sopa |
| **Desayuno** | Avena, Cereal, Chocolate, Copo, Copos, Dulce, Granola, Mermelada, Miel, Semilla, Tostadas |
| **Yerba y Té** | Té, Yerba |
| **Café** | Café |
| **Azúcar** | Azúcar |

---

## Bebidas (Beverages)

| Subcategory | Product Types |
|---|---|
| **Gaseosas** | Agua, Gaseosa, Jugo, Soda |
| **Cerveza** | Cerveza |
| **Vino** | Espumante, Vino |
| **Bebidas Alcohólicas** | Aperitivo, Fernet, Gin, Licor, Ron, Whisky, Vodka |
| **Bebidas Energizantes** | Energizante, Isotonica |

---

## Lácteos (Dairy)

| Subcategory | Product Types |
|---|---|
| **Leche** | Leche |
| **Yoghurt** | Yoghurt |
| **Queso** | Queso |
| **Manteca y Crema** | Crema, Manteca |

---

## Fiambres y Embutidos (Deli & Cold Meats)

| Subcategory | Product Types |
|---|---|
| **Embutidos** | Chorizo, Salchicha |
| **Fiambre** | Jamón, Mortadela |

---

## Limpieza (Cleaning)

| Subcategory | Product Types |
|---|---|
| **Detergentes** | Detergente, Jabón |
| **Limpiadores** | Desengrasante, Lavandina, Limpiador |
| **Papel** | Papel Higiénico, Rollo, Servilleta |

---

## Higiene y Cuidado Personal (Personal Care & Hygiene)

| Subcategory | Product Types |
|---|---|
| **Shampoo y Acondicionador** | Acondicionador, Shampoo |
| **Desodorante** | Desodorante |
| **Jabón y Cremas** | Crema Facial, Jabón Tocador |
| **Dental** | Cepillo Dental, Pasta Dental |
| **Pañales** | Pañal |

---

## Mascotas (Pet Products)

| Subcategory | Product Types |
|---|---|
| **Alimento Gatos** | Alimento Gato |
| **Alimento Perros** | Alimento Perro |
| **Accesorios** | Accesorio Mascota |

---

## Otros (Unmapped)

| Subcategory | Product Types |
|---|---|
| **Otros** | All unmapped product_types default here |

---

## Notes

- All product types are normalized to uppercase for matching (`product_type.upper()`)
- Accents are stripped during matching (e.g., "Pimienta" matches "PIMIENTA")
- If a product's `product_type` is not in this list, it defaults to `(Otros, Otros)`
- To add a new mapping, edit `scraper/postprocess/data/unified_categories.txt`

---

## Example Mappings

| Raw Product Type | Normalized | Department | Subcategory |
|---|---|---|---|
| "Mermelada Frutilla" | MERMELADA | Almacén | Desayuno |
| "Leche La Serenísima" | LECHE | Lácteos | Leche |
| "Cerveza Quilmes" | CERVEZA | Bebidas | Cerveza |
| "Detergente Ola" | DETERGENTE | Limpieza | Detergentes |
| "Alimento para Perros" | ALIMENTO PERRO | Mascotas | Alimento Perros |
| "Random Item XYZ" | RANDOM ITEM XYZ | Otros | Otros |

---

## How to Extend

1. Identify the unmapped `product_type` (use `python -m scraper.postprocess.pipeline --list-unmapped`)
2. Determine the appropriate Department and Subcategory
3. Add line to `scraper/postprocess/data/unified_categories.txt`:
   ```
   Department|Subcategory|ProductType
   ```
4. No code changes needed — pipeline reloads the file automatically
