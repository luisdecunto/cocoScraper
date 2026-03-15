"""Generate fixed-width product tables to exports/<supplier>_products.txt.

Usage:
    python exports/make_table.py              # generates all suppliers
    python exports/make_table.py luvik        # generates luvik only
    python exports/make_table.py nini         # generates nini only
"""
from dotenv import load_dotenv
load_dotenv()

import asyncio
import asyncpg
import os
import sys
import importlib


def _make_size_str(size_value, size_unit):
    if size_value is None:
        return ""
    # Auto-upgrade units for readability
    if size_unit == "ml" and size_value >= 1000:
        size_value = size_value / 1000
        size_unit = "l"
    elif size_unit == "g" and size_value >= 1000:
        size_value = size_value / 1000
        size_unit = "kg"
    n = int(size_value) if size_value == int(size_value) else round(size_value, 3)
    return str(n) + " " + (size_unit or "")


def _write_table(data, cols, out_path):
    widths = [max(len(cols[i]), max((len(row[i]) for row in data), default=0)) for i in range(len(cols))]
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    def fmt(row):
        return "|" + "|".join(" " + row[i].ljust(widths[i]) + " " for i in range(len(cols))) + "|"

    with open(out_path, "w", encoding="utf-8") as out:
        out.write(sep + "\n")
        out.write(fmt(cols) + "\n")
        out.write(sep + "\n")
        for row in data:
            out.write(fmt(row) + "\n")
        out.write(sep + "\n")

    print(f"Written {len(data)} rows -> {out_path}")
    print(f"Column widths: {dict(zip(cols, widths))}")


# Name-specific size corrections for truncated/corrupt source data.
_LUVIK_SIZE_OVERRIDES: dict[str, str] = {
    "PROTECTOR LABIAL NIVEA ESSENCIAL CARE 4.": "4.8 g",
}


async def make_luvik(pool):
    import scraper.postprocess.luvik as _mod
    importlib.reload(_mod)
    from scraper.postprocess.luvik import extract_features

    rows = await pool.fetch(
        "SELECT name, category FROM products WHERE supplier=$1 ORDER BY name", "luvik"
    )
    data = []
    for r in rows:
        f = extract_features(r["name"])
        size = (
            _LUVIK_SIZE_OVERRIDES.get(r["name"])
            or f.get("size_display")
            or _make_size_str(f.get("size_value"), f.get("size_unit"))
        )
        data.append([
            r["name"] or "",
            f.get("brand") or "",
            f.get("product_type") or "",
            f.get("variant") or "",
            size,
            f.get("category") or r["category"] or "",
        ])
    _write_table(data, ["Nombre", "Marca", "Tipo", "Variante", "Tamaño", "Categoría"],
                 "exports/luvik_products.txt")


async def make_nini(pool):
    import scraper.postprocess.nini as _mod
    importlib.reload(_mod)
    from scraper.postprocess.nini import extract_features

    rows = await pool.fetch(
        "SELECT name, category FROM products WHERE supplier=$1 ORDER BY name", "nini"
    )
    data = []
    for r in rows:
        f = extract_features(r["name"], r["category"])
        data.append([
            r["name"] or "",
            f.get("brand") or "",
            f.get("product_type") or "",
            f.get("variant") or "",
            _make_size_str(f.get("size_value"), f.get("size_unit")),
            f.get("category") or r["category"] or "",
        ])
    _write_table(data, ["Nombre", "Marca", "Tipo", "Variante", "Tamaño", "Categoría"],
                 "exports/nini_products.txt")


async def make_vital(pool):
    import scraper.postprocess.vital as _mod
    importlib.reload(_mod)
    from scraper.postprocess.vital import extract_features, parse_category

    rows = await pool.fetch(
        "SELECT name, category FROM products WHERE supplier=$1 ORDER BY name", "vital"
    )
    data = []
    for r in rows:
        f = extract_features(r["name"])
        c = parse_category(r["category"])
        n = f.get("units_in_name")
        if f.get("weight"):
            per = _make_size_str(f["weight"]["value"], f["weight"]["unit"])
            size = f"{n}x{per}" if n else per
        elif f.get("volume"):
            per = _make_size_str(f["volume"]["value"], f["volume"]["unit"])
            size = f"{n}x{per}" if n else per
        elif n:
            lbl = f.get('units_label') or 'un'
            if f.get("length"):
                size = f"{n} {lbl} x {f['length']}"
            else:
                size = f"{n} {lbl}"
        elif f.get("inches"):
            size = f["inches"]
        elif f.get("dimensions"):
            dim = f["dimensions"]
            if f.get("length"):
                size = f"{dim} x {f['length']}"
            elif f.get("page_count"):
                size = f"{dim}, {f['page_count']} h"
            else:
                size = dim
        elif f.get("page_count"):
            size = f"{f['page_count']} h"
        elif f.get("length"):
            size = f["length"]
        else:
            size = ""
        data.append([
            r["name"] or "",
            f.get("brand") or "",
            f.get("product_type") or "",
            f.get("variant") or "",
            size,
            c.get("section") or r["category"] or "",
        ])
    _write_table(data, ["Nombre", "Marca", "Tipo", "Variante", "Tamaño", "Categoría"],
                 "exports/vital_products.txt")


async def make_maxiconsumo(pool):
    import scraper.postprocess.maxiconsumo as _mod
    importlib.reload(_mod)
    from scraper.postprocess.maxiconsumo import extract_features

    rows = await pool.fetch(
        "SELECT name, category FROM products WHERE supplier=$1 ORDER BY name", "maxiconsumo"
    )
    data = []
    for r in rows:
        f = extract_features(r["name"])
        if f.get("weight"):
            size = _make_size_str(f["weight"]["value"], f["weight"]["unit"])
        elif f.get("volume"):
            size = _make_size_str(f["volume"]["value"], f["volume"]["unit"])
        elif f.get("units_in_name"):
            size = f"{f['units_in_name']} {f.get('units_label') or 'un'}"
        elif f.get("dimensions"):
            size = f["dimensions"]
        elif f.get("length"):
            size = f["length"]
        else:
            size = ""
        data.append([
            r["name"] or "",
            f.get("brand") or "",
            f.get("product_type") or "",
            f.get("variant") or "",
            size,
            r["category"] or "",
        ])
    _write_table(data, ["Nombre", "Marca", "Tipo", "Variante", "Tamaño", "Categoría"],
                 "exports/maxiconsumo_products.txt")


async def make_santamaria(pool):
    import scraper.postprocess.santamaria as _mod
    importlib.reload(_mod)
    from scraper.postprocess.santamaria import extract_features

    rows = await pool.fetch(
        "SELECT name, category FROM products WHERE supplier=$1 ORDER BY name", "santamaria"
    )
    data = []
    for r in rows:
        f = extract_features(r["name"])
        if f.get("weight_g") is not None:
            weight_str = _make_size_str(f["weight_g"], "g")
            n = f.get("pack_count")
            size = f"{n}x{weight_str}" if n else weight_str
        elif f.get("volume_ml") is not None:
            size = _make_size_str(f["volume_ml"], "ml")
        elif f.get("bag_dimensions"):
            dim = f["bag_dimensions"]
            count = f.get("bag_count") or f.get("units_in_name")
            size = f"{count} U x {dim}" if count else dim
        elif f.get("units_in_name"):
            lbl = f.get("units_label") or "U"
            n = f.get("pack_count")
            if n:
                size = f"{n}x{f['units_in_name']} {lbl}"
            elif f.get("length"):
                size = f"{f['units_in_name']} {lbl} x {f['length']}"
            else:
                size = f"{f['units_in_name']} {lbl}"
        elif f.get("length"):
            size = f["length"]
        else:
            size = ""
        data.append([
            r["name"] or "",
            f.get("brand") or "",
            f.get("product_type") or "",
            f.get("variant") or "",
            size,
            r["category"] or "",
        ])
    _write_table(data, ["Nombre", "Marca", "Tipo", "Variante", "Tamaño", "Categoría"],
                 "exports/santamaria_products.txt")


async def main():
    pool = await asyncpg.create_pool(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", 5432)),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASS"),
    )

    target = sys.argv[1] if len(sys.argv) > 1 else "all"

    if target in ("all", "luvik"):
        await make_luvik(pool)
    if target in ("all", "nini"):
        await make_nini(pool)
    if target in ("all", "vital"):
        await make_vital(pool)
    if target in ("all", "maxiconsumo"):
        await make_maxiconsumo(pool)
    if target in ("all", "santamaria"):
        await make_santamaria(pool)

    await pool.close()


asyncio.run(main())
