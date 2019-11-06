
import contextlib
import typing
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree as e3
from itertools import chain
import string

import click
import yaml
from charset_normalizer import detect as detect_encoding


file_types = {
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

info_file_masks = [
    'readme.*',
    'Readme.*',
    'README.*',
    'info.*',
    'Info.*',
    'INFO.*',
    'about.*',
    'About.*',
    'ABOUT.*',
    '*.txt',
    '*.TXT',
    '*.md',
    '*.MD',
    '*.info',
    '*.INFO',
    '*.rst',
    '*.RST',
]

LOCAL_SETTINGS_FILENAME = '.abooker'


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
):
    rss = e3.Element(
        'rss',
        version="2.0",
        attrib={
            "xmlns:googleplay": "http://www.google.com/schemas/play-podcasts/1.0",
            "xmlns:itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
        },
    )
    channel = e3.SubElement(rss, 'channel')
    if title:
        e3.SubElement(channel, 'title').text = title
    if author:
        e3.SubElement(channel, 'googleplay:author').text = author
    if description:
        e3.SubElement(channel, 'description').text = description
    if image:
        e3.SubElement(channel, 'googleplay:image', href=image)
        e3.SubElement(e3.SubElement(channel, 'image'), 'url').text = image
    if lang:
        e3.SubElement(channel, 'language').text = lang
    if link:
        e3.SubElement(channel, 'link').text = link

    for item in items:
        path: Path = item['path']
        ext = path.suffix.strip('.').lower()
        mime = item.get('mime') or file_types.get(ext)
        enclosure_attrs = dict(url=item['url'])
        if mime:
            enclosure_attrs['type'] = mime
        duration = item.get('duration')

        item_node = e3.SubElement(channel, 'item')
        e3.SubElement(item_node, 'title').text = item.get('title') or path.name
        e3.SubElement(item_node, 'enclosure', attrib=enclosure_attrs)
        if duration:
            e3.SubElement(item_node, 'itunes:duration').text = str(duration)

    return rss


def load_settings(path: Path, filename: str = LOCAL_SETTINGS_FILENAME, errors='strict') -> dict:
    with contextlib.suppress(*(errors == 'ignore' and [Exception] or [])):
        with path.joinpath(filename).open() as f:
            data = yaml.safe_load(f)
            return data


def save_settings(settings: dict, path: Path, errors='strict'):
    with contextlib.suppress(*(errors == 'ignore' and [Exception] or [])):
        with path.open('w') as f:
            yaml.dump(settings, f, allow_unicode=True, encoding='utf-8', indent=2)


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
@click.option('--verbose', is_flag=True)
def main(
    path: str, url: str, rss: str, title: str, author: str, description: str, image: str, lang: str, link: str,
    no_local_settings: bool, verbose: bool,
):
    path: Path = Path(path)

    settings = {} if no_local_settings else load_settings(path.parent, errors='ignore') or {}

    url = url or settings.get('url')
    if url:
        settings['url'] = url

    lang = lang or settings.get('lang')
    if lang:
        settings['lang'] = lang

    if not image:
        pics = [f for t in image_types for f in path.rglob(mask_case_fix(f'*.{t}'))]
        if pics:
            image = str(pics[0].relative_to(path.parent))

    if image:
        if not image.startswith('http'):
            image = f'{url.rstrip("/")}/{image}'

    if not description:
        descr_files = [f for t in info_file_masks for f in path.rglob(t)]
        if descr_files:
            with descr_files[0].open('rb') as f:
                raw_description = f.read()
            descr_encoding = detect_encoding(raw_description)['encoding']
            if descr_encoding:
                description = raw_description.decode(descr_encoding)

    if verbose:
        click.echo(f'Processing path: {path} -> {url}\n')

    files = [
        f
        for t in file_types
        for f in path.rglob(mask_case_fix(f'*.{t}'))
    ]
    files = [(str(f).lower(), f) for f in files]
    files.sort()  # TODO: numeric/alphabetic sort
    items = []
    for k, p in files:
        rpath = p.relative_to(path.parent)
        item = dict(
            path=p,
            rpath=rpath,
            url=f'{url.rstrip("/")}/{quote(str(rpath))}',
        )
        items.append(item)
        if verbose:
            click.echo(f'{rpath}:: {item["url"]}')

    if rss:
        rss_xml = make_rss(
            items,
            title=title or path.name,
            author=author, description=description, image=image, lang=lang, link=link,
        )
        rss_path = path.joinpath(rss)
        rss_url = f'{url.rstrip("/")}/{quote(str(rss_path.relative_to(path.parent)))}'
        e3.ElementTree(rss_xml).write(rss_path, encoding='utf-8', xml_declaration=True)
        click.echo(f'RSS: {rss_url}')

    if not no_local_settings:
        save_settings(settings, path=path.parent.joinpath(LOCAL_SETTINGS_FILENAME), errors='ignore')


if __name__ == '__main__':
    main()
