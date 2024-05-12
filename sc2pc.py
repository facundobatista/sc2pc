#!/usr/bin/env python

import argparse
import json
import logging
import os
import pathlib
import shutil
import subprocess
import sys
from operator import attrgetter

import bunch
import dateutil.parser
import requests
import yaml
from feedgen.feed import FeedGenerator
from soundcloud import SoundCloud

logger = logging.getLogger()
h = logging.StreamHandler()
h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-10s %(message)s"))
logger.addHandler(h)
logger.setLevel(logging.INFO)


def is_ffmpeg_available():
    """Return true if ffmpeg is available in the operating system."""
    return shutil.which("ffmpeg") is not None


def get_stream_url(client, url):
    headers = client.get_default_headers()
    if client.auth_token:
        headers["Authorization"] = f"OAuth {client.auth_token}"
    req = requests.get(url, params={"client_id": client.client_id}, headers=headers)
    logger.debug(req.url)
    return req.json()["url"]


def download_new_tracks(show, limit_timestamp, podcast_dir, metadata_file_path):
    """Get all tracks from an url more recent that the limit timestamp."""
    client = SoundCloud("KhqBlYHkMDSGNC9DdLrcJHXqaLv5kOrh", None)

    if not client.is_client_id_valid():
        client = SoundCloud("", None)
        if not client.is_client_id_valid():
            raise ValueError("Dynamically generated client_id is not valid")

    user = client.resolve(show.soundcloud_url)
    if not user:
        raise ValueError("URL is not valid")

    logger.info(f"Retrieving all tracks & reposts of user {user.username}...")
    resources = client.get_user_stream(user.id, limit=1000)

    tracks = []
    # for i, item in itertools.islice(enumerate(resources, 1), offset, None):
    for idx, item in enumerate(resources):
        if item.type != "track":
            continue

        if item.created_at <= limit_timestamp.astimezone():
            continue

        # fix the track creation time to be the one of the item (when it was uploaded), otherwise
        # we may get mixed times of the tracks and it breaks the RSS clients: if track A is
        # created before B but uploaded after it, the RSS will see that B is latest, and will
        # never get A
        item.track.created_at = item.created_at

        tracks.append(item.track)

    logger.info("Found %d tracks", len(tracks))
    for idx, track in enumerate(sorted(tracks, key=attrgetter("created_at"))):
        title = track.title.strip('"')
        logger.info("Downloading %d: (%s) %r", idx, track.created_at, title)

        # check if track is useful
        if track.policy == "BLOCK":
            logger.warning("Skipping track %s: geoblocked", track.permalink_url)
            continue
        if not track.media.transcodings:
            logger.warning("Skipping track %s: no transcodings available", track.permalink_url)
            continue
        for transcoding in track.media.transcodings:
            if transcoding.format.protocol == "hls" and "mp3" in transcoding.preset:
                break
        else:
            logger.warning(
                "Skipping track %s: no mp3 transcoding found in %s",
                track.permalink_url, track.media.transcodings)
            continue
        if transcoding.url is None:
            logger.warning("Skipping track %s: no url", track.permalink_url)
            continue

        # downloadable track!
        filename = podcast_dir / f"{show.id}_{track.id}.mp3"
        if filename.exists():
            logger.warning("Not downloading because already there: %r", str(filename))
        else:
            # get the requests stream
            stream_url = get_stream_url(client, transcoding.url)

            # XXX use this in a more direct way
            p = subprocess.Popen(
                ["ffmpeg", "-i", stream_url, "-c", "copy", str(filename), "-loglevel", "error"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            stdout, stderr = p.communicate()
            if stderr:
                logger.error(stderr.decode("utf-8"))
                continue

            logger.info("Downloaded ok: %r", str(filename))
        metadata = {
            "show_id": show.id,
            "track_id": track.id,
            "title": title,
            "date": track.created_at.isoformat(),
            "description": track.description,
        }
        with open(metadata_file_path, "at", encoding="utf8") as fh:
            fh.write(json.dumps(metadata) + "\n")


def load_config(config_file_path, selected_show):
    """Load the configuration file and validate format."""
    with open(config_file_path, 'rt', encoding='utf8') as fh:
        from_config_file = yaml.safe_load(fh)

    if not isinstance(from_config_file, dict):
        raise ValueError("Bad general config format, must be a dict/map.")

    base_keys = {'name', 'description', 'timezone', 'soundcloud_url', 'image_url'}

    config_data = []
    for show_id, show_data in from_config_file.items():
        if not show_id.isalnum():
            raise ValueError(
                "Bad format for show id {!r} (must be alphanumerical)".format(show_id))

        if selected_show is not None and selected_show != show_id:
            logger.warning("Ignoring config because not selected show: %r", show_id)
            continue

        missing = base_keys - set(show_data)
        if missing:
            raise ValueError("Missing keys {} for show id {}".format(missing, show_id))

        if not show_data["soundcloud_url"].startswith("https://soundcloud.com/"):
            logger.error("Invalid soundclad url: %r", show_data.soundcloud_url)
            exit()

        config_data.append(bunch.Bunch(show_data, id=show_id))

    return config_data


def get_last_track_times(metadata_file_path):
    """Return the last track times for each show, or None if file is not there."""
    # (try to) open it
    if not os.path.exists(metadata_file_path):
        return

    last_track_times = {}
    with open(metadata_file_path, 'rt', encoding='utf8') as fh:
        for line in fh:
            data = json.loads(line)
            tstamp = dateutil.parser.parse(data["date"])
            show_id = data["show_id"]
            if show_id in last_track_times:
                last_track_times[show_id] = max(last_track_times[show_id], tstamp)
            else:
                last_track_times[show_id] = tstamp

    return last_track_times


class Main:
    """Main entry point."""

    def __init__(
            self, metadata_file_path, podcast_dir, config_file_path, base_public_url, since,
            selected_show):
        self.podcast_dir = podcast_dir
        self.base_public_url = base_public_url
        self.metadata_file_path = metadata_file_path

        # get the last run
        self.last_track_times = get_last_track_times(metadata_file_path)
        if self.last_track_times is None:
            if since is None:
                logger.error("Parameters problem: Must indicate a start point in time "
                             "(through metadata file or --since parameter")
                exit()
            else:
                self.last_track_times = {"__since__": since}
        else:
            if since is not None:
                self.last_track_times = {"__since__": since}

        # open the config file
        try:
            self.config_data = load_config(config_file_path, selected_show)
        except ValueError as exc:
            logger.error("Problem loading config: %s", exc)
            exit()

        logger.info("Loaded config for shows %s", sorted(x.id for x in self.config_data))

    def get_episodes(self, show):
        """Get episodes for a given show."""
        logger.info("Downloading %r", show.name)

        if "__since__" in self.last_track_times:
            start_datetime = self.last_track_times["__since__"]
            logger.info("Since (passed arg): %s", start_datetime)
        else:
            start_datetime = self.last_track_times[show.id]
            logger.info("Since (metadata): %s", start_datetime)
        download_new_tracks(show, start_datetime, self.podcast_dir, self.metadata_file_path)

    def run(self):
        """Process everything."""
        for show_data in self.config_data:
            logger.info("Processing show %s", show_data.id)
            self.get_episodes(show_data)
            self.write_podcast(show_data)

    def write_podcast(self, show):
        """Create the podcast file."""
        fg = FeedGenerator()
        fg.load_extension('podcast')

        url = "{}{}.xml".format(self.base_public_url, show.id)
        fg.id(url.split('.')[0])
        fg.title(show.name)
        fg.image(show.image_url)
        fg.description(show.description)
        fg.link(href=url, rel='self')

        # load ALL the metadata, building a dict by track id
        metadata = {}
        with open(self.metadata_file_path, "rt", encoding="utf8") as fh:
            for line in fh:
                datum = json.loads(line)
                metadata[datum["track_id"]] = datum

        # collect all mp3s for the given show
        all_mp3s = self.podcast_dir.glob(f"{show.id}_*.mp3")

        for filepath in all_mp3s:
            filename = filepath.name
            track_id = int(filename.split("_")[1].split('.')[0])
            this_metadatum = metadata[track_id]

            mp3_date = this_metadatum["date"]
            mp3_size = filepath.stat().st_size
            mp3_url = self.base_public_url + filename
            title = this_metadatum["title"]
            description = this_metadatum["description"]

            # build the rss entry
            fe = fg.add_entry()
            fe.id(str(track_id))
            fe.pubDate(mp3_date)
            fe.description(description)
            fe.title(title)
            fe.enclosure(mp3_url, str(mp3_size), 'audio/mpeg')

        fg.rss_str(pretty=True)
        fg.rss_file(os.path.join(podcast_dir, '{}.xml'.format(show.id)))


if __name__ == '__main__':
    if not is_ffmpeg_available():
        print("ERROR: ffmpeg is not installed")
        sys.exit(1)

    parser = argparse.ArgumentParser()
    parser.add_argument('--since', help="A date (YYYY-MM--DD) to get stuff since.")
    parser.add_argument('--show', help="Work with this show only.")
    parser.add_argument('--quiet', action='store_true', help="Be quiet, unless any issue is found")
    parser.add_argument('podcast_dir', help="The directory where podcast files will be stored")
    parser.add_argument('metadata_file', help="The file to store the metadata")
    parser.add_argument('config_file', help="The configuration file")
    parser.add_argument('base_public_url', help="The public URL from where the podcast is served")
    args = parser.parse_args()

    # parse input
    since = None if args.since is None else dateutil.parser.parse(args.since)
    if args.quiet:
        logger.setLevel(logging.WARNING)
    podcast_dir = pathlib.Path(args.podcast_dir)

    m = Main(
        args.metadata_file, podcast_dir, args.config_file, args.base_public_url, since, args.show)
    m.run()
