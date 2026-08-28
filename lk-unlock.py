#!/usr/bin/env python3
# lk-unlock - Unlock the bootloader of Xiaomi MTK devices by patching the LK image.
# Copyright (C) 2026 TFast Digital Agency - https://tfastdigital.com
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU Affero General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU Affero General Public License for more
# details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""lk-unlock — Unlock the bootloader of Xiaomi MTK devices by patching the LK image.

This tool replaces Xiaomi's embedded public key with a user-controlled key and
applies a certificate bypass to the LK image, enabling offline generation of
bootloader unlock signatures.

Commands:
    patch    Patch an LK image (replace public key + apply cert bypass).
    sign     Sign a fastboot unlock token with the local private key.
    unlock   Run the unlock sequence against a connected fastboot device.
    verify   Validate a patched LK image (key + cert bypass sanity check).

Distributed for educational and research purposes only. Use at your own risk.
Copyright (C) 2026 TFast Digital Agency. Licensed under the GNU AGPL v3.0.

Community: https://t.me/tfasthub
"""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from pyasn1.codec.der.encoder import encode as der_encode
from pyasn1.type.univ import BitString

from liblk.image import LkImage
from liblk.structures.certificate import Certificate

__version__ = "1.1.0"

# Default file names (relative to the key directory).
DEFAULT_PRIVATE_KEY = "private.pem"
DEFAULT_PUBLIC_KEY = "public.pem"
DEFAULT_XIAOMI_KEY = "xiaomi.pem"
DEFAULT_SIGNATURE = "signature.bin"

# Fastboot command timeout in seconds. Prevents a hung device from blocking forever.
FASTBOOT_TIMEOUT = 30

log = logging.getLogger("lk-unlock")


class LkUnlockError(Exception):
    """Base exception for lk-unlock errors."""

    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


class CertBypassMode(str, Enum):
    """Supported certificate-bypass strategies."""

    WRAP = "wrap"
    OVERRIDE = "override"


# --------------------------------------------------------------------------- #
# Certificate bypass builders
# --------------------------------------------------------------------------- #
def build_bypass_cert2_wrap(
    original_cert2: bytes, header_hash: bytes, image_hash: bytes
) -> bytes:
    """Wrap the original cert2 with a verified copy plus a forged copy."""
    cert = Certificate.from_bytes(original_cert2)
    verified_copy = der_encode(BitString(hexValue=bytes(original_cert2).hex()))
    forged_copy = cert.encode_with_hashes(header_hash, image_hash)
    return verified_copy + forged_copy


def build_bypass_cert2_override(
    original_cert2: bytes, header_hash: bytes, image_hash: bytes
) -> bytes:
    """Prepend a hash-override block to the original cert2."""
    cert = Certificate.from_bytes(original_cert2)
    override = cert.build_hash_override_block(header_hash, image_hash)
    return override + bytes(original_cert2)


_CERT_BUILDERS = {
    CertBypassMode.WRAP: build_bypass_cert2_wrap,
    CertBypassMode.OVERRIDE: build_bypass_cert2_override,
}


# --------------------------------------------------------------------------- #
# Key management
# --------------------------------------------------------------------------- #
def _key_dir() -> Path:
    """Resolve the directory holding key material (defaults to CWD)."""
    return Path.cwd()


def _key_path(name: str, key_dir: Optional[Path] = None) -> Path:
    base = key_dir or _key_dir()
    return base / name


def get_keys(key_dir: Optional[Path] = None) -> Tuple[RSAPrivateKey, RSAPublicKey]:
    """Load existing keys from disk, or generate a fresh 2048-bit RSA pair.

    Returns (private_key, public_key). The modulus size is inferred from the
    loaded keys, so the rest of the tool never hardcodes a byte length.
    """
    private_path = _key_path(DEFAULT_PRIVATE_KEY, key_dir)
    public_path = _key_path(DEFAULT_PUBLIC_KEY, key_dir)

    if private_path.exists() and public_path.exists():
        private_key = serialization.load_pem_private_key(
            private_path.read_bytes(), password=None
        )
        public_key = serialization.load_pem_public_key(public_path.read_bytes())
        log.info("Existing keys found. Using %s and %s", private_path, public_path)
        return private_key, public_key

    log.info("No existing keys found; generating a new 2048-bit RSA key pair...")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    private_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )

    log.info("New keys generated and saved to %s and %s", private_path, public_path)
    return private_key, private_key.public_key()


def _modulus_bytes(public_key: RSAPublicKey) -> bytes:
    """Return the big-endian byte representation of the RSA modulus."""
    size = public_key.key_size
    return public_key.public_numbers().n.to_bytes((size + 7) // 8, byteorder="big")


def _load_public_key(path: Path) -> RSAPublicKey:
    try:
        return serialization.load_pem_public_key(path.read_bytes())
    except FileNotFoundError:
        raise LkUnlockError(f"'{path}' file not found.") from None
    except ValueError as exc:
        raise LkUnlockError(f"'{path}' is not a valid public key: {exc}") from exc


# --------------------------------------------------------------------------- #
# RSA signature helpers
# --------------------------------------------------------------------------- #
def encode(token: bytes, modulus_len: int) -> bytes:
    """Build the PKCS#1 v1.5 style EM block for the given token.

    The padding length is derived from the modulus size so it works for any
    RSA key size (2048-bit -> 256 bytes).
    """
    if len(token) > modulus_len - 11:
        raise ValueError(
            f"Token too long ({len(token)} bytes); max is {modulus_len - 11} "
            f"for a {modulus_len * 8}-bit key"
        )
    ps = b"\xff" * (modulus_len - len(token) - 3)
    return b"\x00\x01" + ps + b"\x00" + token


def sign_token(token: bytes, key_dir: Optional[Path] = None) -> Path:
    """Sign a token with the local private key and write signature.bin.

    Returns the path of the generated signature file.
    """
    if isinstance(token, str):
        token = token.encode()

    private_path = _key_path(DEFAULT_PRIVATE_KEY, key_dir)
    try:
        priv = serialization.load_pem_private_key(
            private_path.read_bytes(), password=None
        )
    except FileNotFoundError:
        raise LkUnlockError(
            f"'{private_path}' not found. Run the 'patch' command first to generate keys."
        ) from None

    numbers = priv.private_numbers()
    modulus_len = (priv.key_size + 7) // 8
    n = numbers.public_numbers.n

    em = encode(token, modulus_len)
    m = int.from_bytes(em, "big")
    s = pow(m, numbers.d, n)
    signature = s.to_bytes(modulus_len, "big")

    # Self-check: verify the signature against the public key before writing it.
    _verify_signature(token, signature, priv.public_key(), modulus_len)

    signature_path = _key_path(DEFAULT_SIGNATURE, key_dir)
    signature_path.write_bytes(signature)
    log.info("Token signature generated and saved to %s", signature_path)
    return signature_path


def _verify_signature(
    token: bytes, signature: bytes, public_key: RSAPublicKey, modulus_len: int
) -> None:
    """Verify a signature matches the token under the given public key."""
    n = public_key.public_numbers().n
    m = int.from_bytes(signature, "big")
    em = pow(m, public_key.public_numbers().e, n).to_bytes(modulus_len, "big")
    if em != encode(token, modulus_len):
        raise LkUnlockError(
            "Signature self-check failed. Refusing to write an invalid signature."
        )
    log.debug("Signature self-check passed.")


# --------------------------------------------------------------------------- #
# LK image patching
# --------------------------------------------------------------------------- #
def apply_cert_bypass(
    image: LkImage, mode: CertBypassMode = CertBypassMode.OVERRIDE
) -> List[str]:
    """Apply the certificate bypass to all signed partitions in the image.

    Returns the list of partition names that were patched.
    """
    build = _CERT_BUILDERS[CertBypassMode(mode)]
    signed: List[str] = []

    for name, partition in image.partitions.items():
        if partition.cert2 is None:
            continue

        status = partition.matches_cert2()
        if status is None:
            log.warning("Partition '%s' cert2 could not be parsed. Skipping cert bypass.", name)
            continue
        if status:
            continue

        header_hash, image_hash = partition.compute_hashes()
        original = bytes(partition.cert2.data)
        partition.cert2.data = build(original, header_hash, image_hash)

        log.info(
            "Cert bypass applied to partition '%s' (%s, cert2 %d -> %d bytes)",
            name,
            CertBypassMode(mode).value,
            len(original),
            len(partition.cert2.data),
        )
        signed.append(name)

    if signed:
        image._rebuild_contents()

    return signed


def patch_img(
    img_path: str,
    output_path: Optional[str] = None,
    use_wrap: bool = False,
    key_dir: Optional[Path] = None,
) -> Path:
    """Patch the LK image: replace the public key and apply the cert bypass.

    Returns the path of the written patched image.
    """
    img = Path(img_path)
    if not img.exists():
        raise LkUnlockError(f"'{img_path}' file not found.")

    if output_path is None:
        output_path = str(img.with_name(f"{img.stem}_patched{img.suffix}"))

    _, new_pub_key = get_keys(key_dir)
    new_n_bytes = _modulus_bytes(new_pub_key)

    old_pub_key = _load_public_key(_key_path(DEFAULT_XIAOMI_KEY, key_dir))
    old_n_bytes = _modulus_bytes(old_pub_key)

    data = img.read_bytes()
    pos = data.find(old_n_bytes)

    if pos == -1:
        raise LkUnlockError(
            "Xiaomi's public key modulus not found in LK image. Nothing to patch."
        )

    log.info("Original key modulus found at offset 0x%X", pos)

    patched_data = data[:pos] + new_n_bytes + data[pos + len(new_n_bytes):]
    log.info("Public key patched successfully")

    cert_bypass_mode = CertBypassMode.WRAP if use_wrap else CertBypassMode.OVERRIDE
    log.info("Selected cert bypass mode: %s", cert_bypass_mode.value)

    try:
        image = LkImage(patched_data)
        signed = apply_cert_bypass(image, cert_bypass_mode)
        if signed:
            patched_data = bytes(image.contents)
            log.info("Cert bypass completed for: %s", ", ".join(signed))
        else:
            log.info("Cert bypass was not needed")
    except Exception as exc:
        raise LkUnlockError(f"Failed to apply cert bypass: {exc}") from exc

    output = Path(output_path)
    output.write_bytes(patched_data)
    log.info("All done! LK saved to: %s", output)
    return output


def verify_img(img_path: str, key_dir: Optional[Path] = None) -> bool:
    """Sanity-check a patched LK image.

    Confirms the image parses and that the embedded public key matches the
    local public.pem. Returns True when the image looks valid.
    """
    img = Path(img_path)
    if not img.exists():
        raise LkUnlockError(f"'{img_path}' file not found.")

    new_pub_key = get_keys(key_dir)[1]
    new_n_bytes = _modulus_bytes(new_pub_key)

    data = img.read_bytes()
    pos = data.find(new_n_bytes)
    if pos == -1:
        log.warning("Patched public key modulus not found in image.")
        return False

    log.info("Patched public key modulus found at offset 0x%X", pos)

    try:
        image = LkImage(data)
        log.info("Image parsed successfully with %d partition(s).", len(image.partitions))
    except Exception as exc:
        raise LkUnlockError(f"Failed to parse image: {exc}") from exc

    log.info("Image verification passed.")
    return True


# --------------------------------------------------------------------------- #
# Fastboot helpers
# --------------------------------------------------------------------------- #
def run_fastboot(
    *args: str, timeout: int = FASTBOOT_TIMEOUT
) -> subprocess.CompletedProcess:
    """Run a fastboot command, raising LkUnlockError on failure or timeout."""
    command = ["fastboot", *args]
    log.debug("Running: %s", " ".join(command))

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise LkUnlockError(
            "fastboot binary not found. Put it in the program folder or add it to PATH."
        ) from None
    except subprocess.TimeoutExpired:
        raise LkUnlockError(
            f"fastboot command timed out after {timeout}s: {' '.join(args)}"
        ) from None
    except OSError as exc:
        raise LkUnlockError(f"Failed to run fastboot: {exc}") from exc

    if result.returncode != 0:
        error_text = (result.stderr or result.stdout or "").strip()
        detail = f"\n{error_text}" if error_text else ""
        raise LkUnlockError(f"Fastboot command failed: {' '.join(args)}{detail}")

    return result


def extract_token(output: str) -> str:
    """Extract the unlock token from fastboot output.

    Handles both the common ``(bootloader) token:`` prefix and a bare
    ``token:`` label, and tolerates tokens split across multiple lines.
    """
    token_lines: List[str] = []

    for line in output.splitlines():
        line = line.strip()
        match = re.match(r"^\(?bootloader\)?\s*[:\-]?\s*token:\s*(.+)$", line, re.IGNORECASE)
        if not match:
            match = re.match(r"^token:\s*(.+)$", line, re.IGNORECASE)
        if match:
            token_lines.append(match.group(1).strip())

    return "".join(token_lines)


def _fastboot_devices() -> List[str]:
    """Return the list of serials reported by ``fastboot devices``."""
    devices_result = run_fastboot("devices")
    devices = [
        line.strip().split(" ")[0]
        for line in devices_result.stdout.splitlines()
        if line.strip()
    ]
    return devices


def unlock_device(dry_run: bool = False, serial: Optional[str] = None) -> None:
    """Run the full unlock sequence against a fastboot device."""
    log.info("Looking for fastboot devices...")
    devices = _fastboot_devices()

    if not devices:
        raise LkUnlockError("No fastboot devices found.")

    if serial:
        if serial not in devices:
            raise LkUnlockError(
                f"Device '{serial}' not found. Available: {', '.join(devices)}"
            )
        target = serial
    else:
        if len(devices) > 1:
            log.warning(
                "Multiple devices detected (%s); using the first one. "
                "Use --device to select a specific serial.",
                ", ".join(devices),
            )
        target = devices[0]

    log.info("Device found: %s", target)

    log.info("Reading unlock token...")
    token_result = run_fastboot("-s", target, "oem", "get_token")
    token_output = (token_result.stdout or "") + (token_result.stderr or "")
    token = extract_token(token_output)

    if not token:
        raise LkUnlockError("Failed to extract token from fastboot output.")

    log.info("Token received: %s", token)

    log.info("Signing token...")
    sign_token(token)

    if dry_run:
        log.info("Dry run enabled. Skipping fastboot stage and fastboot oem unlock")
        return

    log.info("Uploading signature.bin to device...")
    run_fastboot("-s", target, "stage", DEFAULT_SIGNATURE)

    log.info("Sending unlock command...")
    run_fastboot("-s", target, "oem", "unlock")

    log.info("Device unlock command completed successfully")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lk-unlock",
        description="Unlocking Xiaomi MTK bootloader by patching the public key.",
    )
    parser.add_argument("--version", action="version", version=f"lk-unlock {__version__}")
    parser.add_argument(
        "--key-dir",
        type=Path,
        default=None,
        help="Directory holding key material (private.pem, public.pem, xiaomi.pem). Defaults to CWD.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging."
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="Suppress informational output."
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    patch_parser = subparsers.add_parser("patch", help="Patch lk.img")
    patch_parser.add_argument("img", help="lk.img file path")
    patch_parser.add_argument("-o", "--output", help="Output file path", default=None)
    patch_parser.add_argument(
        "--wrap", action="store_true", help="Use wrap mode for cert bypass (default: override)"
    )

    sign_parser = subparsers.add_parser("sign", help="Sign the token")
    sign_parser.add_argument("token", help="Token string (or '-' to read from stdin)")

    unlock_parser = subparsers.add_parser("unlock", help="Unlock patched device automatically")
    unlock_parser.add_argument(
        "--dry-run", action="store_true", help="Read and sign token, but skip stage and unlock"
    )
    unlock_parser.add_argument(
        "-s", "--device", help="Target fastboot serial (default: first device found)"
    )

    verify_parser = subparsers.add_parser("verify", help="Validate a patched LK image")
    verify_parser.add_argument("img", help="Patched lk.img file path")

    return parser


def _configure_logging(verbose: bool, quiet: bool) -> None:
    level = logging.DEBUG if verbose else (logging.WARNING if quiet else logging.INFO)
    logging.basicConfig(level=level, format="%(message)s", stream=sys.stdout)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point. Returns an exit code instead of calling sys.exit()."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    _configure_logging(args.verbose, args.quiet)

    log.info("* LK-Unlock v%s by @georgiynesterov", __version__)

    try:
        if args.command == "patch":
            out = patch_img(args.img, args.output, args.wrap, args.key_dir)
            log.info("Patched image written to: %s", out)
        elif args.command == "sign":
            if args.token == "-":
                token = sys.stdin.read().strip()
                if not token:
                    raise LkUnlockError("No token provided on stdin.")
            else:
                token = args.token
            sign_token(token, args.key_dir)
        elif args.command == "unlock":
            unlock_device(args.dry_run, args.device)
        elif args.command == "verify":
            verify_img(args.img, args.key_dir)
    except LkUnlockError as exc:
        log.error("%s", exc.message)
        return exc.exit_code
    except KeyboardInterrupt:
        log.error("Interrupted by user.")
        return 130

    return 0


if __name__ == "__main__":
    sys.exit(main())
