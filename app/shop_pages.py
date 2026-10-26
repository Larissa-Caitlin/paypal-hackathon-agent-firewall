"""HTML shop pages, and the text a scraping agent would extract from them."""

from html import escape

from bs4 import BeautifulSoup

from app.catalog import get_products_by_shop, list_shops


def render_shop_page(shop_key) -> str:
    """Render a shop's product listing. Raises KeyError for an unknown shop.

    Platform-controlled fields are escaped; the seller-written description is
    inserted as raw HTML, exactly as a real marketplace would render it.
    """
    shop_name = dict(list_shops())[shop_key]
    items = "\n".join(
        f"""    <li class="product">
      <h2>{escape(p.name)}</h2>
      <p class="price">${p.price:.2f}</p>
      <p class="product-id">Product ID: {escape(p.product_id)}</p>
      <div class="description">{p.description}</div>
    </li>"""
        for p in get_products_by_shop(shop_key)
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{escape(shop_name)}</title>
</head>
<body>
  <h1>{escape(shop_name)}</h1>
  <ul>
{items}
  </ul>
</body>
</html>
"""


def page_text_for_agent(shop_key, include_hidden: bool = True) -> str:
    """Return page text as a scraping agent sees it.

    By default this includes display:none text. With include_hidden=False,
    elements styled display:none are removed first (what a shopper sees).
    """
    soup = BeautifulSoup(render_shop_page(shop_key), "html.parser")
    if not include_hidden:
        for el in soup.find_all(style=True):
            if "display:none" in el["style"].replace(" ", "").lower():
                el.decompose()
    return soup.get_text(separator="\n", strip=True)
