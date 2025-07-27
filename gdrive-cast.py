from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive
from pydrive2.files import GoogleDriveFile

ROOT_FOLDER = "gdrive-cast"
FOLDER_TYPE = "application/vnd.google-apps.folder"


def auth() -> GoogleAuth:
    gauth = GoogleAuth()

    gauth.LoadCredentialsFile("mycreds.txt")
    if gauth.credentials is None:
        gauth.LocalWebserverAuth()
    elif gauth.access_token_expired:
        gauth.Refresh()
    else:
        gauth.Authorize()
    gauth.SaveCredentialsFile("mycreds.txt")

    return gauth


def get_root_folder(drive) -> GoogleDriveFile:
    roots = drive.ListFile({
        'q': f"title='{ROOT_FOLDER}' and 'root' in parents and trashed=false and mimeType='{FOLDER_TYPE}'"
    }).GetList()

    if roots:
        return roots[0]

    # If the list is empty, the folder doesn't exist.
    print(f"Folder '{ROOT_FOLDER}' not found. Creating a new one...")
    folder_metadata = {
        'title': ROOT_FOLDER,
        'mimeType': FOLDER_TYPE
    }
    folder = drive.CreateFile(folder_metadata)
    folder.Upload()
    print(f"Folder '{folder['title']}' created with ID: {folder['id']}")
    return folder


drive = GoogleDrive(auth())
root = get_root_folder(drive)
print('root: %s' % root)

# upload file
file_to_upload = drive.CreateFile({'parents': [{'id': root['id']}]})
file_to_upload.SetContentFile('sample.mp3')
file_to_upload.Upload()
print(f"Uploaded file: `{file_to_upload}`")

# add "Anyone with link" permission
file_to_upload.InsertPermission({
    'type': 'anyone',
    'value': 'anyone',
    'role': 'reader'}
)
print(f"Uploaded file (direct link): https://drive.usercontent.google.com/download?export=download&confirm=t&id=`{file_to_upload['id']}`")