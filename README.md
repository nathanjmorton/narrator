# Narrator

**Listen to your ebooks.** Narrator reads EPUB, PDF, Word (.docx) and text books aloud in natural-sounding AI voices. It runs entirely on your own Windows PC: no account, no subscription, and nothing is sent anywhere.

- Read-along view highlights each paragraph as it's spoken. Click any paragraph to jump there.
- Remembers your place in every book.
- Buttons for back/forward 15 seconds, previous/next paragraph, previous/next chapter, and speed from 0.5× to 3×.
- 28 voices (American and British, male and female), each with a sample you can play.
- Sleep timer, bookmarks, light and dark themes, volume up to 160%.
- Save any book as an `.m4b` audiobook with chapters, or as MP3s, for your phone or car.
- Drag books into the window or onto the desktop icon to add them.

## Install (Windows 10/11)

1. Download **[Narrator.zip](../../releases/latest/download/Narrator.zip)** and unzip it somewhere permanent, such as `Documents\Narrator`.
2. Double-click **`Setup.bat`**. It installs everything into that folder (about 3 GB, 10–20 minutes) and puts a **Narrator** shortcut on your desktop. If Python isn't installed, it tries to install it for you.
3. Open **Narrator** from the desktop.

An NVIDIA graphics card makes narration much faster, but it isn't required.

## Tips

- Press <kbd>?</kbd> in the app to see the keyboard shortcuts. <kbd>Space</kbd> plays and pauses, the arrow keys skip, and <kbd>[</kbd> <kbd>]</kbd> change speed.
- Books with copy protection (DRM), such as most Kindle purchases, can't be read. Scanned PDFs have no text to read.
- Your library and reading positions are stored in `state.json` inside the app folder.

## Command line

`narrate.bat book.epub` makes an audiobook file without opening the app. Run `narrate.bat --help` to see the options.

## Credits

Voices by [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) (Apache 2.0).
