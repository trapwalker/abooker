import typing
from pathlib import Path

from abooker.files import media_types


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
        size = item.get('size')
        if size:
            enclosure_attrs['length'] = str(size)
        duration = item.get('duration')

        item_node = SubElement(channel, 'item')
        SubElement(item_node, 'title').text = item.get('title') or path.name
        SubElement(item_node, 'enclosure', attrib=enclosure_attrs)
        if duration:
            SubElement(item_node, 'itunes:duration').text = str(duration)

    xmlstr = minidom.parseString(tostring(rss, encoding='utf-8')).toprettyxml(indent="  ", encoding='utf-8')
    return xmlstr
