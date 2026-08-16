#!/usr/bin/env python3
"""Scaffold a standard CRUD catalogue module from a small JSON spec.

Generates models.py, schemas.py, router.py, __init__.py under
apps/api/app/modules/<module>/ following the project's conventions
(UUIDv7 PK + timestamps via mixins, RBAC-guarded CRUD via CRUDService).

Usage:
    python scripts/scaffold_module.py path/to/spec.json [--force]

It only writes the boilerplate. Custom services (rate selection, effective-dated
sub-tables, pricing math) are added by hand afterwards. The script prints the
manual wiring steps (register model, include router, add permissions).

Spec example:
{
  "module": "vehicles",
  "class": "Vehicle",
  "table": "vehicles",
  "route_prefix": "/vehicles",
  "permission": "vehicle",
  "tag": "fleet",
  "slug_from": "name",
  "fields": [
    {"name": "name", "type": "str", "max": 200, "index": true},
    {"name": "passenger_capacity", "type": "int", "default": 6},
    {"name": "cost_per_km", "type": "decimal", "optional": true},
    {"name": "supplier_id", "type": "fk", "target": "suppliers", "ondelete": "SET NULL", "optional": true},
    {"name": "is_active", "type": "bool", "default": true}
  ]
}
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULES = ROOT / "apps" / "api" / "app" / "modules"

# type -> (SQLAlchemy column type name, python type)
SA_TYPE = {
    "str": "String",
    "text": "Text",
    "int": "Integer",
    "bool": "Boolean",
    "decimal": "Numeric",
    "date": "Date",
}
PY_TYPE = {
    "str": "str",
    "text": "str",
    "int": "int",
    "bool": "bool",
    "decimal": "Decimal",
    "date": "date",
    "fk": "uuid.UUID",
}


def py_literal(value) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, str):
        return f'"{value}"'
    return str(value)


def col_type_expr(f: dict) -> str:
    t = f["type"]
    if t == "fk":
        return "PGUUID(as_uuid=True)"
    if t == "str":
        return f"String({f.get('max', 255)})"
    if t == "decimal":
        p, s = f.get("precision", 18), f.get("scale", 4)
        return f"Numeric({p}, {s})"
    return SA_TYPE[t] + "()"


def model_field(f: dict) -> str:
    name = f["name"]
    optional = f.get("optional", False)
    py = PY_TYPE[f["type"]]
    ann = f"{py} | None" if optional else py
    args = [col_type_expr(f)]
    if f["type"] == "fk":
        target = f["target"]
        ondelete = f.get("ondelete", "RESTRICT")
        args.append(f'ForeignKey("{target}.id", ondelete="{ondelete}")')
    if f.get("index"):
        args.append("index=True")
    if f.get("unique"):
        args.append("unique=True")
    if "default" in f:
        args.append(f"default={py_literal(f['default'])}")
    args.append(f"nullable={optional}")
    return f"    {name}: Mapped[{ann}] = mapped_column(\n        {', '.join(args)}\n    )"


def model_imports(spec: dict) -> str:
    fields = spec["fields"]
    types = {f["type"] for f in fields}
    has_slug = bool(spec.get("slug_from"))
    sa: set[str] = set()
    if any(f["type"] == "fk" for f in fields):
        sa.add("ForeignKey")
    for t in types:
        if t in SA_TYPE and t != "fk":
            sa.add(SA_TYPE[t])
    if has_slug:
        sa.add("String")
    lines = ["from __future__ import annotations", "", "import uuid"]
    if "date" in types:
        lines.append("from datetime import date")
    if "decimal" in types:
        lines.append("from decimal import Decimal")
    lines.append("")
    if sa:
        lines.append(f"from sqlalchemy import {', '.join(sorted(sa))}")
    if any(f["type"] == "fk" for f in fields):
        lines.append("from sqlalchemy.dialects.postgresql import UUID as PGUUID")
    lines.append("from sqlalchemy.orm import Mapped, mapped_column")
    lines.append("")
    lines.append("from app.db.base_class import Base, TimestampMixin, UUIDPKMixin")
    return "\n".join(lines)


def gen_models(spec: dict) -> str:
    cls, table = spec["class"], spec["table"]
    body = [model_imports(spec), "", "", f"class {cls}(UUIDPKMixin, TimestampMixin, Base):",
            f'    __tablename__ = "{table}"', ""]
    if spec.get("slug_from"):
        body.append('    slug: Mapped[str] = mapped_column(String(280), unique=True, '
                     "index=True, nullable=False)")
    for f in spec["fields"]:
        body.append(model_field(f))
    return "\n".join(body) + "\n"


def schema_fields(spec: dict, *, for_update: bool) -> list[str]:
    out = []
    for f in spec["fields"]:
        py = PY_TYPE[f["type"]]
        if for_update:
            out.append(f"    {f['name']}: {py} | None = None")
        else:
            optional = f.get("optional", False)
            if "default" in f:
                out.append(f"    {f['name']}: {py} = {py_literal(f['default'])}")
            elif optional:
                out.append(f"    {f['name']}: {py} | None = None")
            else:
                out.append(f"    {f['name']}: {py}")
    return out


def gen_schemas(spec: dict) -> str:
    cls = spec["class"]
    types = {f["type"] for f in spec["fields"]}
    header = ["from __future__ import annotations", "", "import uuid",
              "from datetime import datetime"]
    if "date" in types:
        header.insert(3, "from datetime import date")
    if "decimal" in types:
        header.append("from decimal import Decimal")
    header += ["", "from pydantic import BaseModel, ConfigDict", "", ""]

    base = [f"class {cls}Base(BaseModel):"] + schema_fields(spec, for_update=False)
    create = [f"class {cls}Create({cls}Base):"]
    create.append("    slug: str | None = None" if spec.get("slug_from") else "    pass")
    update = [f"class {cls}Update(BaseModel):"] + schema_fields(spec, for_update=True)
    read = [f"class {cls}Read({cls}Base):",
            "    model_config = ConfigDict(from_attributes=True)",
            "    id: uuid.UUID"]
    if spec.get("slug_from"):
        read.append("    slug: str")
    read.append("    created_at: datetime")

    return "\n".join(header) + "\n\n\n".join(
        ["\n".join(base), "\n".join(create), "\n".join(update), "\n".join(read)]
    ) + "\n"


def gen_router(spec: dict) -> str:
    cls, pref, perm, tag = spec["class"], spec["route_prefix"], spec["permission"], spec.get("tag", "catalogue")
    slug = spec.get("slug_from")
    idvar = f"{spec['module']}_id"
    slug_import = ", slugify" if slug else ""
    create_body = (
        f'    data = body.model_dump()\n    data["slug"] = slugify(data.get("slug") or data["{slug}"])\n'
        f"    return await CRUDService(db, {cls}).create(data)"
        if slug else
        f"    return await CRUDService(db, {cls}).create(body.model_dump())"
    )
    return f'''from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crud import CRUDService{slug_import}
from app.core.deps import require_permission
from app.db.session import get_db
from app.modules.{spec["module"]}.models import {cls}
from app.modules.{spec["module"]}.schemas import {cls}Create, {cls}Read, {cls}Update

router = APIRouter(prefix="{pref}", tags=["{tag}"])

READ = "{perm}:read"
MANAGE = "{perm}:manage"


@router.get("", response_model=list[{cls}Read])
async def list_items(db: AsyncSession = Depends(get_db), _=Depends(require_permission(READ))):
    return await CRUDService(db, {cls}).list()


@router.post("", response_model={cls}Read, status_code=201)
async def create_item(
    body: {cls}Create,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(MANAGE)),
):
{create_body}


@router.get("/{{{idvar}}}", response_model={cls}Read)
async def get_item(
    {idvar}: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(READ)),
):
    return await CRUDService(db, {cls}).get({idvar})


@router.patch("/{{{idvar}}}", response_model={cls}Read)
async def update_item(
    {idvar}: uuid.UUID,
    body: {cls}Update,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_permission(MANAGE)),
):
    return await CRUDService(db, {cls}).update({idvar}, body.model_dump(exclude_unset=True))
'''


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    spec = json.loads(Path(sys.argv[1]).read_text())
    force = "--force" in sys.argv
    mod_dir = MODULES / spec["module"]
    mod_dir.mkdir(parents=True, exist_ok=True)

    files = {
        "__init__.py": "",
        "models.py": gen_models(spec),
        "schemas.py": gen_schemas(spec),
        "router.py": gen_router(spec),
    }
    for name, content in files.items():
        path = mod_dir / name
        if path.exists() and not force and name != "__init__.py":
            print(f"SKIP (exists): {path}  (use --force to overwrite)")
            continue
        path.write_text(content)
        print(f"wrote {path.relative_to(ROOT)}")

    cls, perm = spec["class"], spec["permission"]
    print("\n--- Manual wiring still required ---")
    print(f"1. app/db/base.py: import {cls}(+related) and add to __all__")
    print(f"2. app/api/v1/router.py: include the {spec['module']} router")
    print(f"3. app/modules/rbac/permissions.py: add '{perm}:read'/'{perm}:manage' "
          f"(+ '{perm}:read' to _REFERENCE_READ)")
    print("4. make makemigration name=... && migrate; add tests")


if __name__ == "__main__":
    main()
