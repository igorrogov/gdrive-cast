call .\.venv\Scripts\activate.bat
pyinstaller -y --clean --collect-all tiktoken --collect-all tiktoken_ext --hidden-import tiktoken_ext.openai_public --collect-all litellm .\gdrive-cast.py