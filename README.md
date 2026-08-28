
# lk-unlock — Xiaomi MTK Bootloader Unlock Tool

**lk-unlock** is a lightweight Python tool for unlocking the bootloader of Xiaomi devices that run MediaTek (MTK) SoCs. It does this by patching the little kernel (LK) image directly — replacing Xiaomi's embedded public key with one you control and applying a certificate bypass so the device accepts signatures you generate offline.

> **⚠️ WARNING:** This method is risky and can permanently brick your device. Only use it if you know how to recover your device and you accept the consequences. You are responsible for your own hardware.

---

## About this Solution

Built and maintained by **TFast Digital Agency**.

- Website: [https://tfastdigital.com](https://tfastdigital.com)
- GitHub: [tfastdigital/lk-unlock-Xiaomi-devices](https://github.com/tfastdigital/lk-unlock-Xiaomi-devices)
- Telegram community: [@tfasthub](https://t.me/tfasthub)

This project is shared for **educational and research purposes**. It is not intended for anything else, and the authors take no responsibility for how it is used.

---

## How it Works

Xiaomi guards bootloader unlock behind RSA signatures. When you ask to unlock, the device hands you a one-time token, the Xiaomi server signs that token with its private key, and the LK bootloader checks the returned signature against the public key baked into the image.

This tool takes advantage of a weakness in MTK LK images that lets you patch the bootloader and skip certificate verification. By swapping Xiaomi's public key for your own and applying a cert bypass, the bootloader will happily accept signatures you produce locally with the matching private key.

The whole flow is offline once the LK image is patched — no Xiaomi account, no waiting for unlock permission, no Mi account binding.

---

## Requirements

- Python 3.8 or newer
- `fastboot` available in `PATH` or in the current working directory
- Python dependencies (install from `requirements.txt`):

```bash
pip install -r requirements.txt
```

That pulls in `cryptography`, `pyasn1`, and the `liblk` image-parsing library.

---

## Installation

```bash
git clone https://github.com/tfastdigital/lk-unlock-Xiaomi-devices.git
cd lk-unlock-Xiaomi-devices
pip install -r requirements.txt
```

Make sure `fastboot` is installed and reachable from your terminal. On Windows, drop `fastboot.exe` next to the script or add it to `PATH`.

---

## Usage

The script has four commands:

| Command | What it does |
| ------- | ------------ |
| `patch` | Patch an LK image (replace the public key + apply cert bypass). |
| `sign` | Sign a fastboot unlock token with your local private key. |
| `unlock` | Run the whole unlock sequence against a connected device. |
| `verify` | Check that a patched LK image is still valid. |

### Global options

| Option | Description |
| ------ | ----------- |
| `--key-dir <dir>` | Folder holding your key files (`private.pem`, `public.pem`, `xiaomi.pem`). Defaults to the current directory. |
| `-v`, `--verbose` | Turn on debug logging. |
| `-q`, `--quiet` | Only show warnings and errors. |
| `--version` | Print the version and exit. |

### 1. Patch the LK image

Grab your device's `lk.img` first — from a firmware dump or by reading it in BROM mode:

```bash
python lk-unlock.py patch lk.img -o lk_patched.img
```

Options:
- `-o`, `--output <file>` — where to write the result (default: `<img>_patched.<ext>`)
- `--wrap` — use wrap mode for the cert bypass (the default is `override`)

What it does:
- Generates `private.pem` and `public.pem` if they don't exist yet
- Replaces Xiaomi's public key modulus inside `lk.img`
- Applies the cert bypass to any signed partitions
- Writes the patched image to `lk_patched.img`

### 2. Verify a patched image

After patching, confirm everything is still sound:

```bash
python lk-unlock.py verify lk_patched.img
```

This reports whether your public key modulus is present and whether the image parses as a valid LK image.

### 3. Flash the patched LK image

Pick one of these methods to push the patched image onto the device:

- **MTKClient** (where supported):
  ```bash
  python mtk.py r lk_a,lk_b lk_a_backup.img,lk_b_backup.img
  python mtk.py w lk_a,lk_b lk_patched.img,lk_patched.img
  ```
- Xiaomi BROM auth service
- A temporary root exploit (Ghostlock and similar)
- A UFS programmer or hardware tool

### 4. Unlock the device

Once the patched LK is flashed and the device is back in fastboot:

```bash
python lk-unlock.py unlock
```

This will:
- Detect the fastboot device
- Ask it for an unlock token
- Sign the token with `private.pem`
- Upload `signature.bin`
- Send the unlock command

Options:
- `--dry-run` — sign the token but skip staging and the actual unlock
- `-s`, `--device <serial>` — pick a specific device when several are connected

### 5. Sign a token manually

If you prefer to drive fastboot yourself:

```bash
fastboot oem get_token
python lk-unlock.py sign "TOKEN"
fastboot stage signature.bin
fastboot oem unlock
```

You can also pipe a token in through stdin:

```bash
echo "TOKEN" | python lk-unlock.py sign -
```

---

## Using lk-unlock from another tool

The script is easy to drop into a bigger pipeline. It is a single file with a clean exit code, so you can call it from a shell script, a Python app, or a GUI wrapper.

### Call it as a subprocess

Every command returns `0` on success and a non-zero code on failure, so you can chain it:

```bash
python lk-unlock.py patch lk.img -o lk_patched.img || echo "patch failed"
python lk-unlock.py verify lk_patched.img
python lk-unlock.py unlock --device SERIAL
```

### Import it as a module

The functions are importable if you want to build a wrapper:

```python
from pathlib import Path
from lk_unlock import patch_img, sign_token, unlock_device, verify_img

# Patch an image and keep the keys in a custom folder
patch_img("lk.img", "lk_patched.img", use_wrap=False, key_dir=Path("./keys"))

# Sign a token
sign_token(b"the-token-from-fastboot", key_dir=Path("./keys"))

# Run the unlock flow against one device
unlock_device(dry_run=False, serial="SERIAL123")
```

> Note: the module imports `liblk`, so make sure the dependency is installed in the environment that imports it.

### Read the JSON-friendly output

For automation, run with `-q` to keep output minimal, and rely on the exit code plus the files written (`signature.bin`, the patched image) rather than parsing log text.

---

## Files Generated

- `private.pem` — your RSA private key, used for signing
- `public.pem` — the public key that gets embedded into the patched LK image
- `signature.bin` — the unlock signature handed to fastboot

These key files are git-ignored on purpose. Don't commit them.

---

## Keeping the Copyright in the Project

The AGPL notice and the TFast Digital Agency attribution live at the top of every source file. To make sure they never get dropped, the repo ships a small task runner and a header helper:

```bash
# Install the task runner (pick whichever works for you)
go install github.com/go-task/task/v3/cmd/task@latest
# or: scoop install task  |  choco install task  |  winget install Task.Task

# Check every source file still carries the copyright header
task check-license

# Insert the header into any file that is missing it
task add-license

# See the full list of tasks
task
```

You can also run the helper directly without the task runner:

```bash
python scripts/license_header.py check
python scripts/license_header.py add
python scripts/license_header.py list
```

The `pre-commit` task refuses to let you commit while any source file is missing the header, so the attribution stays in the solution.

---

## Notes

- The RSA modulus length is worked out from your generated key, so the tool handles any key size (the default is 2048-bit).
- Signatures are re-verified against the public key before they are written to disk.
- Every `fastboot` call is bounded by a 30-second timeout so a stuck device won't hang your script.

---

## License

This project is licensed under the **GNU Affero General Public License v3.0**. See [LICENSE](LICENSE).

Copyright (C) 2026 TFast Digital Agency.

---

## Disclaimer

Provided for research and educational use only. TFast Digital Agency is not responsible for damaged devices, lost data, or any other consequence of using this tool. Proceed at your own risk.

Questions or feedback? Join the Telegram community at [@tfasthub](https://t.me/tfasthub) or open an issue on GitHub.


