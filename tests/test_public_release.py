import re
import struct
import unittest
from pathlib import Path
from urllib.parse import urlsplit


REPO_ROOT = Path(__file__).resolve().parents[1]
TEXT_NAMES = {"LICENSE", ".gitignore"}
TEXT_SUFFIXES = {".md", ".py", ".yaml", ".yml", ".json", ".txt", ".toml"}
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,})", re.I)
URL_RE = re.compile(r"https?://[^\s)>`]+", re.I)
PUBLIC_DOC_HOSTS = {
    "agentskills.io",
    "developers.google.com",
    "docs.github.com",
    "github.com",
    "learn.chatgpt.com",
    "support.google.com",
}


def public_text_files():
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if ".git" in path.parts or "__pycache__" in path.parts:
            continue
        if path.name in TEXT_NAMES or path.suffix.lower() in TEXT_SUFFIXES:
            yield path


class PublicReleasePrivacyTest(unittest.TestCase):
    def test_no_local_home_paths(self):
        forbidden = (
            "/" + "Users" + "/",
            "C:" + "\\Users\\",
        )
        for path in public_text_files():
            content = path.read_text(encoding="utf-8")
            for marker in forbidden:
                self.assertNotIn(marker.lower(), content.lower(), f"Private marker in {path}: {marker}")

    def test_literal_email_addresses_use_reserved_example_domains(self):
        for path in public_text_files():
            content = path.read_text(encoding="utf-8")
            for match in EMAIL_RE.finditer(content):
                domain = match.group(1).lower()
                self.assertTrue(
                    domain == "example.com"
                    or domain.endswith(".example.com")
                    or domain == "example.org"
                    or domain.endswith(".example.org")
                    or domain == "example.net"
                    or domain.endswith(".example.net"),
                    f"Non-example email domain in {path}: {domain}",
                )

    def test_documentation_urls_are_public_docs_or_reserved_examples(self):
        for path in public_text_files():
            if path.suffix.lower() != ".md":
                continue
            content = path.read_text(encoding="utf-8")
            for match in URL_RE.finditer(content):
                host = (urlsplit(match.group(0)).hostname or "").lower()
                is_reserved_example = host in {"example.com", "example.org", "example.net"} or host.endswith(
                    (".example.com", ".example.org", ".example.net")
                )
                self.assertTrue(
                    host in PUBLIC_DOC_HOSTS or is_reserved_example,
                    f"Unexpected documentation host in {path}: {host}",
                )

    def test_png_assets_have_no_embedded_text_or_exif_metadata(self):
        forbidden_chunks = {b"tEXt", b"zTXt", b"iTXt", b"eXIf"}
        for path in REPO_ROOT.rglob("*.png"):
            data = path.read_bytes()
            self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"), f"Invalid PNG signature: {path}")
            offset = 8
            while offset + 12 <= len(data):
                length = struct.unpack(">I", data[offset : offset + 4])[0]
                chunk_type = data[offset + 4 : offset + 8]
                self.assertNotIn(chunk_type, forbidden_chunks, f"Embedded metadata in {path}: {chunk_type!r}")
                offset += 12 + length
                if chunk_type == b"IEND":
                    break


if __name__ == "__main__":
    unittest.main()
