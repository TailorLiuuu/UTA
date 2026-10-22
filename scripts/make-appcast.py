#!/usr/bin/env python3
"""Run after packaging; publish the feed only after its Release asset is public."""
import argparse
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
import plistlib
import re
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SPARKLE = 'http://www.andymatuschak.org/xml-namespaces/sparkle'
ET.register_namespace('sparkle', SPARKLE)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('tag')
    args = parser.parse_args()
    if not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+(?:-preview\.[0-9]+)?', args.tag):
        parser.error('expected vX.Y.Z or vX.Y.Z-preview.N')
    app = Path((ROOT/'build/verified-app-path.txt').read_text().strip())
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    info = plistlib.loads((app/'Contents/Info.plist').read_bytes())
    archive = ROOT/'dist'/f'UTA-{info["CFBundleShortVersionString"]}-macOS-arm64.dmg'
    signer = ROOT/'build/sparkle/bin/sign_update'
    signature = subprocess.check_output([str(signer), '--account', 'uta-updates', '-p', str(archive)], text=True).strip()
    subprocess.run([str(signer), '--account', 'uta-updates', '--verify', str(archive), signature], check=True)
    rss = ET.Element('rss', version='2.0')
    channel = ET.SubElement(rss, 'channel')
    ET.SubElement(channel, 'title').text = 'UTA macOS updates'
    item = ET.SubElement(channel, 'item')
    ET.SubElement(item, 'title').text = f'UTA {args.tag} · build {info["CFBundleVersion"]}'
    ET.SubElement(item, 'pubDate').text = format_datetime(datetime.now(timezone.utc))
    for key, value in [('version', info['CFBundleVersion']),
                       ('shortVersionString', args.tag.removeprefix('v')),
                       ('minimumSystemVersion', info['LSMinimumSystemVersion'])]:
        ET.SubElement(item, '{'+SPARKLE+'}'+key).text = value
    ET.SubElement(item, 'description').text = '在 UTA 内检查更新、下载、验证并安装。请先断开 USB 并等待清理完成。'
    ET.SubElement(item, 'link').text = 'https://github.com/TailorLiuuu/UTA/releases/tag/'+args.tag
    ET.SubElement(item, 'enclosure', {'url': f'https://github.com/TailorLiuuu/UTA/releases/download/{args.tag}/{archive.name}',
        'length': str(archive.stat().st_size), 'type': 'application/octet-stream',
        '{'+SPARKLE+'}edSignature': signature})
    output = ROOT/'updates/appcast.xml'
    output.parent.mkdir(exist_ok=True)
    ET.indent(rss)
    ET.ElementTree(rss).write(output, encoding='utf-8', xml_declaration=True)
    subprocess.run([str(signer), '--account', 'uta-updates', str(output)], check=True)
    print(output)

if __name__ == '__main__': main()
