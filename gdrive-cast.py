import sys

from googleapiclient import discovery
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive
from pydrive2.files import GoogleDriveFile

ROOT_FOLDER = "gdrive-cast"
FOLDER_TYPE = "application/vnd.google-apps.folder"


def auth() -> GoogleAuth:
    gauth = GoogleAuth()

    gauth.LoadCredentialsFile("mycreds.txt")

    gauth.auth_params = {
        'access_type': 'offline',
        'prompt': 'consent'
    }
    gauth.settings['oauth_scope'] = [
        'https://www.googleapis.com/auth/drive',
        'https://www.googleapis.com/auth/youtube.readonly'
    ]

    if gauth.credentials is None:
        gauth.LocalWebserverAuth()
    elif gauth.access_token_expired:
        gauth.Refresh()
    else:
        gauth.Authorize()
    gauth.SaveCredentialsFile("mycreds.txt")

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


def upload_file(file_path, folder_id):
    file_to_upload = drive.CreateFile({'parents': [{'id': folder_id}]})
    file_to_upload.SetContentFile(file_path)
    file_to_upload.Upload()

    print(f"Uploaded file: `{file_to_upload}`")
    print(f"Uploaded file (direct link): https://drive.usercontent.google.com/download?export=download&confirm=t&id=`{file_to_upload['id']}`")

    # add "Anyone with link" permission
    file_to_upload.InsertPermission({
        'type': 'anyone',
        'value': 'anyone',
        'role': 'reader'}
    )


class YouTubeVideo:

    def __init__(self, youtube, id):
        response = youtube.videos().list(part='snippet,contentDetails', id=id).execute()
        snippet = response['items'][0]['snippet']

        self.title = snippet['title']
        self.description = snippet['description']
        self.published = snippet['publishedAt']
        self.thumbnail_url = snippet['thumbnails']['standard']['url']

        self.channel_id = snippet['channelId']
        self.channel_title = snippet['channelTitle']


# authenticate and init services
gauth = auth()
drive = GoogleDrive(gauth)
youtube = discovery.build('youtube', 'v3', credentials=gauth.credentials)

root = get_or_create_folder(drive, ROOT_FOLDER, 'root')
print('root: %s' % root)

video_id = sys.argv[1]
video = YouTubeVideo(youtube=youtube, id=video_id)

print("--- Video Details ---")
print(f"Title: {video.title}")
print(f"Published at: {video.published}")
print(f"Thumbnails: {video.thumbnail_url}")
print(f"Channel Name: {video.channel_title}")
print(f"Channel ID: {video.channel_id}")
print("\n--- Description ---")
print(video.description)

channel_folder = get_or_create_folder(drive, video.channel_id, root['id'])
print('channel_folder: %s' % root)