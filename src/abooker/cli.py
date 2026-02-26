from pathlib import Path
from urllib.parse import quote

import click
import yaml
from charset_normalizer import detect as detect_encoding

from abooker.files import iter_files, filename_key, image_file_masks, info_file_masks, media_file_masks
from abooker.rss import make_rss
from abooker.settings import load_settings, save_settings, BOOK_SETTINGS_FILENAME, LOCAL_SETTINGS_FILENAME


@click.command()
@click.option('-d', '--dir', 'path', type=click.Path(file_okay=False, resolve_path=False), required=True)
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
    path: Path = Path(path).absolute()
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
            click.echo(f'\t{rpath}:: {item["url"]}')

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
        save_settings(settings, path=path.parent.joinpath(LOCAL_SETTINGS_FILENAME), errors='ignore')
