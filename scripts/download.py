import yt_dlp


FOLDER = '_downloads'

ydl_opts = dict(
    outtmpl=f'{FOLDER}/%(id)s.%(ext)s',
    writedescription=True,
    writeinfojson=True,
    writethumbnail=True,
    keepvideo=False,
    postprocessors=[
        {
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
        },
    ],
)

with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    ydl.download(['https://www.youtube.com/watch?v=BaW_jenozKc'])
