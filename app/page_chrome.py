"""Site header shared by the server-rendered pages (the static pages carry the same markup)."""

NAV = [("demo", "/", "Demo"), ("lab", "/lab", "Attack Lab"), ("security", "/security", "Security")]


def site_header(current) -> str:
    """Brand plus navigation; the current page is marked with aria-current."""
    links = "".join(
        f'<a href="{href}"{" aria-current=\"page\"" if key == current else ""}>{label}</a>'
        for key, href, label in NAV
    )
    return (
        '<header class="site-header">'
        '<a class="brand" href="/"><span class="brand-mark" aria-hidden="true"></span>'
        '<span class="brand-name">Agent Payment Firewall</span></a>'
        f'<nav class="site-nav" aria-label="Main">{links}</nav>'
        "</header>"
    )
