import argparse
import configparser
import os
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import format_datetime

import humanize
from googleapiclient import discovery
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive
from pydrive2.files import GoogleDriveFile

ROOT_FOLDER = "gdrive-cast"
FOLDER_TYPE = "application/vnd.google-apps.folder"
MEDIA_CACHE_FOLDER = "media-cache"
FEED_CACHE_FOLDER = "feed-cache"
FEED_FILE_NAME = "feed.xml"

def auth() -> GoogleAuth:
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


def get_or_create_folder(drive, name, parent_folder_id) -> GoogleDriveFile:
    roots = drive.ListFile({
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
    folder = drive.CreateFile(folder_metadata)
    folder.Upload()
    print(f"Folder '{folder['title']}' created with ID: {folder['id']}")
    return folder


def upload_file(file_path, file_name, folder_id) -> str:

    # check whether the file already exists
    query = f"title = '{file_name}' and '{folder_id}' in parents and trashed = false"
    file_list = drive.ListFile({'q': query}).GetList()
    if file_list:
        print(f"Overriding existing file: {file_name}")
        remote_file = file_list[0]
        created = False
    else:
        print(f"Creating a new file: {file_name}")
        remote_file = drive.CreateFile({'parents': [{'id': folder_id}]})
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


class YouTubeVideo:

    def __init__(self, youtube, video_id):
        response = youtube.videos().list(part='snippet,contentDetails', id=video_id).execute()
        snippet = response['items'][0]['snippet']

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


def create_or_append_feed_file(feed_file, parent_folder_id, youtube_channel: YouTubeChannel, video: YouTubeVideo, audio_link, audio_file_path):

    # remove local feed if exists
    if os.path.exists(feed_file):
        os.remove(feed_file)

    # download the existing feed.xml from Google Drive if exists

    remote_feed_files = drive.ListFile({
        'q': f"title='{feed_file}' and '{parent_folder_id}' in parents and trashed=false"
    }).GetList()

    if remote_feed_files:
        remote_feed_file = remote_feed_files[0]
        size_str = humanize.naturalsize(remote_feed_file.get('fileSize', 0), binary = True)
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

    item = ET.SubElement(channel, "item")
    ET.SubElement(item, "title").text = video.title
    ET.SubElement(item, "description").text = video.description
    ET.SubElement(item, "itunes:explicit").text = 'no'
    ET.SubElement(item, "enclosure", url=audio_link, length=f'{audio_file_size}', type="audio/mpeg")
    ET.SubElement(item, "guid").text = audio_link
    video_date = datetime.fromisoformat(video.published)
    ET.SubElement(item, "pubDate").text = format_datetime(video_date)

    ET.indent(tree, space="\t", level=0)
    tree.write(feed_file, encoding="utf-8", xml_declaration=True)

def list_podcasts(root: GoogleDriveFile):
    podcast_folders = list_podcast_folders_sorted(root)

    if not os.path.exists(FEED_CACHE_FOLDER):
        os.makedirs(FEED_CACHE_FOLDER)

    index = 1
    for f in podcast_folders:
        remote_feed_files = drive.ListFile({
            'q': f"title='{FEED_FILE_NAME}' and '{f['id']}' in parents and trashed=false"
        }).GetList()

        if remote_feed_files:
            remote_feed_file = remote_feed_files[0]
            local_feed_file = f"{FEED_CACHE_FOLDER}/{f['id']}.xml"
            remote_feed_file.GetContentFile(local_feed_file)
            tree = ET.parse(local_feed_file)
            channel = tree.getroot().find('channel')
            print(f"\n{index}. Channel: {f['title']} - {channel.find('title').text}")
            index += 1

            # show episodes
            episodes = channel.findall('item')
            for episode in episodes:
                print(f" - {episode.find('title').text}")


def delete_podcast(root: GoogleDriveFile, channel_index: int):
    ch = find_channel_folder(root, channel_index)
    if ch:
        ch.Delete()
        print(f"Deleted podcast / channel folder: {channel_index}")


def purge_podcast(root: GoogleDriveFile, channel_index: int):
    ch = find_channel_folder(root, channel_index)
    if not ch:
        print(f"Channel folder not found: {channel_index}")
        return

    file_list = drive.ListFile({
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


def list_podcast_folders_sorted(root: GoogleDriveFile):
    return drive.ListFile({
        'q': f"'{root['id']}' in parents and trashed=false and mimeType='{FOLDER_TYPE}'",
        'orderBy': 'folder'
    }).GetList()

def find_channel_folder(root: GoogleDriveFile, channel_index: int) -> GoogleDriveFile | None:
    podcast_folders = list_podcast_folders_sorted(root)

    if 1 <= channel_index <= len(podcast_folders):
        return podcast_folders[channel_index - 1]

    return None


## Program start

ET.register_namespace('itunes', 'http://www.itunes.com/dtds/podcast-1.0.dtd')

parser = argparse.ArgumentParser(prog='GDrive Cast', description='Host a podcast on Google Drive')
parser.add_argument('video_id', nargs='?', default="")
parser.add_argument("-l", "--list", help="List existing podcast channels and exit.", action="store_true")
parser.add_argument("-d", "--delete", help="Delete a channel by its index (starts with 1).")
parser.add_argument("-p", "--purge", help="Purge a channel by index (starts with 1) (delete all episodes but keep the channel).")
args = parser.parse_args()

config = configparser.ConfigParser()
config.read('config.ini')

# authenticate and init services
gauth = auth()
drive = GoogleDrive(gauth)
youtube = discovery.build('youtube', 'v3', credentials=gauth.credentials)

root = get_or_create_folder(drive, ROOT_FOLDER, 'root')
print(f"Using root folder: {root['title']} ({root['id']})")

if args.list:
    list_podcasts(root)
    sys.exit(0)

if args.delete:
    delete_podcast(root, int(args.delete))
    sys.exit(0)

if args.purge:
    purge_podcast(root, int(args.purge))
    sys.exit(0)

if not args.video_id:
    print("Video ID is required")
    sys.exit(-1)

video_id = args.video_id
video = YouTubeVideo(youtube=youtube, video_id=video_id)

print("--- Video Details ---")
print(f"Title: {video.title}")
print(f"Published at: {video.published}")
print(f"Thumbnail: {video.thumbnail_url}")
print(f"Channel Name: {video.channel_title}")
print(f"Channel ID: {video.channel_id}")
# print("\n--- Description ---")
# print(video.description)

channel_folder = get_or_create_folder(drive, video.channel_id, root['id'])
print(f"Using channel folder: {channel_folder['title']} ({channel_folder['id']})")

channel = YouTubeChannel(youtube, channel_id=video.channel_id)
print("--- Channel ---")
print(f"Title: {channel.title}")
print(f"Description: {channel.description}")
print(f"Banner: {channel.banner_url}")
print(f"URL: {channel.url}")

process_command_template = config['app']['youtube_process_command']
audio_file_name = f"{video_id}.mp3"
audio_file_path = process_file(process_command_template, video_id)
print(f"Saved file to {audio_file_path}")

feed_file = FEED_FILE_NAME
audio_link = upload_file(audio_file_path, audio_file_name, channel_folder['id'])
create_or_append_feed_file(feed_file, channel_folder['id'], channel, video, audio_link, audio_file_path)
feed_link = upload_file(feed_file, feed_file, channel_folder['id'])
print(f"Feed link: {feed_link}")