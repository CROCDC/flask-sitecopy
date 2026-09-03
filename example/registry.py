"""The catalogue of editable strings for the demo site.

This is the whole "content model": every string the demo renders through ``t('…')``
is declared here once, with a human label and a default. A fresh database therefore
renders exactly these defaults — there is no seeding step. Add a field here plus a
``t('<key>')`` in a template and it is instantly editable in the panel.

The registry is grouped the way the editor lists it: Group → Section → TextField. Each
group carries a ``preview_path`` so the panel can open the right page in the canvas.
"""

from __future__ import annotations

from sitecopy import Collection, Group, Item, ItemField, Registry, Section, TextField

# --- Inicio --------------------------------------------------------------------------

HOME = Group(
    key="home",
    title="Inicio",
    description="La portada del sitio.",
    preview_path="/",
    sections=(
        Section(
            key="hero",
            title="Portada",
            note="Lo primero que se ve al entrar.",
            fields=(
                TextField("home.hero.title", "Título", "Bolsos de cuero vegano", max_length=80),
                TextField(
                    "home.hero.subtitle",
                    "Bajada",
                    "Hechos a mano en {brand}, sin un solo animal de por medio.",
                    type="text",
                ),
                TextField("home.hero.cta", "Botón", "Ver la colección"),
                TextField(
                    "home.hero.image",
                    "Foto de portada",
                    "/static/hero.svg",
                    type="image",
                    hint="Pegá el link de una imagen (https://…) o una ruta del sitio como /static/foto.jpg.",
                ),
                TextField("home.hero.alt", "Texto de la foto de portada", "Un bolso sobre una mesa de madera"),
            ),
        ),
        Section(
            key="values",
            title="Los tres valores",
            note="La tira de tres columnas debajo de la portada.",
            fields=(
                TextField("home.values.heading", "Título de la sección", "Por qué {brand}"),
                TextField(
                    "home.values.items",
                    "Los tres, uno por línea",
                    "Cuero de cactus, cero crueldad\nCosido a mano, garantía de por vida\nEnvío gratis en todo el país",
                    type="lines",
                ),
            ),
        ),
        Section(
            key="galeria",
            title="Galería",
            note=(
                "Las fotos de la galería. Se pueden agregar, borrar y reordenar — a "
                "diferencia del resto, acá la LISTA también se edita, no sólo los textos."
            ),
            fields=(
                TextField("home.galeria.heading", "Título de la sección", "La galería"),
            ),
            collections=(
                Collection(
                    key="home.galeria",
                    title="Fotos",
                    item_label="Foto",
                    item_fields=(
                        ItemField(
                            "img",
                            "Imagen",
                            type="image",
                            default="/static/galeria-1.svg",
                            hint="Subí un archivo o pegá un link (https://…) o una ruta como /static/foto.jpg.",
                        ),
                        ItemField("cap", "Epígrafe", type="text", default="Una foto de la colección"),
                    ),
                    default_items=(
                        Item("frente", img="/static/galeria-1.svg", cap="La mochila de cactus, de frente"),
                        Item("silla", img="/static/galeria-2.svg", cap="Un bolso cruzado sobre una silla"),
                        Item("cosido", img="/static/galeria-3.svg", cap="El detalle del cosido a mano"),
                    ),
                    min_items=1,
                    max_items=8,
                ),
            ),
        ),
        Section(
            key="meta",
            title="Buscadores y redes",
            note="Lo que ve Google y lo que aparece al compartir el link. No se ve en la página.",
            fields=(
                TextField("home.meta.title", "Título en Google", "{brand} · Bolsos de cuero vegano"),
                TextField(
                    "home.meta.description",
                    "Descripción en Google",
                    "Bolsos de cuero vegano hechos a mano. Envío gratis en todo el país.",
                    type="text",
                ),
            ),
        ),
    ),
)

# --- Nosotros ------------------------------------------------------------------------

ABOUT = Group(
    key="about",
    title="Nosotros",
    description="La página institucional.",
    preview_path="/nosotros",
    category="Páginas",
    sections=(
        Section(
            key="body",
            title="Cuerpo",
            fields=(
                TextField("about.title", "Título", "La historia de {brand}"),
                TextField(
                    "about.body",
                    "Texto de la página",
                    "<p>Empezamos en un taller de barrio buscando un cuero que no "
                    "viniera de ningún animal.</p>"
                    "<h2>El material</h2>"
                    "<p>Hoy trabajamos con <strong>cuero de cactus</strong>: resistente, "
                    "suave y completamente vegetal.</p>"
                    "<h2>El taller</h2>"
                    "<p>Cada bolso se cose a mano, de a uno.</p>",
                    type="rich",
                ),
            ),
        ),
    ),
)

# --- Global (marca, redes, pie) ------------------------------------------------------

GLOBAL = Group(
    key="global",
    title="Marca y redes",
    description="Textos que se reutilizan en todo el sitio.",
    preview_path="/",
    category="Global",
    sections=(
        Section(
            key="brand",
            title="Marca",
            note="El nombre se puede escribir en cualquier texto como {brand}.",
            fields=(
                TextField("global.brand", "Nombre de la marca", "Verdana", max_length=40),
                TextField("global.tagline", "Bajada de la marca", "Cuero que no le costó a nadie."),
            ),
        ),
        Section(
            key="links",
            title="Enlaces",
            fields=(
                TextField(
                    "global.instagram",
                    "Instagram",
                    "https://instagram.com/verdana",
                    type="url",
                ),
                TextField("global.instagram_label", "Texto del enlace a Instagram", "Seguinos en Instagram"),
            ),
        ),
        Section(
            key="footer",
            title="Pie de página",
            fields=(
                TextField("global.footer.note", "Nota del pie", "© {year} {brand}. {tagline}"),
                # Copy that lives ONLY in an attribute: there is nowhere on the page to
                # click it, so the editor opens it in the side panel. A picture is the
                # other case — it has controls of its own — and the two paths part ways
                # on exactly this distinction.
                TextField("global.nav.label", "Nombre del menú (lectores de pantalla)", "Menú principal"),
            ),
        ),
    ),
)

# --- Producto (metadatos por producto) ----------------------------------------------

PRODUCT = Group(
    key="product",
    title="Ficha de producto",
    description="Los textos fijos de la ficha. El nombre y el precio salen del catálogo.",
    preview_path="/producto/mochila-cactus",
    category="Páginas",
    sections=(
        Section(
            key="labels",
            title="Etiquetas",
            fields=(
                TextField("product.cta", "Botón de compra", "Agregar al carrito"),
                TextField("product.shipping", "Aviso de envío", "Envío gratis · llega en 3 a 5 días"),
            ),
        ),
        Section(
            key="meta",
            title="Buscadores",
            note="{name} se reemplaza por el nombre del producto.",
            fields=(
                TextField("product.meta.title", "Título en Google", "{name} · {brand}"),
            ),
        ),
    ),
)


def build_registry() -> Registry:
    return Registry(
        groups=(HOME, ABOUT, PRODUCT, GLOBAL),
        # Order matters: {tagline} and {footer} may mention {brand}, so brand resolves
        # first. {year} is always available on top of these.
        tokens=("global.brand", "global.tagline"),
        # Per-call tokens the template passes in. Declared so the admin's validation
        # knows the product title is not a typo.
        field_tokens={"product.meta.title": ("name",)},
    )
