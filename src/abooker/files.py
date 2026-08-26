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


# A number immediately next to a bitrate/samplerate unit (e.g. "128kbps",
# "44.1kHz") is encoding metadata, not part of the narrative numbering —
# skip it, or "128kbps Глава 3.mp3" would sort by 128, not 3.
_NOISE_NUMBER_RE = re.compile(r'\d+(?=\s?(?:kbps|khz|hz|kbit|bit|bps))', re.IGNORECASE)


def _numbers_in_path(path: Path) -> list[int]:
    """All (non-noise) numbers found in a path, read off *every* component
    (each directory name, then the filename's stem) left to right. An
    audiobook's true chapter/part order can come from directory structure as
    much as from the filename itself — "Книга 1/Глава 1/Отправление (часть
    1).mp3" carries its (book, chapter, part) = (1, 1, 1) across all three.
    The extension is excluded so e.g. ".mp3" doesn't contribute a bogus
    trailing "3"."""
    parts = list(path.parts)
    if parts:
        parts[-1] = Path(parts[-1]).stem
    numbers = []
    for part in parts:
        noise_spans = {m.start() for m in _NOISE_NUMBER_RE.finditer(part)}
        for m in re.finditer(r'\d+', part):
            if m.start() not in noise_spans:
                numbers.append(int(m.group(0)))
    return numbers


def _dedupe_name(name: str, used: typing.AbstractSet[str]) -> str:
    if name not in used:
        return name
    p = Path(name)
    stem, suffix = p.stem, p.suffix
    n = 2
    while f"{stem} ({n}){suffix}" in used:
        n += 1
    return f"{stem} ({n}){suffix}"


def build_chapter_aliases(paths: typing.Sequence[Path]) -> list[str]:
    """For a set of audiobook file paths (as they sit inside a torrent —
    possibly nested in subdirectories that group them, e.g. by book/chapter),
    build one alias filename per path, in the same input order, such that:

      - plain alphanumeric sort of the aliases matches the correct narrative
        order, whatever the actual directory/file layout is — the numbers
        found across the whole path (dirs *and* filename, in order) are
        joined into a zero-padded, dot-separated tuple and placed as a
        prefix in front of the original filename;
      - the original filename is otherwise untouched, so a listener who
        pulls the raw cached files onto a plain offline mp3 player (no app,
        no RSS) still gets them in the right order just by sorting on name,
        while still being able to recognize each file by its original name.

    Padding width is computed per tuple position (not globally), so one
    noisy component (e.g. every file sharing a "(2020)" release-year in a
    top-level folder) doesn't force every *other* number to pad wider than
    it needs to.

    Files with no numbers anywhere in their path sort after every numbered
    file (each position gets that position's all-nines sentinel).
    """
    per_file_numbers = [_numbers_in_path(p) for p in paths]
    max_len = max((len(ns) for ns in per_file_numbers), default=0)

    pad_widths = []
    for i in range(max_len):
        values_here = [ns[i] for ns in per_file_numbers if len(ns) > i]
        pad_widths.append(max(2, len(str(max(values_here, default=0)))))

    aliases: list[str] = []
    used: set[str] = set()
    for path, numbers in zip(paths, per_file_numbers):
        if numbers:
            padded = numbers + [0] * (max_len - len(numbers))
            prefix = ".".join(str(n).zfill(pad_widths[i]) for i, n in enumerate(padded))
        elif max_len:
            prefix = ".".join("9" * w for w in pad_widths)
        else:
            prefix = ""

        alias = f"{prefix}-{path.name}" if prefix else path.name
        alias = _dedupe_name(alias, used)
        used.add(alias)
        aliases.append(alias)

    return aliases
