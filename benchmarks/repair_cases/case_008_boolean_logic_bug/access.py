def can_edit(is_owner: bool, is_admin: bool) -> bool:
    return is_owner and is_admin
