import argparse
import os
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import format_datetime

from googleapiclient import discovery
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive
from pydrive2.files import GoogleDriveFile

ROOT_FOLDER = "gdrive-cast"
FOLDER_TYPE = "application/vnd.google-apps.folder"


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


def upload_file(file_name, folder_id) -> str:

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

    size = os.path.getsize(file_name)
    print(f"Uploading file: {file_name}, size={size}")

    remote_file.SetContentFile(file_name)
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

    output_file = f"{video_id}.mp3"

    command_to_run = command_template.format(video_id=video_id, output_file=output_file)
    print(f"Executing: {command_to_run}")
    subprocess.run(shlex.split(command_to_run), check=True)
    print("Command executed successfully.")
    return output_file


def create_feed_file(feed_file, youtube_channel: YouTubeChannel, video: YouTubeVideo, audio_link):
    rss = ET.Element("rss")
    rss.set('version', '2.0')
    rss.set('xmlns:itunes', 'http://www.itunes.com/dtds/podcast-1.0.dtd')
    rss.set('xmlns:atom', 'http://www.w3.org/2005/Atom')

    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = youtube_channel.title
    ET.SubElement(channel, "link").text = youtube_channel.url
    ET.SubElement(channel, "language").text = 'en-us'
    ET.SubElement(channel, "itunes:author").text = 'author'
    ET.SubElement(channel, "itunes:summary").text = youtube_channel.description
    ET.SubElement(channel, "description").text = youtube_channel.description
    ET.SubElement(channel, "itunes:explicit").text = 'no'
    ET.SubElement(channel, "itunes:category", text='Politics')
    ET.SubElement(channel, "itunes:image", href=youtube_channel.banner_url)

    audio_file_size = os.path.getsize(audio_file)

    item = ET.SubElement(channel, "item")
    ET.SubElement(item, "title").text = video.title
    ET.SubElement(item, "description").text = video.description
    ET.SubElement(item, "itunes:explicit").text = 'no'
    ET.SubElement(item, "enclosure", url=audio_link, length=f'{audio_file_size}', type="audio/mpeg")
    ET.SubElement(item, "guid").text = audio_link
    video_date = datetime.fromisoformat(video.published)
    ET.SubElement(item, "pubDate").text = format_datetime(video_date)

    tree = ET.ElementTree(rss)
    ET.indent(tree, space="\t", level=0)
    tree.write(feed_file, encoding="utf-8")


parser = argparse.ArgumentParser(prog='GDrive Cast', description='Host a podcast on Google Drive')
parser.add_argument('video_id')
parser.add_argument('-p', '--process', type=str, help='Post-processing command. Use {video_id} and {output_file} as placeholders.')
args = parser.parse_args()

# authenticate and init services
gauth = auth()
drive = GoogleDrive(gauth)
youtube = discovery.build('youtube', 'v3', credentials=gauth.credentials)

root = get_or_create_folder(drive, ROOT_FOLDER, 'root')
print('root: %s' % root)

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
print('channel_folder: %s' % root)

channel = YouTubeChannel(youtube, channel_id=video.channel_id)
print("--- Channel ---")
print(f"Title: {channel.title}")
print(f"Description: {channel.description}")
print(f"Banner: {channel.banner_url}")
print(f"URL: {channel.url}")

audio_file = process_file(args.process, video_id)
print(f"Saved file to {audio_file}")

feed_file = 'feed.xml'
audio_link = upload_file(audio_file, channel_folder['id'])
create_feed_file(feed_file, channel, video, audio_link)
feed_link = upload_file(feed_file, channel_folder['id'])
print(f"Feed link: {feed_link}")