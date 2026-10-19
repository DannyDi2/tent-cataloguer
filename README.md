# Tent Cataloguer for Linux

Offline catalog of external and local disks: browse folder structure, sizes
and files without the disk connected. Python 3 + PyQt6 + SQLite.

## Install (Debian/Ubuntu/Linux Mint)

    sudo apt install pipx git libxcb-cursor0
    pipx ensurepath

Close and reopen the terminal, then:

    pipx install git+https://github.com/DannyDi2/tent-cataloguer.git

## Run

    tent-cataloguer

## Update

    pipx upgrade tent-cataloguer

## Uninstall

    pipx uninstall tent-cataloguer

## Features

- Catalog external drives, local folders and shares, then browse folder structure, sizes and files later with the disk disconnected
- Offline search with wildcards (`*` `?` `#` `[abc]`), Normal / All words / Any word modes, and size and date filters
- Descriptions on any item, included in searches
- CSV export
- Save / Save As, and multi-level Undo / Redo (up to 25 steps)
- Update Disk keeps your descriptions; items you delete can be remembered and ignored on future updates
- Unreadable items (for example I/O errors) are skipped and listed at the end of the scan; previously catalogued content of those folders is kept

## Known limitations

- Developed and tested on Linux Mint with Python 3.12. Other distributions and desktop environments are untested.
- "Open file location" tries the D-Bus FileManager1 interface and falls back to opening the folder with Nemo or xdg-open. It was only tested with simulated executables, so whether the file manager selects the file is unconfirmed. It also requires the disk to be mounted at the path recorded when it was scanned.
- Network shares have not been tested by the author.
- Undo keeps full snapshots of the catalog, so very large catalogs may use more memory.
- The .bxd format of Broken X Disk Manager is not supported.

## License

GPL-3.0, see the LICENSE file. PyQt6 is distributed under GPL v3 or a commercial license.
