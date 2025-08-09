# GDrive Cast

This is a small utility that can help you to automate the process of converting a YouTube video to a podcast hosted on your Google Drive.

## Requirements

* Python >= 3.8.
* You need to have a google account with Google Drive.
* Your Google Drive should have enough space for your podcast audio files.
* You should separately download and put a YouTube converter tool (such as https://github.com/yt-dlp/yt-dlp) to the root folder. Alternatively, you can configure the path to the tool in the `config.ini` file.
* You create and download your Google OAuth2 credentials (see https://developers.google.com/identity/protocols/oauth2). Very good documentation on how to do that is here: https://docs.iterative.ai/PyDrive2/quickstart/#authentication.

## How to build and run

Clone the repo:

```
git clone https://github.com/igorrogov/gdrive-cast
cd gdrive-cast
```

Create a venv and install dependencies:

```
python -m venv .venv
.\.venv\Scripts\activate.bat
pip install -r requirements.txt
```

Create an Google OAuth2 credentials and save it to `credentials.json` in the root folder. See instructions at https://docs.iterative.ai/PyDrive2/quickstart/#authentication. Required APIs:
 * Google Drive API
 * YouTube Data API v3 (used only for loading information about videos and channels, not for downloading).

Make sure you have a YouTube converter installed. The simplest option is to simply copy the `yt-dlp` to the root folder.

Find a video tha you want to convert and use this command:

```
python .\gdrive-cast.py <ID>
```

where <ID> is the YouTube video ID (e.g. https://www.youtube.com/watch?v=ID)

It will open a browser window to authentificate against Google for the first time. On next run it will use the saved credentials.
