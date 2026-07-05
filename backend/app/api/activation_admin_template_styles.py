"""CSS for the subscription activation Admin workspace template."""

from app.api.activation_admin_template_style_base import ADMIN_STYLE_BASE
from app.api.activation_admin_template_style_detail import ADMIN_STYLE_DETAIL_AND_RESPONSIVE
from app.api.activation_admin_template_style_lists import ADMIN_STYLE_CREATION_AND_LISTS

ADMIN_STYLES = "".join(
    (
        ADMIN_STYLE_BASE,
        ADMIN_STYLE_CREATION_AND_LISTS,
        ADMIN_STYLE_DETAIL_AND_RESPONSIVE,
    )
)
