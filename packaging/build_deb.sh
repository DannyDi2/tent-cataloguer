#!/bin/bash
# Builds tent-cataloguer_<version>_all.deb next to the app (in ../dist).
# Needs: dpkg-deb, python3-pyqt6 (only to draw the icon).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$(dirname "$HERE")"
VERSION="$(sed -n 's/^APP_VERSION = "\(.*\)"/\1/p' "$APP/dcat_gui.py")"
PKG=tent-cataloguer
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
R="$WORK/$PKG"
mkdir -p "$R/DEBIAN" "$R/usr/lib/$PKG" "$R/usr/bin" "$R/usr/share/applications" \
         "$R/usr/share/icons/hicolor" "$R/usr/share/mime/packages" "$R/usr/share/doc/$PKG"
install -m 644 "$APP"/dcat_core.py "$APP"/dcat_gui.py "$APP"/icons.py "$R/usr/lib/$PKG/"

cat > "$R/usr/bin/$PKG" <<'SH'
#!/bin/sh
export PYTHONDONTWRITEBYTECODE=1
exec python3 /usr/lib/tent-cataloguer/dcat_gui.py "$@"
SH
cat > "$R/usr/bin/$PKG-cli" <<'SH'
#!/bin/sh
export PYTHONDONTWRITEBYTECODE=1
exec python3 /usr/lib/tent-cataloguer/dcat_core.py "$@"
SH
chmod 755 "$R/usr/bin/$PKG" "$R/usr/bin/$PKG-cli"

cat > "$R/usr/share/applications/$PKG.desktop" <<'DT'
[Desktop Entry]
Type=Application
Name=Tent Cataloguer
GenericName=Disk Catalog
Comment=Offline catalog of your disks: find any file, even on disks that are not connected
Exec=tent-cataloguer %f
Icon=tent-cataloguer
Terminal=false
Categories=Utility;FileTools;
MimeType=application/x-tent-catalog;
Keywords=disk;catalog;search;files;offline;
StartupWMClass=tent-cataloguer
DT

cat > "$R/usr/share/mime/packages/$PKG.xml" <<'MX'
<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="application/x-tent-catalog">
    <comment>Disk catalog</comment>
    <sub-class-of type="application/vnd.sqlite3"/>
    <glob pattern="*.dcat" weight="80"/>
    <icon name="tent-cataloguer"/>
  </mime-type>
</mime-info>
MX

python3 "$HERE/make_icon.py" "$R/usr/share/icons/hicolor" >/dev/null

cat > "$R/usr/share/doc/$PKG/README" <<'RD'
Tent Cataloguer for Linux - offline catalog of external and local disks.
Author: Danny - License: GPL-3.0 - https://github.com/DannyDi2/tent-cataloguer
Start it from the menu, or run: tent-cataloguer [catalog.dcat]
Command line: tent-cataloguer-cli (scan, update, search, list, stats, dups, export-csv)
Optional helpers: python3-mutagen (music tags), python3-pil (photo EXIF),
ffmpeg (video length), 7-Zip or PeaZip (rar/7z/iso contents).
RD

find "$R" -type d -exec chmod 755 {} +
find "$R/usr/lib" "$R/usr/share" -type f -exec chmod 644 {} +
cat > "$R/usr/share/doc/$PKG/copyright" <<'CP'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: tent-cataloguer
Upstream-Contact: Danny <https://github.com/DannyDi2>
Source: https://github.com/DannyDi2/tent-cataloguer

Files: *
Copyright: 2026 Danny
License: GPL-3
 This program is free software: you can redistribute it and/or modify
 it under the terms of the GNU General Public License version 3 as
 published by the Free Software Foundation.
 .
 This program is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 .
 On Debian systems, the full text of the GNU General Public License
 version 3 can be found in the file /usr/share/common-licenses/GPL-3.
CP
SIZE=$(du -sk "$R/usr" | cut -f1)
cat > "$R/DEBIAN/control" <<CT
Package: $PKG
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Installed-Size: $SIZE
Depends: python3 (>= 3.10), python3-pyqt6, util-linux
Recommends: python3-mutagen, python3-pil
Suggests: ffmpeg, 7zip
Maintainer: Danny <249636954+DannyDi2@users.noreply.github.com>
Homepage: https://github.com/DannyDi2/tent-cataloguer
Description: Offline catalog of external and local disks
 Reads disks and folders into a single catalog file (.dcat, SQLite) so you
 can browse and search them even when they are not connected.
 Instant search (Ctrl+F) with one background color per disk, filters by
 file type, statistics, probable duplicates across disks, archive contents
 (zip, rar, 7z, tar, iso) and media info (music tags, photo EXIF, video length).
CT

mkdir -p "$APP/dist"
OUT="$APP/dist/${PKG}_${VERSION}_all.deb"
dpkg-deb --root-owner-group --build "$R" "$OUT" >/dev/null
echo "$OUT"
