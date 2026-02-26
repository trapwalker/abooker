import contextlib
from pathlib import Path

import yaml

from abooker.files import mask_case_fix


BOOK_SETTINGS_FILENAME = '.abook'
LOCAL_SETTINGS_FILENAME = '.abooker'
LOCAL_SETTINGS_FILEMASK = '.abook*'


def load_settings(
    path: Path,
    filename: str = None,
    mask: str = LOCAL_SETTINGS_FILEMASK,
    errors='strict',
) -> dict:
    if filename is None:
        filenames = path.glob(mask_case_fix(mask))
    elif isinstance(filename, str):
        filenames = [filename]
    else:
        filenames = filename

    with contextlib.suppress(*(errors == 'ignore' and [Exception] or [])):
        for fn in filenames:
            with path.joinpath(fn).open() as f:
                data = yaml.safe_load(f)
                return data


def save_settings(settings: dict, path: Path, errors='strict'):
    with contextlib.suppress(*(errors == 'ignore' and [Exception] or [])):
        with path.open('w') as f:
            yaml.dump(settings, f, allow_unicode=True, encoding='utf-8', indent=2, default_flow_style=False)
