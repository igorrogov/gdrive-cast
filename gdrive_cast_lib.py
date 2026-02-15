import configparser
from datetime import datetime
import os
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from email.utils import format_datetime
from typing import Iterable
from urllib.parse import urlparse, parse_qs

import humanize
from googleapiclient import discovery
from litellm import completion
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive
from pydrive2.files import GoogleDriveFile
from youtube_transcript_api import YouTubeTranscriptApi, FetchedTranscriptSnippet
from youtube_transcript_api.formatters import _TextBasedFormatter


VERSION = "1.3"

ROOT_FOLDER = "gdrive-cast"
FOLDER_TYPE = "application/vnd.google-apps.folder"
MEDIA_CACHE_FOLDER = "media-cache"
FEED_CACHE_FOLDER = "feed-cache"
FEED_FILE_NAME = "feed.xml"

class MyFormatter(_TextBasedFormatter):
    def _format_timestamp(self, hours: int, mins: int, secs: int, ms: int) -> str:
        return "{:02d}:{:02d}:{:02d}".format(hours, mins, secs)

    def _format_transcript_header(self, lines: Iterable[str]) -> str:
        return "\n\n".join(lines) + "\n"

    def _format_transcript_helper(
            self, i: int, time_text: str, snippet: FetchedTranscriptSnippet
    ) -> str:
        return "{}\n{}".format(time_text, snippet.text)

class YouTubeVideo:

    def __init__(self, youtube, video_id):
        response = youtube.videos().list(part='snippet,contentDetails', id=video_id).execute()
        snippet = response['items'][0]['snippet']

        self.id = video_id
        self.title = snippet['title']
        self.description = snippet['description']
        self.published = snippet['publishedAt']
        self.thumbnail_url = snippet['thumbnails']['standard']['url']

        self.channel_id = snippet['channelId']
        self.channel_title = snippet['channelTitle']

class YouTubeChannel:

    def __init__(self, youtube, channel_id):
        response = youtube.channels().list(part='snippet,brandingSettings', id=channel_id).execute()
        item = response['items'][0]
        snippet = item.get('snippet', {})

        self.title = snippet.get('title', 'N/A')
        self.description = snippet.get('description', 'No description available.')
        self.url = f"https://www.youtube.com/channel/{channel_id}"

        branding = item.get('brandingSettings', {}).get('image', {})
        self.banner_url = branding.get('bannerExternalUrl', 'No banner image found.')


def extract_video_id(video_url):
    # parse YouTube URL and extract video ID
    # https://www.youtube.com/watch?v=XYZ -> XYZ

    parsed_url = urlparse(video_url)
    if "youtube" not in parsed_url.netloc:
        print("YouTube URL is required")
        sys.exit(-1)

    query_params = parse_qs(parsed_url.query)

    if "v" not in query_params:
        print(f"Param not found: 'v'. Found: {query_params}")
        sys.exit(-1)

    video_id = query_params['v'][0]
    return video_id


def process_file(command_template: str, video_id: str) -> str:
    if not command_template:
        print("No external command configured. Skipping.")
        sys.exit(-1)

    output_file = f"{MEDIA_CACHE_FOLDER}/{video_id}.mp3"

    command_to_run = command_template.format(video_id=video_id, output_file=output_file)
    print(f"Executing: {command_to_run}")
    subprocess.run(shlex.split(command_to_run), check=True)
    print("Command executed successfully.")
    return output_file


class PodcastManager:

    def __init__(self, root_folder_name="gdrive-cast"):
        ET.register_namespace('itunes', 'http://www.itunes.com/dtds/podcast-1.0.dtd')

        self.config = configparser.ConfigParser()
        self.config.read('config.ini')

        self.root_folder_name = root_folder_name
        self.gauth = self._auth()
        self.drive = GoogleDrive(self.gauth)
        self.youtube = discovery.build('youtube', 'v3', credentials=self.gauth.credentials)
        self.root = self.get_or_create_folder(self.root_folder_name, 'root')

    def _auth(self) -> GoogleAuth:
        gauth = GoogleAuth()

        gauth.LoadCredentialsFile()

        # gauth.auth_params = {
        #     'access_type': 'offline',
        #     'prompt': 'consent'
        # }
        gauth.settings['oauth_scope'] = [
            'https://www.googleapis.com/auth/drive',
            'https://www.googleapis.com/auth/youtube.readonly'
        ]

        if gauth.credentials is None:
            print("Web server auth...")
            gauth.LocalWebserverAuth()
        elif gauth.access_token_expired:
            print("Credentials expired. Refreshing...")
            gauth.Refresh()
        else:
            print("Authorizing...")
            gauth.Authorize()

        gauth.SaveCredentialsFile()

        return gauth

    def find_channel_folder(self, channel_index: int) -> GoogleDriveFile | None:
        podcast_folders = self.list_podcast_folders_sorted()

        if 1 <= channel_index <= len(podcast_folders):
            return podcast_folders[channel_index - 1]

        return None

    def list_podcast_folders_sorted(self):
        return self.drive.ListFile({
            'q': f"'{self.root['id']}' in parents and trashed=false and mimeType='{FOLDER_TYPE}'",
            'orderBy': 'folder'
        }).GetList()

    def fetch_library_data(self):
        print("Fetching podcast data...")
        podcast_folders = self.list_podcast_folders_sorted()

        if not os.path.exists(FEED_CACHE_FOLDER):
            os.makedirs(FEED_CACHE_FOLDER)

        library = []

        index = 1
        for f in podcast_folders:
            remote_feed_files = self.drive.ListFile({
                'q': f"title='{FEED_FILE_NAME}' and '{f['id']}' in parents and trashed=false"
            }).GetList()

            episodes = []
            if remote_feed_files:
                remote_feed_file = remote_feed_files[0]
                local_feed_file = f"{FEED_CACHE_FOLDER}/{f['id']}.xml"
                remote_feed_file.GetContentFile(local_feed_file)
                tree = ET.parse(local_feed_file)
                channel = tree.getroot().find('channel')
                # print(f"\n{index}. Channel: {f['title']} - {channel.find('title').text}")
                index += 1

                for item in channel.findall('item'):
                    episodes.append({
                        'id': str(index),
                        'title': item.find('title').text,
                        'date': item.find('pubDate').text
                    })

                library.append({'id': f['id'], 'title': channel.find('title').text, 'episodes': episodes})

        return library

    def upload_file(self, file_path, file_name, folder_id) -> str:

        # check whether the file already exists
        query = f"title = '{file_name}' and '{folder_id}' in parents and trashed = false"
        file_list = self.drive.ListFile({'q': query}).GetList()
        if file_list:
            print(f"Overriding existing file: {file_name}")
            remote_file = file_list[0]
            created = False
        else:
            print(f"Creating a new file: {file_name}")
            remote_file = self.drive.CreateFile({'parents': [{'id': folder_id}]})
            created = True

        size = os.path.getsize(file_path)
        print(f"Uploading file: {file_name}, size={humanize.naturalsize(size, binary=True)}")

        remote_file.SetContentFile(file_path)
        remote_file.Upload()

        # print(f"Uploaded file: `{file_to_upload}`")
        direct_link = f"https://drive.usercontent.google.com/download?export=download&confirm=t&id={remote_file['id']}"
        print(f"Uploaded file (direct link): {direct_link}")

        # add "Anyone with link" permission
        if created:
            remote_file.InsertPermission({
                'type': 'anyone',
                'value': 'anyone',
                'role': 'reader'}
            )
            print('Added permission: "Anyone with link"')

        return direct_link

    def delete_podcast(self, channel_index: int):
        ch = self.find_channel_folder(channel_index)
        if ch:
            ch.Delete()

    def get_timestamps(self, video_url) -> str:
        return self.get_timestamps_by_video_id(extract_video_id(video_url))

    def get_timestamps_by_video_id(self, video_id) -> str:
        # first, extract the transcript for the video
        print(f"Getting transcript for: {video_id}")
        ytt_api = YouTubeTranscriptApi()
        formatter = MyFormatter()
        transcript = ytt_api.fetch(video_id, languages=["ru", "en"])
        text_output = formatter.format_transcript(transcript)
        # print(text_output)
        print(f"Successfully loaded transcript: {humanize.naturalsize(len(text_output), binary=True)}")

        # then, use LLM to create chapters
        model = self.config['app']['llm_model']
        print(f"Creating chapters using: {model}")
        os.environ[self.config['app']['llm_api_key_type']] = self.config['app']['llm_api_key']
        with open("chapters_prompt.txt", "r") as f:
            prompt = f.read()
        content = prompt + text_output
        response = completion(
            model=model,
            messages=[{"role": "user", "content": content}]
        )
        return "\nTimestamps:\n" + response.choices[0].message.content

    def purge_podcast(self, channel_index: int):
        ch = self.find_channel_folder(channel_index)
        if not ch:
            print(f"Channel folder not found: {channel_index}")
            return

        file_list = self.drive.ListFile({
            'q': f"'{ch['id']}' in parents and trashed=false"
        }).GetList()

        if not file_list:
            print(f"Channel folder not found: {channel_index}")
            return

        for f in file_list:
            if f['title'] == "feed.xml":
                remote_feed_file = f
                local_feed_file = f"{FEED_CACHE_FOLDER}/{f['id']}.xml"
                remote_feed_file.GetContentFile(local_feed_file)
                tree = ET.parse(local_feed_file)
                channel = tree.getroot().find('channel')

                print(f"Updating channel: {f['title']} - {channel.find('title').text}")

                episodes = channel.findall('item')
                for episode in episodes:
                    print(f"Deleted episode: {episode.find('title').text}")
                    channel.remove(episode)

                ET.indent(tree, space="\t", level=0)
                tree.write(local_feed_file, encoding="utf-8", xml_declaration=True)

                size = os.path.getsize(local_feed_file)
                print(f"Uploading feed file: {f['title']}, size={humanize.naturalsize(size, binary=True)}")
                remote_feed_file.SetContentFile(local_feed_file)
                remote_feed_file.Upload()

                print(f"Updated feed file: {f['title']}")
            else:
                f.Delete()
                print(f"Deleted file: {f['title']}")

    def download_podcast(self, video_url: str, add_generated_timestamps):
        video_id = extract_video_id(video_url)
        print(f"Video ID: {video_id}")

        video = YouTubeVideo(youtube=self.youtube, video_id=video_id)

        print("--- Video Details ---")
        print(f"Title: {video.title}")
        print(f"Published at: {video.published}")
        print(f"Thumbnail: {video.thumbnail_url}")
        print(f"Channel Name: {video.channel_title}")
        print(f"Channel ID: {video.channel_id}")
        # print("\n--- Description ---")
        # print(video.description)

        channel_folder = self.get_or_create_folder(video.channel_id, self.root['id'])
        print(f"Using channel folder: {channel_folder['title']} ({channel_folder['id']})")

        channel = YouTubeChannel(self.youtube, channel_id=video.channel_id)
        print("--- Channel ---")
        print(f"Title: {channel.title}")
        print(f"Description: {channel.description}")
        print(f"Banner: {channel.banner_url}")
        print(f"URL: {channel.url}")

        process_command_template = self.config['app']['youtube_process_command']
        audio_file_name = f"{video_id}.mp3"
        audio_file_path = process_file(process_command_template, video_id)
        print(f"Saved file to {audio_file_path}")

        feed_file = FEED_FILE_NAME
        audio_link = self.upload_file(audio_file_path, audio_file_name, channel_folder['id'])
        self.create_or_append_feed_file(feed_file, channel_folder['id'], channel, video, audio_link, audio_file_path, add_generated_timestamps)
        feed_link = self.upload_file(feed_file, feed_file, channel_folder['id'])
        print(f"Feed link: {feed_link}")

    def create_or_append_feed_file(self, feed_file, parent_folder_id, youtube_channel: YouTubeChannel, video: YouTubeVideo, audio_link, audio_file_path, add_generated_timestamps):

        # remove local feed if exists
        if os.path.exists(feed_file):
            os.remove(feed_file)

        # download the existing feed.xml from Google Drive if exists

        remote_feed_files = self.drive.ListFile({
            'q': f"title='{feed_file}' and '{parent_folder_id}' in parents and trashed=false"
        }).GetList()

        if remote_feed_files:
            remote_feed_file = remote_feed_files[0]
            size_str = humanize.naturalsize(remote_feed_file.get('fileSize', 0), binary=True)
            print(f"Downloading remote feed file: {feed_file} ({size_str})...")
            remote_feed_file.GetContentFile(feed_file)

            print("Parsing existing feed...")
            tree = ET.parse(feed_file)
            channel = tree.getroot().find('channel')
        else:
            print("Creating new feed file...")
            rss = ET.Element("rss")
            rss.set('version', '2.0')
            rss.set('xmlns:itunes', 'http://www.itunes.com/dtds/podcast-1.0.dtd')
            rss.set('xmlns:atom', 'http://www.w3.org/2005/Atom')

            tree = ET.ElementTree(rss)

            channel = ET.SubElement(rss, "channel")
            ET.SubElement(channel, "title").text = youtube_channel.title
            ET.SubElement(channel, "link").text = youtube_channel.url
            ET.SubElement(channel, "language").text = 'en-us'
            ET.SubElement(channel, "itunes:author").text = 'GDrive Cast'
            ET.SubElement(channel, "itunes:summary").text = youtube_channel.description
            ET.SubElement(channel, "description").text = youtube_channel.description
            ET.SubElement(channel, "itunes:explicit").text = 'no'
            ET.SubElement(channel, "itunes:category", text='Politics')
            ET.SubElement(channel, "itunes:image", href=youtube_channel.banner_url)

        audio_file_size = os.path.getsize(audio_file_path)

        podcast_description = video.description
        # optionally add generated chapters / timestamps
        if add_generated_timestamps:
            timestamps = self.get_timestamps_by_video_id(video.id)
            podcast_description += "\n" + timestamps
            print(f" ----- ")
            print(f" Added generated chapters:\n\n{timestamps}")
            print(f" ----- ")

        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = video.title
        ET.SubElement(item, "description").text = podcast_description
        ET.SubElement(item, "itunes:explicit").text = 'no'
        ET.SubElement(item, "enclosure", url=audio_link, length=f'{audio_file_size}', type="audio/mpeg")
        ET.SubElement(item, "guid").text = audio_link
        video_date = datetime.fromisoformat(video.published)
        ET.SubElement(item, "pubDate").text = format_datetime(video_date)

        ET.indent(tree, space="\t", level=0)
        tree.write(feed_file, encoding="utf-8", xml_declaration=True)

    def get_or_create_folder(self, name, parent_folder_id) -> GoogleDriveFile:
        roots = self.drive.ListFile({
            'q': f"title='{name}' and '{parent_folder_id}' in parents and trashed=false and mimeType='{FOLDER_TYPE}'"
        }).GetList()

        if roots:
            return roots[0]

        # If the list is empty, the folder doesn't exist.
        print(f"Folder '{name}' not found. Creating a new one...")
        folder_metadata = {
            'title': name,
            'parents': [{'id': parent_folder_id}],
            'mimeType': FOLDER_TYPE
        }
        folder = self.drive.CreateFile(folder_metadata)
        folder.Upload()
        print(f"Folder '{folder['title']}' created with ID: {folder['id']}")
        return folder