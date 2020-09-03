

import youtube_dl


FOLDER = '_downloads'


ydl_opts = dict(
    encoding='utf-8',
    outtmpl=f'{FOLDER}/%(id)s.%(ext)s',
    getdescription=True,
    writedescription=True,
    writeannotations=True,
    writeinfojson=True,
    writethumbnail=True,
    extractaudio=True,
    audioformat='mp3',
    keepvideo=False,
    # format='mp3',
    postprocessors=[
        {
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            # 'preferredquality': opts.audioquality,
            # 'nopostoverwrites': opts.nopostoverwrites,
        }
    ],
)


with youtube_dl.YoutubeDL(ydl_opts) as ydl:
    ydl.download(['https://www.youtube.com/watch?v=BaW_jenozKc'])
