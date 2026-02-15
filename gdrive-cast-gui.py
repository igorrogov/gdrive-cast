from nicegui import app, ui, run

from gdrive_cast_lib import PodcastManager

mgr = None

@ui.refreshable
async def render_podcast_list():

    with ui.column().classes('w-full items-center') as loader:
        ui.spinner(size='lg')
        ui.label('Fetching data from GDrive...')

    try:
        data = await run.io_bound(mgr.fetch_library_data)

        loader.set_visibility(False)  # Hide spinner

        if not data:
            ui.label('No podcasts found.')
            return

        for pod in data:
            with ui.expansion(pod['title'], icon='mic').classes('w-full border rounded mb-2'):
                for ep in pod['episodes']:
                    with ui.row().classes('w-full items-center justify-between p-2 border-t'):
                        ui.label(ep['title']).classes('font-medium')
                        ui.label(ep['date']).classes('text-xs text-gray-500')
    except Exception as e:
        loader.set_visibility(False)
        ui.label(f'Error: {e}').classes('text-red')


@ui.page('/')
async def index():
    with ui.column().classes('w-full max-w-3xl mx-auto p-4'):
        ui.label('GDrive Cast GUI').classes('text-h4 mb-4')
        ui.timer(0.1, render_podcast_list, once=True)


@app.on_startup
def startup():
    global mgr
    print("Initializing Podcast Manager...")
    mgr = PodcastManager()


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title="GDrive Cast", native=True, reload=False)