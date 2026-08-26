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


# Words that typically precede an actual chapter/track number in torrent-sourced
# audiobook filenames, in any of the languages/scripts commonly seen on trackers.
_CHAPTER_WORD_RE = re.compile(
    r'(?:глав|часть|трек|track|chapter|part|ch|№|#)\D{0,3}(\d{1,4})',
    re.IGNORECASE,
)
# Numbers that look like metadata rather than a chapter index — bitrate/samplerate
# tags immediately next to their unit, or a bare 4-digit year — are deprioritized.
_NOISE_NUMBER_RE = re.compile(r'\d+(?=\s?(?:kbps|khz|hz|bit|bps))', re.IGNORECASE)


def chapter_sort_key(name: str, pad: int = 6) -> str:
    """Build a technical sort key for an audiobook chapter filename.

    Chapter numbering in torrent releases is inconsistent: the index can sit
    anywhere in the name ("Глава 2.mp3" vs "02 - Глава.mp3"), and digits from
    bitrate tags or years can outrank the real chapter number in a naive
    "first digit run wins" scan. This extracts the most likely chapter number,
    moves it to the front of the key and zero-pads it, so plain string sort
    orders chapters correctly regardless of where the number originally sat
    or how it was padded.

    Falls back to filename_key()'s natural (chunked, numeric-aware) ordering
    as a tiebreaker for files that carry the same detected number, and to a
    high sentinel prefix for files with no usable number at all (sorted last,
    after any real chapter).
    """
    stem = Path(name).stem

    noise_spans = {m.start() for m in _NOISE_NUMBER_RE.finditer(stem)}

    keyword_match = _CHAPTER_WORD_RE.search(stem)
    number: int | None = None
    if keyword_match:
        number = int(keyword_match.group(1))
    else:
        for m in re.finditer(r'\d+', stem):
            if m.start() in noise_spans:
                continue
            number = int(m.group(0))
            break

    tiebreak = tuple(
        int(chunk) if chunk.isdigit() else chunk.lower()
        for chunk in re.split(r'(\d+)', stem)
        if chunk
    )
    tiebreak_str = "_".join(str(c).zfill(pad) if isinstance(c, int) else c for c in tiebreak)

    if number is None:
        return f"{'9' * pad}_{tiebreak_str}"
    return f"{str(number).zfill(pad)}_{tiebreak_str}"
