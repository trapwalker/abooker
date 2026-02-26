#! /bin/sh

docker run -d --user $UID:$GID \
    --name qb \
    --rm \
    -p 8080:8080 -p 6881:6881/tcp -p 6881:6881/udp \
    -v $PWD/config:/config \
    -v $PWD/torrents:/torrents \
    -v $PWD/downloads:/downloads \
    wernight/qbittorrent
