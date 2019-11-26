
import contextlib
import typing
from pathlib import Path
from urllib.parse import quote
import re

import click
import yaml
from charset_normalizer import detect as detect_encoding


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

BOOK_SETTINGS_FILENAME = '.abook'
LOCAL_SETTINGS_FILENAME = '.abooker'
LOCAL_SETTINGS_FILEMASK = '.abook*'


def mask_case_fix(mask: str) -> str:
    return ''.join(
        f'[{c.lower()}{c.upper()}]'
        if c.isalpha() else
        c
        for c in mask
    )


def make_rss(
    items: typing.Sequence[typing.Dict[str, typing.Any]],
    title=None, author=None, description=None, image=None, lang=None, link=None,
) -> bytes:
    from xml.etree.ElementTree import Element, SubElement, tostring
    from xml.dom import minidom

    rss = Element(
        'rss',
        version="2.0",
        attrib={
            "xmlns:googleplay": "http://www.google.com/schemas/play-podcasts/1.0",
            "xmlns:itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
        },
    )
    channel = SubElement(rss, 'channel')
    if title:
        SubElement(channel, 'title').text = title
    if author:
        SubElement(channel, 'googleplay:author').text = author
    if description:
        SubElement(channel, 'description').text = description
    if image:
        SubElement(channel, 'googleplay:image', href=image)
        SubElement(SubElement(channel, 'image'), 'url').text = image
    if lang:
        SubElement(channel, 'language').text = lang
    if link:
        SubElement(channel, 'link').text = link

    for item in items:
        path: Path = item['path']
        ext = path.suffix.strip('.').lower()
        mime = item.get('mime') or media_types.get(ext)
        enclosure_attrs = dict(url=item['url'])
        if mime:
            enclosure_attrs['type'] = mime
        duration = item.get('duration')

        item_node = SubElement(channel, 'item')
        SubElement(item_node, 'title').text = item.get('title') or path.name
        SubElement(item_node, 'enclosure', attrib=enclosure_attrs)
        if duration:
            SubElement(item_node, 'itunes:duration').text = str(duration)

    xmlstr = minidom.parseString(tostring(rss, encoding='utf-8')).toprettyxml(indent="  ", encoding='utf-8')
    return xmlstr


# TODO: Extract settings class
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


@click.command()
@click.option('-d', '--dir', 'path', type=click.Path(file_okay=False, resolve_path=True), required=True)
@click.option('-u', '--url', type=str)
@click.option('--rss', type=str, default='playlist.rss')
@click.option('--title', type=str)
@click.option('--author', type=str)
@click.option('--description', type=str)
@click.option('--image', type=str)
@click.option('--lang', type=str)
@click.option('--link', type=str)
@click.option('--no-local-settings', is_flag=True)
@click.option('-S', '--save-local-settings', is_flag=True)
@click.option('--verbose', is_flag=True)
def main(
    path: str, url: str, rss: str, title: str, author: str, description: str, image: str, lang: str, link: str,
    no_local_settings: bool, save_local_settings: bool, verbose: bool,
):
    path: Path = Path(path)
    if verbose:
        _descr_lines = description and '\n            '.join(['|'] + description.split('\n'))
        click.echo(
            f'CLI params:\n' 
            f'\tpath:    {path}\n'
            f'\turl:     {url}\n'
            f'\trss:     {rss}\n'
            f'\ttitle:   {title}\n'
            f'\tauthor:  {author}\n'
            f'\timage:   {image}\n'
            f'\tlang:    {lang}\n'
            f'\tlink:    {link}\n'
            f'\tverbose: {verbose}\n' 
            f'\tno_local_settings:   {no_local_settings}\n'
            f'\tsave_local_settings: {save_local_settings}\n'
            f'\tdescription: {_descr_lines}\n'
        )

    settings = {} if no_local_settings else load_settings(path.parent, errors='ignore') or {}
    book_settings = load_settings(path, errors='ignore') or {}
    if verbose:
        click.echo(f'# settings: #\n{yaml.dump(settings, indent=2, allow_unicode=True)}')
        click.echo(f'# book settings: #\n{yaml.dump(book_settings, indent=2, allow_unicode=True)}')

    url = url or settings.get('url')
    if url:
        settings['url'] = url

    lang = lang or settings.get('lang')
    if lang:
        settings['lang'] = lang

    title = title or book_settings.get('title')
    if title:
        book_settings['title'] = title

    author = author or book_settings.get('author')
    if author:
        book_settings['author'] = author

    description = description or book_settings.get('description') or book_settings.get('about')
    if description:
        book_settings['description'] = description

    if not image:
        pics = list(iter_files(path, mask_list=image_file_masks))
        if pics:
            image = str(pics[0].relative_to(path.parent))

    if image:
        if not image.startswith('http'):
            image = f'{url and url.rstrip("/") or ""}/{image}'

    if not description:
        for descr_file in iter_files(path, mask_list=info_file_masks):
            try:
                raw_description = descr_file.read_bytes()
                descr_encoding = detect_encoding(raw_description)['encoding']
                if raw_description and descr_encoding:
                    description = raw_description.decode(descr_encoding)
                    break
            except Exception as e:
                click.echo(f'Description reading from {descr_file} error: {e}', err=True)

    files = iter_files(path, mask_list=media_file_masks)
    files = [(filename_key(f, root=path), f) for f in files]
    items = []
    for k, p in sorted(files):
        rpath = p.relative_to(path.parent)
        item = dict(
            path=p,
            rpath=rpath,
            url=f'{url and url.rstrip("/") or ""}/{quote(str(rpath))}',
        )
        items.append(item)
        if verbose:
            click.echo(f'{rpath}:: {item["url"]}')

    if rss:
        rss_path = path.joinpath(rss)
        rss_url = f'{url and url.rstrip("/") or ""}/{quote(str(rss_path.relative_to(path.parent)))}'
        rss_path.write_bytes(make_rss(
            items,
            title=title or path.name,
            author=author, description=description, image=image, lang=lang, link=link,
        ))
        click.echo(f'RSS: {rss_url}')

    save_settings(book_settings, path=path.joinpath(BOOK_SETTINGS_FILENAME), errors='ignore')
    if save_local_settings:
        # todo: do not save settings if they was not changed
        save_settings(settings, path=path.parent.joinpath(LOCAL_SETTINGS_FILENAME), errors='ignore')


if __name__ == '__main__':
    main()
