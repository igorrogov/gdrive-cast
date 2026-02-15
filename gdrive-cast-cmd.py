import argparse
import configparser
import sys

import gdrive_cast_lib
from gdrive_cast_lib import PodcastManager


def list_podcasts(manager: PodcastManager):
    index = 1
    for p in manager.fetch_library_data():
        print(f"\n{index}. Channel: {p['title']}")
        index += 1

        for episode in p['episodes']:
            print(f" - {episode['title']}")

def run_program():
    manager = PodcastManager()

    parser = argparse.ArgumentParser(prog='GDrive Cast ' + gdrive_cast_lib.VERSION, description='Host a podcast on Google Drive')
    parser.add_argument('video_url', nargs='?', default="")
    parser.add_argument("-l", "--list", help="List existing podcast channels and exit.", action="store_true")
    parser.add_argument("-d", "--delete", help="Delete a channel by its index (starts with 1).")
    parser.add_argument("-p", "--purge", help="Purge a channel by index (starts with 1) (delete all episodes but keep the channel).")
    parser.add_argument("-st", "--show-timestamps",
                        help="Generate and print timestamps for a video URL. Can be used for testing before embedding them into a podcast.")
    parser.add_argument("-adt", "--add-generated-timestamps",
                        help="When downloading a new video, generate and insert chapters with timestamps into podcast description. Reqiures an LLM API key.",
                        action="store_true")
    args = parser.parse_args()

    config = configparser.ConfigParser()
    config.read('config.ini')

    if args.list:
        list_podcasts(manager)
        sys.exit(0)

    if args.show_timestamps:
        print(manager.get_timestamps(args.show_timestamps))
        sys.exit(0)

    if args.delete:
        manager.delete_podcast(int(args.delete))
        print(f"Deleted podcast / channel folder: {args.delete}")
        sys.exit(0)

    if args.purge:
        manager.purge_podcast(int(args.purge))
        print(f"Purged podcast / channel folder: {args.delete}")
        sys.exit(0)

    if not args.video_url:
        print("Video URL is required")
        sys.exit(-1)

    manager.download_podcast(args.video_url, args.add_generated_timestamps)


if __name__ == "__main__":
    run_program()
