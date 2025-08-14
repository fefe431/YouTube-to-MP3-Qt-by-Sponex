import argparse
import os
import shutil
import sys
from typing import List, Dict, Any

try:
    from yt_dlp import YoutubeDL
except Exception as import_error:
    print("Failed to import yt-dlp. Please run: pip install -r requirements.txt", file=sys.stderr)
    raise


def find_local_ffmpeg_dir() -> str:
    """Search for a local ffmpeg binary under tools/ffmpeg and return its directory, or '' if not found."""
    base_dir = os.path.join(os.path.dirname(__file__), "tools", "ffmpeg")
    if not os.path.isdir(base_dir):
        return ""
    target_exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    for root, _dirs, files in os.walk(base_dir):
        if target_exe in files:
            return root
    return ""


def ensure_ffmpeg_available() -> None:
    """Exit with a helpful message if ffmpeg is not available on PATH and no local copy was found."""
    if shutil.which("ffmpeg") is not None or find_local_ffmpeg_dir():
        return

    is_windows = os.name == "nt"
    advice_lines: List[str] = [
        "ffmpeg is required to convert audio to MP3.",
        "",
        "Install options:",
    ]
    if is_windows:
        advice_lines.extend(
            [
                "- Windows: run the helper script: .\\install_ffmpeg.ps1",
                "  (This uses winget. If you don't have winget, download ffmpeg from the Gyan.dev builds and add it to PATH.)",
            ]
        )
    else:
        advice_lines.extend(
            [
                "- macOS (Homebrew): brew install ffmpeg",
                "- Linux (Debian/Ubuntu): sudo apt-get update && sudo apt-get install -y ffmpeg",
            ]
        )

    print("\n".join(advice_lines), file=sys.stderr)
    sys.exit(1)


def normalize_bitrate_to_yt_dlp_quality(bitrate: str) -> str:
    """Normalize bitrate input (e.g. '192', '192k', '320') to yt-dlp expected quality string (e.g. '192')."""
    cleaned = bitrate.strip().lower()
    if cleaned.endswith("k"):
        cleaned = cleaned[:-1]
    if not cleaned.isdigit():
        return "192"
    return cleaned


def build_yt_dlp_options(
    output_dir: str,
    audio_format: str,
    bitrate: str,
    embed_thumbnail: bool,
    write_metadata: bool,
    cookies_file: str,
    uploader_filter_str: str = "",
    download_archive_path: str = "",
) -> Dict[str, Any]:
    postprocessors = [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": audio_format,
            "preferredquality": normalize_bitrate_to_yt_dlp_quality(bitrate),
        }
    ]

    if write_metadata:
        postprocessors.append({"key": "FFmpegMetadata"})

    ydl_opts: Dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(output_dir, "%(title)s.%(ext)s"),
        "noplaylist": True,
        "quiet": False,
        "no_warnings": True,
        "ignoreerrors": True,
        "default_search": "ytsearch",
        "overwrites": False,
        "postprocessors": postprocessors,
        "progress_hooks": [progress_hook],
    }

    # Prefer local ffmpeg bundle if available
    local_ffmpeg_dir = find_local_ffmpeg_dir()
    if local_ffmpeg_dir:
        ydl_opts["ffmpeg_location"] = local_ffmpeg_dir

    if embed_thumbnail:
        ydl_opts["writethumbnail"] = True
        postprocessors.append({"key": "FFmpegThumbnailsEmbed"})

    if cookies_file:
        ydl_opts["cookiefile"] = cookies_file
    if download_archive_path:
        ydl_opts["download_archive"] = download_archive_path
    if uploader_filter_str:
        needle = uploader_filter_str.strip().lower()
        def _match_filter(info_dict):
            uploader = (info_dict.get("uploader") or "").lower()
            channel = (info_dict.get("channel") or "").lower()
            artist = (info_dict.get("artist") or "").lower()
            if needle and (needle in uploader or needle in channel or needle in artist):
                return None
            return f"skip: uploader/channel does not include '{uploader_filter_str}'"
        ydl_opts["match_filter"] = _match_filter

    return ydl_opts


def progress_hook(status: Dict[str, Any]) -> None:
    if status.get("status") == "downloading":
        percent = status.get("_percent_str", "?").strip()
        speed = status.get("_speed_str", "?").strip()
        eta = status.get("_eta_str", "?").strip()
        filename = status.get("filename", "")
        print(f"Downloading: {percent} | {speed} | ETA {eta} -> {os.path.basename(filename)}", end="\r", flush=True)
    elif status.get("status") == "finished":
        filename = status.get("filename", "")
        print(f"\nDownloaded: {os.path.basename(filename)}. Converting...", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download and convert YouTube audio to MP3 using yt-dlp and ffmpeg.",
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help=(
            "One or more YouTube URLs or playlist URLs. You can also use yt-dlp search expressions, e.g. 'ytsearch1:your song name'."
        ),
    )
    parser.add_argument(
        "--output",
        default=os.path.join(os.getcwd(), "downloads"),
        help="Output directory (will be created if it does not exist).",
    )
    parser.add_argument(
        "--audio-format",
        default="mp3",
        choices=["mp3", "m4a", "flac", "wav", "opus", "aac", "vorbis"],
        help="Audio format to convert to (default: mp3).",
    )
    parser.add_argument(
        "--bitrate",
        default="192",
        help="Target audio bitrate in kbps (e.g. 128, 192, 256, 320). 'k' suffix optional.",
    )
    parser.add_argument(
        "--embed-thumbnail",
        action="store_true",
        help="Embed the video thumbnail into the audio file (requires ffmpeg).",
    )
    parser.add_argument(
        "--no-metadata",
        action="store_true",
        help="Do not write metadata tags via ffmpeg.",
    )
    parser.add_argument(
        "--cookies",
        default="",
        help="Path to a cookies file for age-restricted or region-locked videos (optional).",
    )
    parser.add_argument(
        "--only-artist",
        default="",
        help="Only download items where uploader/channel/artist contains this text (case-insensitive).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Ensure destination dir exists
    os.makedirs(args.output, exist_ok=True)

    # Check ffmpeg since audio extraction depends on it
    ensure_ffmpeg_available()

    ydl_opts = build_yt_dlp_options(
        output_dir=args.output,
        audio_format=args.audio_format,
        bitrate=args.bitrate,
        embed_thumbnail=args.embed_thumbnail,
        write_metadata=not args.no_metadata,
        cookies_file=args.cookies,
        uploader_filter_str=args.only_artist,
        download_archive_path=os.path.join(args.output, "downloaded.txt"),
    )

    inputs: List[str] = list(args.inputs)

    try:
        with YoutubeDL(ydl_opts) as ydl:
            return_code = ydl.download(inputs)
    except KeyboardInterrupt:
        print("\nAborted by user.")
        sys.exit(130)
    except Exception as exc:  # yt-dlp already prints rich errors; exit with failure
        print(f"\nError: {exc}", file=sys.stderr)
        sys.exit(1)

    if return_code != 0:
        print("\nSome downloads failed.", file=sys.stderr)
        sys.exit(return_code)

    print("\nAll done! Files saved to:", args.output)


if __name__ == "__main__":
    main()


