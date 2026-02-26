import re
import typing
from pathlib import Path


media_types = {
    'aac': 'audio/aac',
    'mp3': 'audio/mpeg',
    'ogg': 'audio/ogg',
    'wma': 'audio/x-ms-wma',
    'wav': 'audio/vnd.wave',
    'mp4': 'audio/mp4',
}

image_types = {
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'png': 'image/png',
}

media_file_masks = [f'*.{t}' for t in media_types]
image_file_masks = [f'*.{t}' for t in image_types]

info_file_masks = [
    'readme.*',
    'readme',
    'info.*',
    'info',
    'about.*',
    'about',
    '*.txt',
    '*.md',
    '*.info',
    '*.rst',
]


def mask_case_fix(mask: str) -> str:
    return ''.join(
        f'[{c.lower()}{c.upper()}]'
        if c.isalpha() else
        c
        for c in mask
    )


def iter_files(
    path: Path,
    mask_list: typing.Iterable[str],
    recursive: bool = True,
    case_sens=False,
) -> typing.Iterable[Path]:
    glob_method = path.rglob if recursive else path.glob
    case_fix = (lambda s: s) if case_sens else mask_case_fix
    return (f for m in mask_list for f in glob_method(case_fix(m)))


def filename_key(fn: Path, root: Path = None) -> typing.Hashable:
    def split(s: str, re_splitter=re.compile(r'(\D+|\d+)')) -> tuple:
        return tuple(int(chunk) if chunk.isdigit() else chunk for chunk in re_splitter.split(s) if chunk)

    rel_path = fn.relative_to(root) if root else fn
    return tuple(split(name) for name in rel_path.parent.parts) + (split(rel_path.stem) + (rel_path.suffix,),)
