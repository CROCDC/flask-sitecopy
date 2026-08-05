"""Edit every string on a Flask site from an admin panel, without a deploy.

    from sitecopy import SiteCopy, Registry, Group, Section, TextField

    REGISTRY = Registry(
        groups=(
            Group("home", "Inicio", "La página principal", sections=(
                Section("hero", "Portada", fields=(
                    TextField("home.hero.title", "Título", "Bolsos de cuero vegano"),
                )),
            )),
        ),
    )

    sitecopy = SiteCopy(app, registry=REGISTRY, db=db)

Then in a template::

    <h1>{{ t('home.hero.title') }}</h1>

and the string is editable at /admin/content — in place, on the real page.

The database stores overrides only, so a fresh install renders exactly what the code
says, "restore the original" is a row delete, and adding copy never needs a migration.

See the README for the hooks (`login_required`, `pages`, `base_template`, …).
"""

from sitecopy.extension import SiteCopy
from sitecopy.registry import (
    DEFAULT_MAX_LENGTH,
    FieldType,
    Group,
    Registry,
    Section,
    TextField,
)
from sitecopy.resolver import (
    editable,
    editable_lines,
    editable_optional,
    field_state,
    group_states,
    has_stray_brace,
    invalidate,
    is_edit_mode,
    is_preview,
    override_count,
    pending_draft_count,
    rollback,
    save,
    t,
    t_lines,
    t_optional,
    t_plain,
    token,
    tokens,
    unknown_tokens,
)
from sitecopy.sanitizer import sanitize, strip_tags, visible_text
from sitecopy.state import current_registry, current_state, current_store
from sitecopy.storage import MemoryStore, SQLAlchemyStore, TextRow, TextStore

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_MAX_LENGTH",
    "FieldType",
    "Group",
    "MemoryStore",
    "Registry",
    "SQLAlchemyStore",
    "Section",
    "SiteCopy",
    "TextField",
    "TextRow",
    "TextStore",
    "__version__",
    "current_registry",
    "current_state",
    "current_store",
    "editable",
    "editable_lines",
    "editable_optional",
    "field_state",
    "group_states",
    "has_stray_brace",
    "invalidate",
    "is_edit_mode",
    "is_preview",
    "override_count",
    "pending_draft_count",
    "rollback",
    "sanitize",
    "save",
    "strip_tags",
    "t",
    "t_lines",
    "t_optional",
    "t_plain",
    "token",
    "tokens",
    "unknown_tokens",
    "visible_text",
]
