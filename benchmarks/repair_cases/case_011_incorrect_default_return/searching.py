def find_index(values: list[str], target: str) -> int:
    for index, value in enumerate(values):
        if value == target:
            return index
    return 0
