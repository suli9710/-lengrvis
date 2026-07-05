"""Client-side script for the subscription activation Admin workspace."""

from app.api.activation_admin_template_script_actions import ADMIN_SCRIPT_ACTIONS
from app.api.activation_admin_template_script_core import ADMIN_SCRIPT_CORE
from app.api.activation_admin_template_script_detail import ADMIN_SCRIPT_DETAIL
from app.api.activation_admin_template_script_list import ADMIN_SCRIPT_LIST

ADMIN_SCRIPT = "".join(
    (
        ADMIN_SCRIPT_CORE,
        ADMIN_SCRIPT_LIST,
        ADMIN_SCRIPT_DETAIL,
        ADMIN_SCRIPT_ACTIONS,
    )
)
